"""Deterministic, provenance-backed session identity candidate extraction."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ingestion.store import connect

from .embeddings import utc_now
from .people import clean_person_name, normalize_person_name, provisional_person_id

NON_PERSON_NAMES = frozenset(
    {
        "ai agent",
        "ai-agent",
        "assistant",
        "k pro",
        "model",
        "unknown",
    }
)


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalize_question(value: str) -> str:
    return " ".join(value.split()).casefold()


def is_person_candidate(value: str | None) -> bool:
    if value is None:
        return False
    try:
        return normalize_person_name(value) not in NON_PERSON_NAMES
    except ValueError:
        return False


def backfill_session_identity_candidates(path: str | Path) -> dict[str, int]:
    """Persist graded identity evidence without promoting proposed candidates to owners."""

    connection = connect(path)
    now = utc_now()
    candidate_keys: set[tuple[str, str, str]] = set()
    counts: defaultdict[str, int] = defaultdict(int)

    def upsert_candidate(
        *,
        session_id: str,
        candidate_name: str,
        evidence_type: str,
        evidence_strength: str,
        status: str,
        evidence: dict[str, Any],
    ) -> None:
        display_name = clean_person_name(candidate_name)
        normalized_name = normalize_person_name(display_name)
        person_id = provisional_person_id(normalized_name)
        evidence_json = canonical_json(evidence)
        evidence_hash = hashlib.sha256(evidence_json.encode("utf-8")).hexdigest()
        candidate_keys.add((session_id, person_id, evidence_type))
        connection.execute(
            """
            INSERT INTO people(
                id, display_name, normalized_name, aliases, org_unit,
                identity_status, created_at, updated_at
            ) VALUES (?, ?, ?, '[]', NULL, 'provisional', ?, ?)
            ON CONFLICT(normalized_name) DO NOTHING
            """,
            (person_id, display_name, normalized_name, now, now),
        )
        connection.execute(
            """
            INSERT INTO session_identity_candidates(
                session_id, person_id, candidate_name, evidence_type,
                evidence_strength, status, evidence, evidence_hash,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, person_id, evidence_type) DO UPDATE SET
                candidate_name = excluded.candidate_name,
                evidence_strength = excluded.evidence_strength,
                status = CASE
                    WHEN excluded.evidence_type = 'session_researcher' THEN 'accepted'
                    ELSE session_identity_candidates.status
                END,
                evidence = excluded.evidence,
                evidence_hash = excluded.evidence_hash,
                updated_at = excluded.updated_at
            WHERE session_identity_candidates.candidate_name != excluded.candidate_name
                OR session_identity_candidates.evidence_strength != excluded.evidence_strength
                OR session_identity_candidates.evidence_hash != excluded.evidence_hash
                OR (
                    excluded.evidence_type = 'session_researcher'
                    AND session_identity_candidates.status != 'accepted'
                )
            """,
            (
                session_id,
                person_id,
                display_name,
                evidence_type,
                evidence_strength,
                status,
                evidence_json,
                evidence_hash,
                now,
                now,
            ),
        )
        counts[evidence_type] += 1

    try:
        sessions = connection.execute(
            """
            SELECT
                s.id,
                s.researcher,
                m.id AS first_user_message_id,
                m.content AS first_user_question
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON m.id = (
                SELECT first_user.id
                FROM chat_messages first_user
                WHERE first_user.session_id = s.id AND first_user.role = 'user'
                ORDER BY first_user.seq
                LIMIT 1
            )
            ORDER BY s.id
            """
        ).fetchall()
        session_by_id = {row["id"]: row for row in sessions}

        with connection:
            for session in sessions:
                if not is_person_candidate(session["researcher"]):
                    continue
                upsert_candidate(
                    session_id=session["id"],
                    candidate_name=session["researcher"],
                    evidence_type="session_researcher",
                    evidence_strength="confirmed",
                    status="accepted",
                    evidence={
                        "field": "chat_sessions.researcher",
                        "session_id": session["id"],
                    },
                )

            artifact_sources = (
                (
                    "finding_author",
                    connection.execute(
                        "SELECT source_session_id, author, id FROM findings "
                        "WHERE source_session_id IS NOT NULL ORDER BY source_session_id, author, id"
                    ).fetchall(),
                ),
                (
                    "knowledge_author",
                    connection.execute(
                        "SELECT source_session_id, author, id FROM knowledge_items "
                        "WHERE source_session_id IS NOT NULL ORDER BY source_session_id, author, id"
                    ).fetchall(),
                ),
            )
            for evidence_type, rows in artifact_sources:
                grouped: defaultdict[tuple[str, str], list[str]] = defaultdict(list)
                display_names: dict[tuple[str, str], str] = {}
                for row in rows:
                    if row["source_session_id"] not in session_by_id:
                        continue
                    if not is_person_candidate(row["author"]):
                        continue
                    display_name = clean_person_name(row["author"])
                    key = (row["source_session_id"], normalize_person_name(display_name))
                    grouped[key].append(row["id"])
                    display_names[key] = display_name
                for (session_id, normalized_name), artifact_ids in sorted(grouped.items()):
                    upsert_candidate(
                        session_id=session_id,
                        candidate_name=display_names[(session_id, normalized_name)],
                        evidence_type=evidence_type,
                        evidence_strength="supporting",
                        status="proposed",
                        evidence={
                            "artifact_ids": sorted(artifact_ids),
                            "relationship": evidence_type,
                            "session_id": session_id,
                        },
                    )

            named_by_question: defaultdict[str, list[Any]] = defaultdict(list)
            for session in sessions:
                if (
                    is_person_candidate(session["researcher"])
                    and session["first_user_question"]
                ):
                    named_by_question[
                        normalize_question(session["first_user_question"])
                    ].append(session)

            exact_matches: defaultdict[tuple[str, str], list[Any]] = defaultdict(list)
            target_messages: dict[str, str] = {}
            for target in sessions:
                if is_person_candidate(target["researcher"]) or not target[
                    "first_user_question"
                ]:
                    continue
                normalized_question = normalize_question(target["first_user_question"])
                for source in named_by_question.get(normalized_question, []):
                    normalized_name = normalize_person_name(source["researcher"])
                    exact_matches[(target["id"], normalized_name)].append(source)
                    target_messages[target["id"]] = target["first_user_message_id"]
            for (target_session_id, normalized_name), sources in sorted(
                exact_matches.items()
            ):
                question = normalize_question(sources[0]["first_user_question"])
                upsert_candidate(
                    session_id=target_session_id,
                    candidate_name=clean_person_name(sources[0]["researcher"]),
                    evidence_type="exact_question_match",
                    evidence_strength="weak",
                    status="proposed",
                    evidence={
                        "matched_question_hash": hashlib.sha256(
                            question.encode("utf-8")
                        ).hexdigest(),
                        "source_message_ids": sorted(
                            source["first_user_message_id"] for source in sources
                        ),
                        "source_session_ids": sorted(source["id"] for source in sources),
                        "target_message_id": target_messages[target_session_id],
                    },
                )

            if candidate_keys:
                placeholders = ",".join("(?,?,?)" for _ in candidate_keys)
                parameters = [value for key in sorted(candidate_keys) for value in key]
                connection.execute(
                    "DELETE FROM session_identity_candidates "
                    "WHERE status = 'proposed' AND (session_id, person_id, evidence_type) "
                    f"NOT IN ({placeholders})",
                    parameters,
                )
            else:
                connection.execute(
                    "DELETE FROM session_identity_candidates WHERE status = 'proposed'"
                )
    finally:
        connection.close()

    return dict(sorted(counts.items()))


__all__ = [
    "NON_PERSON_NAMES",
    "backfill_session_identity_candidates",
    "canonical_json",
    "is_person_candidate",
    "normalize_question",
]
