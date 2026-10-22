"""Destructive test setup requires an explicitly named PostgreSQL test database."""

import os

from psycopg.conninfo import conninfo_to_dict

from ingestion.store import connect, initialize_schema


def get_database_url() -> str:
    # Never fall back to the application's DATABASE_URL.
    url = os.getenv("TEST_DATABASE_URL", "")
    if not url.startswith(("postgresql://", "postgres://")):
        raise ValueError("TEST_DATABASE_URL must be an explicit PostgreSQL URL")
    if not conninfo_to_dict(url).get("dbname", "").endswith("_test"):
        raise ValueError("TEST_DATABASE_URL database name must end in _test")
    return url


def reset_database() -> str:
    url = get_database_url()
    initialize_schema(url)
    with connect(url) as connection:
        connection.execute("TRUNCATE chat_sessions, knowledge_items CASCADE")
    return url
