"""PostgreSQL persistence for conversation provenance."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .models import Conversation

SCHEMA_PATH = Path(__file__).resolve().with_name("graph_schema.sql")
if not SCHEMA_PATH.is_file():
    SCHEMA_PATH = Path(__file__).resolve().parents[1] / "schema" / "graph_schema.sql"
DATABASE_URL_ENV = "DATABASE_URL"

type Connection = psycopg.Connection[dict[str, Any]]


def utc_now() -> datetime:
    return datetime.now(UTC)


def resolve_database_url(database_url: str | None = None) -> str:
    """Resolve the required PostgreSQL DSN without providing a local-file fallback."""

    value = (database_url if database_url is not None else os.getenv(DATABASE_URL_ENV, "")).strip()
    if not value:
        raise RuntimeError(
            "DATABASE_URL is required and must point to PostgreSQL, for example "
            "postgresql://breadcrumbs:breadcrumbs@127.0.0.1:5432/breadcrumbs"
        )
    if not value.casefold().startswith(("postgresql://", "postgres://")):
        raise RuntimeError("DATABASE_URL must use a postgresql:// or postgres:// URL")
    return value


def connect(database_url: str | None = None) -> Connection:
    """Open an autocommit connection; callers wrap related writes in ``transaction()``."""

    return psycopg.connect(
        resolve_database_url(database_url), autocommit=True, row_factory=dict_row
    )


def initialize_schema(database_url: str | None = None) -> None:
    """Install the idempotent PostgreSQL schema once during process or job startup."""

    with connect(database_url) as connection:
        connection.execute(SCHEMA_PATH.read_text(encoding="utf-8"))


def ingested_revisions(connection: Connection) -> dict[str, str | None]:
    """Return the stored source revision for each conversation."""

    return {
        row["id"]: row["revision"]
        for row in connection.execute("SELECT id, revision FROM chat_sessions")
    }


def upsert_session(connection: Connection, conversation: Conversation) -> None:
    """Replace one conversation atomically while retaining stable source-message IDs."""

    with connection.transaction():
        connection.execute(
            """
            INSERT INTO chat_sessions(id, source_uri, title, captured_at, revision, actor, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(id) DO UPDATE SET
                source_uri = excluded.source_uri,
                title = excluded.title,
                captured_at = excluded.captured_at,
                revision = excluded.revision,
                actor = COALESCE(excluded.actor, chat_sessions.actor),
                metadata = excluded.metadata
            """,
            (
                conversation.id,
                conversation.source_uri,
                conversation.title,
                utc_now(),
                conversation.revision,
                conversation.actor,
                Jsonb(conversation.metadata),
            ),
        )
        connection.execute("DELETE FROM chat_messages WHERE session_id = %s", (conversation.id,))
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO chat_messages(id, session_id, seq, role, content, created_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                """,
                [
                    (
                        f"{conversation.id}:{seq}",
                        conversation.id,
                        seq,
                        turn.role,
                        turn.content,
                        turn.created_at,
                    )
                    for seq, turn in conversation.numbered_messages()
                ],
            )
