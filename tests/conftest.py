import os

import pytest

from breadcrumbs.store import BreadcrumbsStore
from ingestion.models import Conversation
from ingestion.store import connect, upsert_session
from tests.postgres import reset_database


@pytest.fixture
def database_url():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL to a dedicated PostgreSQL database ending in _test")
    return reset_database()


@pytest.fixture
def db(database_url):
    with connect(database_url) as connection:
        yield connection


@pytest.fixture
def add_session(db):
    def add(session_id, turns, **changes):
        conversation = {
            "id": session_id,
            "source_uri": f"agent://{session_id}",
            "title": session_id,
            "messages": [{"role": role, "content": content} for role, content in turns],
        }
        upsert_session(db, Conversation.model_validate(conversation | changes))

    return add


@pytest.fixture
def store(database_url):
    return BreadcrumbsStore(database_url)
