"""Capture, approval, and recall over PostgreSQL; the facade behind MCP and REST."""

from __future__ import annotations

import hashlib
from typing import Any

from psycopg import sql
from psycopg.types.json import Jsonb

from ingestion.models import Turn
from ingestion.store import Connection, connect, initialize_schema, resolve_database_url, utc_now

from .contracts import (
    Elicitation,
    EvidenceSource,
    KnowledgeDraft,
    KnowledgeItem,
    KnowledgeRecord,
    MemoryDiff,
    MemoryDiffRequest,
    PeopleRequest,
    PeopleResult,
    PriorKnowledge,
    QueryMatch,
    RecalledKnowledge,
    RecallRequest,
)
from .elicitation import SourceMessage, build_packets
from .knowledge import (
    SEARCH_SQL,
    json_text,
    quoted_phrases,
    scope_compatibility,
    search_text,
)
from .people import find_people
from .surprise import SCORING_METHOD, calculate_surprise

# Related approved items shown to the prior; the paper's best-performing top-k.
PRIOR_KNOWLEDGE_LIMIT = 25
RRF_K = 60
_JSON_FIELDS = frozenset({"scope", "aliases", "conditions", "prior_samples", "posterior_samples"})
_DERIVED_ON_READ = frozenset({"source_drifted", "is_superseded"})
_ITEM_SELECT = sql.SQL(
    "SELECT {columns}, "
    "encode(sha256(convert_to(m.content, 'UTF8')), 'hex') <> k.source_message_hash "
    "AS source_drifted, "
    "EXISTS(SELECT 1 FROM knowledge_items n WHERE n.supersedes_id = k.id) AS is_superseded "
    "FROM knowledge_items k JOIN chat_messages m ON m.id = k.source_message_id "
    "WHERE k.id = ANY(%s)"
).format(
    columns=sql.SQL(", ").join(
        sql.Identifier("k", name)
        for name in KnowledgeItem.model_fields
        if name not in _DERIVED_ON_READ
    )
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _messages(connection: Connection, session_id: str) -> list[SourceMessage]:
    rows = connection.execute(
        "SELECT id, seq, role, content FROM chat_messages WHERE session_id = %s ORDER BY seq",
        (session_id,),
    ).fetchall()
    return [SourceMessage(**row) for row in rows]


def _items(connection: Connection, ids: list[str]) -> dict[str, KnowledgeItem]:
    rows = connection.execute(_ITEM_SELECT, (ids,)).fetchall()
    return {row["id"]: KnowledgeItem.model_validate(row) for row in rows}


class BreadcrumbsStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = resolve_database_url(database_url)
        initialize_schema(self.database_url)

    def _connect(self) -> Connection:
        return connect(self.database_url)

    def _capture_turns(
        self, connection: Connection, turns: list[Turn], actor: str | None, title: str | None
    ) -> str:
        """Store an idempotent, content-addressed snapshot of host-supplied turns."""

        payload = {"actor": actor, "turns": [turn.model_dump(mode="json") for turn in turns]}
        digest = _sha256(json_text(payload))
        session_id = "LIVE-" + digest[:20].upper()
        with connection.transaction():
            connection.execute(
                """
                INSERT INTO chat_sessions(id, source_uri, title, captured_at, actor, metadata)
                VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                """,
                (
                    session_id,
                    f"mcp://breadcrumbs/live/{session_id}",
                    title or "Live interaction evidence",
                    utc_now(),
                    actor,
                    Jsonb({"source": "mcp_live_context", "content_sha256": digest}),
                ),
            )
            with connection.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO chat_messages(id, session_id, seq, role, content, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING
                    """,
                    [
                        (f"{session_id}:{seq}", session_id, seq, t.role, t.content, t.created_at)
                        for seq, t in enumerate(turns)
                    ],
                )
        return session_id

    def prepare_memory_diff(self, request: MemoryDiffRequest) -> MemoryDiff:
        """Resolve exact evidence and build evidence-informed elicitation packets.

        Nothing here approves or persists knowledge; only source turns are stored.
        """

        quote = request.evidence_quote
        with self._connect() as connection:
            if request.source_turns is not None:
                session_id = self._capture_turns(
                    connection, request.source_turns, request.current_actor, request.session_title
                )
                messages = _messages(connection, session_id)
                matches = [message for message in messages if quote in message.content]
                if not matches:
                    raise ValueError("evidence_quote must occur verbatim in one of source_turns")
                source = matches[-1]
            else:
                row = connection.execute(
                    "SELECT session_id FROM chat_messages WHERE id = %s",
                    (request.source_message_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"Unknown source message: {request.source_message_id}")
                session_id = row["session_id"]
                messages = _messages(connection, session_id)
                source = next(m for m in messages if m.id == request.source_message_id)
                if quote not in source.content:
                    raise ValueError("evidence_quote must occur verbatim in the source message")

        related = self.recall_knowledge(
            RecallRequest(
                queries=[request.proposition], scope=request.scope, limit=PRIOR_KNOWLEDGE_LIMIT
            )
        )
        prior_knowledge = [
            PriorKnowledge.model_validate(item, from_attributes=True)
            for item in related
            if not item.scope_compatibility.incompatible
        ]
        start = source.content.index(quote)
        prior, posterior = build_packets(
            messages,
            source,
            start,
            start + len(quote),
            request.proposition,
            prior_knowledge,
            context_chars=request.context_chars,
        )
        run_payload = {
            "model": request.elicitation_model,
            "replicates": request.replicates,
            "prior": prior.model_dump(),
            "posterior": posterior.model_dump(),
        }
        run_id = "ELIC-" + _sha256(json_text(run_payload))[:16].upper()
        missing = ["prior_samples", "posterior_samples"]
        if request.current_actor is None:
            missing.append("author")
        if request.kind == "abandoned":
            missing.append("reason")
        return MemoryDiff(
            source=EvidenceSource(
                source_session_id=session_id,
                source_message_id=source.id,
                source_message_hash=_sha256(source.content),
                role=source.role,
                evidence_quote=quote,
                quote_start=start,
                quote_end=start + len(quote),
            ),
            prior_knowledge=prior_knowledge,
            elicitation=Elicitation(
                model=request.elicitation_model,
                run_id=run_id,
                replicates=request.replicates,
                scoring_method=SCORING_METHOD,
                prior=prior,
                posterior=posterior,
            ),
            record_template=KnowledgeDraft(
                kind=request.kind,
                proposition=request.proposition,
                rationale=request.rationale,
                scope=request.scope,
                evidence_quote=quote,
                source_message_id=source.id,
                elicitation_model=request.elicitation_model,
                elicitation_run_id=run_id,
                author=request.current_actor,
            ),
            missing_record_fields=missing,
        )

    def write_knowledge(self, record: KnowledgeRecord, approved_by: str) -> KnowledgeItem:
        """Persist one explicitly approved, source-linked knowledge item.

        Retries are idempotent, but a retry cannot change reviewed fields; corrections append a
        new item through ``supersedes_id``.
        """

        key = "\x1f".join((record.source_message_id, record.kind, record.proposition))
        item_id = "K-" + _sha256(key)[:12].upper()
        reviewed = record.model_dump(mode="json") | {"approved_by": approved_by}
        metrics = calculate_surprise(record.prior_samples, record.posterior_samples)

        # Keep the source locked from quote validation through the write. Later re-imports may
        # revise it; recall then reports source drift.
        with self._connect() as connection, connection.transaction():
            source = connection.execute(
                "SELECT session_id, content FROM chat_messages WHERE id = %s FOR SHARE",
                (record.source_message_id,),
            ).fetchone()
            if source is None:
                raise ValueError(f"Unknown source message: {record.source_message_id}")
            if record.evidence_quote not in source["content"]:
                raise ValueError("evidence_quote must occur verbatim in the source message")
            if record.supersedes_id is not None:
                if record.supersedes_id == item_id:
                    raise ValueError("a knowledge item cannot supersede itself")
                if not _items(connection, [record.supersedes_id]):
                    raise ValueError(f"Unknown superseded knowledge item: {record.supersedes_id}")

            values: dict[str, Any] = {
                **reviewed,
                **metrics.model_dump(),
                "id": item_id,
                "source_session_id": source["session_id"],
                "source_message_hash": _sha256(source["content"]),
                "search_text": search_text(record),
                "created_at": utc_now(),
            }
            for field in _JSON_FIELDS:
                values[field] = Jsonb(values[field])
            connection.execute(
                sql.SQL(
                    "INSERT INTO knowledge_items ({}) VALUES ({}) ON CONFLICT DO NOTHING"
                ).format(
                    sql.SQL(", ").join(map(sql.Identifier, values)),
                    sql.SQL(", ").join(map(sql.Placeholder, values)),
                ),
                values,
            )
            stored = _items(connection, [item_id]).get(item_id)
            if stored is None:
                if record.supersedes_id is None:
                    raise RuntimeError(f"knowledge item {item_id} was not written")
                raise ValueError(
                    f"knowledge item {record.supersedes_id} already has a successor; "
                    "supersede the active head instead"
                )
            existing = stored.model_dump(mode="json", include=set(reviewed))
            conflicts = sorted(field for field in reviewed if existing[field] != reviewed[field])
            if conflicts:
                raise ValueError(
                    f"knowledge item {item_id} already exists with different reviewed field(s): "
                    + ", ".join(conflicts)
                    + "; create a new source-linked superseding item"
                )
        return stored

    def _search(self, connection: Connection, query: str, limit: int) -> dict[str, float]:
        """Full-text hits for one query, best first."""

        params = {"query": query, "phrases": quoted_phrases(query), "limit": limit}
        return {row["id"]: row["score"] for row in connection.execute(SEARCH_SQL, params)}

    def recall_knowledge(self, request: RecallRequest) -> list[RecalledKnowledge]:
        """Full-text recall per query, fused with RRF, resolved to active heads, and scored for
        scope."""

        requested_scope = request.scope or {}
        queries = list(dict.fromkeys(request.queries or []))
        pool = max(100, request.limit * 10)
        with self._connect() as connection:
            searches = {query: self._search(connection, query, pool) for query in queries}
            if queries:
                hits = list(
                    dict.fromkeys(item_id for found in searches.values() for item_id in found)
                )
            else:
                hits = [
                    row["id"]
                    for row in connection.execute(
                        "SELECT id FROM knowledge_items ORDER BY created_at DESC LIMIT %s", (pool,)
                    )
                ]
            if not hits:
                return []
            if request.include_superseded:
                head_of = {hit: hit for hit in hits}
            else:
                head_of = {
                    row["origin"]: row["id"]
                    for row in connection.execute(
                        """
                        WITH RECURSIVE chain(origin, id) AS (
                            SELECT id, id FROM knowledge_items WHERE id = ANY(%s)
                            UNION ALL
                            SELECT chain.origin, k.id
                            FROM chain JOIN knowledge_items k ON k.supersedes_id = chain.id
                        )
                        SELECT origin, id FROM chain WHERE NOT EXISTS (
                            SELECT 1 FROM knowledge_items n WHERE n.supersedes_id = chain.id
                        )
                        """,
                        (hits,),
                    )
                }
            items = _items(connection, list(set(head_of.values())))

        ranks = {
            query: {item_id: rank for rank, item_id in enumerate(found, 1)}
            for query, found in searches.items()
        }
        results: dict[str, RecalledKnowledge] = {}
        for hit in hits:
            head = items[head_of[hit]]
            if request.kinds and head.kind not in request.kinds:
                continue
            compatibility = scope_compatibility(head.scope, requested_scope, head.conditions)
            if request.strict_scope and (compatibility.incompatible or compatibility.unknown):
                continue
            matches: list[QueryMatch] = []
            reciprocal = 0.0
            for query, found in searches.items():
                rank = ranks[query].get(hit)
                if rank is None:
                    continue
                reciprocal += 1.0 / (RRF_K + rank)
                matches.append(QueryMatch(query=query, rank=rank, score=round(found[hit], 6)))
            # 1.0 means the item ranked first for every query.
            fusion = reciprocal * (RRF_K + 1) / len(queries) if queries else 0.0
            score = round(0.9 * fusion + 0.1 * compatibility.score, 6)
            current = results.get(head.id)
            if current is None or score > current.retrieval_score:
                results[head.id] = RecalledKnowledge(
                    **head.model_dump(),
                    retrieval_score=score,
                    matches=matches,
                    scope_compatibility=compatibility,
                    matched_via_history=current.matched_via_history if current else [],
                )
            if hit != head.id and queries:
                results[head.id].matched_via_history = sorted(
                    {*results[head.id].matched_via_history, hit}
                )

        ranked = sorted(
            results.values(),
            key=lambda r: (r.retrieval_score, r.bayesian_surprise_bits, r.created_at),
            reverse=True,
        )
        return ranked[: request.limit]

    def find_people_with_context(self, request: PeopleRequest) -> PeopleResult:
        """People with relevant source-linked context; never an expertise claim."""

        knowledge = self.recall_knowledge(
            RecallRequest(queries=[request.topic], scope=request.scope, limit=100)
        )
        with self._connect() as connection:
            return find_people(connection, request, knowledge)
