"""Import provider-neutral conversation JSON into Breadcrumbs."""

from __future__ import annotations

import argparse
from pathlib import Path

from pydantic import TypeAdapter

from ingestion.models import Conversation
from ingestion.store import connect, initialize_schema, upsert_session

type SessionFile = list[Conversation] | dict[str, list[Conversation]]
_SESSIONS: TypeAdapter[SessionFile] = TypeAdapter(SessionFile)


def load_sessions(path: Path) -> list[Conversation]:
    """Read a session array, or an object with a ``sessions`` array."""

    payload = _SESSIONS.validate_json(path.read_bytes())
    if isinstance(payload, dict):
        if set(payload) != {"sessions"}:
            raise ValueError(f"{path} must contain a session array or an object with 'sessions'")
        return payload["sessions"]
    return payload


def ingest_files(paths: list[Path], database_url: str | None = None) -> int:
    initialize_schema(database_url)
    imported = 0
    with connect(database_url) as connection:
        for path in paths:
            for conversation in load_sessions(path):
                upsert_session(connection, conversation)
                imported += 1
    return imported


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path, help="JSON files to import")
    parser.add_argument(
        "--database-url",
        help="PostgreSQL URL; defaults to the required DATABASE_URL environment variable",
    )
    args = parser.parse_args()
    imported = ingest_files(args.files, args.database_url)
    print(f"Imported {imported} conversation(s) into PostgreSQL")


if __name__ == "__main__":
    main()
