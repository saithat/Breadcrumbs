"""People with source-linked context, derived from approved knowledge and session owners.

Relationships describe evidence (authored, reviewed, or started a relevant conversation). They do
not assert expertise or organizational role.
"""

from __future__ import annotations

import hashlib
import unicodedata
from collections.abc import Sequence

from ingestion.store import Connection

from .contracts import (
    ConversationEvidence,
    KnowledgeEvidence,
    PeopleRequest,
    PeopleResult,
    PersonContext,
    RecalledKnowledge,
    Relationship,
)
from .knowledge import ANY_TERM_TSQUERY

NON_PERSON_NAMES = frozenset({"ai agent", "ai-agent", "assistant", "model", "unknown"})
_RELATIONSHIP_ORDER: dict[Relationship, int] = {
    "knowledge_author": 0,
    "knowledge_reviewer": 1,
    "session_actor": 2,
}


def clean_person_name(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def person_id(name: str) -> str | None:
    """Stable provisional ID from the normalized name, or None for agents and unknown names."""

    normalized = clean_person_name(name).casefold()
    if not normalized or normalized in NON_PERSON_NAMES:
        return None
    return "P-" + hashlib.sha256(normalized.encode()).hexdigest()[:12].upper()


def find_people(
    connection: Connection,
    request: PeopleRequest,
    knowledge: Sequence[RecalledKnowledge],
) -> PeopleResult:
    names: dict[str, str] = {}
    evidence: dict[str, list[KnowledgeEvidence | ConversationEvidence]] = {}

    def add(name: str, item: KnowledgeEvidence | ConversationEvidence) -> None:
        if (pid := person_id(name)) is not None:
            names.setdefault(pid, clean_person_name(name))
            evidence.setdefault(pid, []).append(item)

    for item in knowledge:
        for relationship, name in (
            ("knowledge_author", item.author),
            ("knowledge_reviewer", item.approved_by),
        ):
            add(
                name,
                KnowledgeEvidence(
                    relationship=relationship,  # type: ignore[arg-type]
                    knowledge_id=item.id,
                    kind=item.kind,
                    summary=item.proposition,
                    rationale=item.rationale,
                    reason=item.reason,
                    scope=item.scope,
                    conditions=item.conditions,
                    scope_compatibility=item.scope_compatibility,
                    source_session_id=item.source_session_id,
                    source_message_id=item.source_message_id,
                    source_message_hash=item.source_message_hash,
                    evidence_quote=item.evidence_quote,
                    source_drifted=item.source_drifted,
                    relevance=min(1.0, item.retrieval_score),
                    created_at=item.created_at,
                ),
            )

    sessions = connection.execute(
        f"""
        WITH first_question AS (
            SELECT DISTINCT ON (session_id) session_id, id, content, created_at
            FROM chat_messages WHERE role = 'user' ORDER BY session_id, seq
        )
        SELECT s.id, s.actor, q.id AS message_id, q.content,
               COALESCE(q.created_at, s.captured_at) AS created_at,
               ts_rank_cd(to_tsvector('english', q.content), t.query, 32) AS relevance
        FROM chat_sessions s
        JOIN first_question q ON q.session_id = s.id
        CROSS JOIN {ANY_TERM_TSQUERY} t
        WHERE s.actor IS NOT NULL AND to_tsvector('english', q.content) @@ t.query
        ORDER BY s.id
        """,
        {"query": request.topic},
    ).fetchall()
    for row in sessions:
        add(
            row["actor"],
            ConversationEvidence(
                session_id=row["id"],
                topic_message_id=row["message_id"],
                topic=row["content"],
                relevance=round(row["relevance"], 6),
                created_at=row["created_at"],
            ),
        )

    people: list[PersonContext] = []
    for pid, items in evidence.items():
        items.sort(key=lambda e: (-e.relevance, _RELATIONSHIP_ORDER[e.relationship]))
        relationships = sorted({e.relationship for e in items}, key=_RELATIONSHIP_ORDER.__getitem__)
        people.append(
            PersonContext(
                person_id=pid,
                display_name=names[pid],
                relationships=relationships,
                best_relevance=items[0].relevance,
                approved_knowledge_count=len(
                    {e.knowledge_id for e in items if isinstance(e, KnowledgeEvidence)}
                ),
                conversation_count=len(
                    {
                        e.source_session_id if isinstance(e, KnowledgeEvidence) else e.session_id
                        for e in items
                    }
                ),
                evidence_count=len(items),
                evidence=items[: request.evidence_limit],
            )
        )
    people.sort(
        key=lambda p: (
            -p.best_relevance,
            _RELATIONSHIP_ORDER[p.relationships[0]],
            -p.approved_knowledge_count,
            p.display_name.casefold(),
        )
    )
    people = people[: request.limit]
    message = (
        f"Found {len(people)} people with source-linked context for {request.topic}. "
        "Relationships describe evidence, not expertise."
        if people
        else f"No people with source-linked context for {request.topic} were found."
    )
    return PeopleResult(topic=request.topic, scope=request.scope, message=message, people=people)
