import json

import pytest

from ingestion.ingest_conversations import ingest_files
from ingestion.store import ingested_revisions
from tests.postgres import get_database_url


def test_import_is_provenance_only_and_reimport_replaces_one_session(
    database_url, db, tmp_path, add_session
):
    source = tmp_path / "conversations.json"
    session = {
        "id": "a",
        "source_uri": "agent://a",
        "revision": "1",
        "actor": "Alex",
        "metadata": {"provider": "example"},
        "messages": [{"role": "user", "content": "Use a staged rollout."}],
    }
    source.write_text(json.dumps({"sessions": [session]}))
    assert ingest_files([source], database_url) == 1
    stored = db.execute("SELECT * FROM chat_sessions").fetchone()
    assert (stored["source_uri"], stored["actor"], stored["metadata"]) == (
        "agent://a",
        "Alex",
        {"provider": "example"},
    )
    assert db.execute("SELECT count(*) AS n FROM knowledge_items").fetchone()["n"] == 0

    add_session("b", [("user", "untouched")], revision="1")
    add_session("a", [("user", "new"), ("assistant", "answer")], revision="2")
    assert (
        db.execute("SELECT actor FROM chat_sessions WHERE id = 'a'").fetchone()["actor"] == "Alex"
    )
    messages = db.execute("SELECT id, content FROM chat_messages ORDER BY id").fetchall()
    assert [(row["id"], row["content"]) for row in messages] == [
        ("a:0", "new"),
        ("a:1", "answer"),
        ("b:0", "untouched"),
    ]
    assert ingested_revisions(db) == {"a": "2", "b": "1"}


def test_test_database_requires_explicit_dedicated_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://host/production")
    for url in (
        "",
        "sqlite:///test",
        "postgresql://host/production",
        "postgresql://host/safe_test?dbname=production",
    ):
        monkeypatch.setenv("TEST_DATABASE_URL", url)
        with pytest.raises(ValueError, match="TEST_DATABASE_URL"):
            get_database_url()
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://host/breadcrumbs_test")
    assert get_database_url() == "postgresql://host/breadcrumbs_test"
