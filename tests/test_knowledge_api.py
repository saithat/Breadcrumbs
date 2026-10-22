import asyncio

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from breadcrumbs import server
from breadcrumbs.contracts import (
    MemoryDiffRequest,
    PeopleRequest,
    RecallRequest,
    SurpriseRequest,
    WriteKnowledgeRequest,
)
from tests.support import QUOTE, TURNS, candidate


def test_mcp_tool_inputs_match_rest_request_models():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name, model in {
        "prepare_memory_diff": MemoryDiffRequest,
        "score_surprise": SurpriseRequest,
        "write_knowledge": WriteKnowledgeRequest,
        "recall_knowledge": RecallRequest,
        "find_people_with_context": PeopleRequest,
    }.items():
        assert set(tools[name].inputSchema["properties"]) == set(model.model_fields), name
    assert tools["recall_knowledge"].annotations.readOnlyHint
    assert not tools["prepare_memory_diff"].annotations.readOnlyHint


@pytest.fixture
def client(monkeypatch, store, add_session):
    monkeypatch.setattr(server, "get_store", lambda: store)
    add_session("source", TURNS, actor="Alex Rivera")
    return TestClient(server.app)


def test_rest_and_mcp_share_capture_approval_recall_and_people(client):
    prepare = {
        "proposition": candidate()["proposition"],
        "rationale": candidate()["rationale"],
        "scope": candidate()["scope"],
        "evidence_quote": QUOTE,
        "source_message_id": "source:1",
        "current_actor": "Alex Rivera",
    }
    prepared = client.post("/knowledge/prepare", json=prepare).json()
    assert prepared == server.prepare_memory_diff(**prepare).model_dump(mode="json")

    samples = {"prior_samples": ["uncertain"] * 3, "posterior_samples": ["believe"] * 3}
    scored = client.post("/knowledge/score", json=samples).json()
    assert scored == server.score_surprise(**samples).model_dump(mode="json")

    draft = {k: v for k, v in prepared["record_template"].items() if v is not None}
    written = client.post(
        "/knowledge", json={"candidate": draft | samples, "approved_by": "Morgan Lee"}
    ).json()
    retried = server.write_knowledge(candidate=draft | samples, approved_by="Morgan Lee")
    assert retried.id == written["id"]

    recall = {"queries": ["canary"], "scope": {"system": "payments"}}
    recalled = client.post("/knowledge/recall", json=recall).json()
    assert recalled == [i.model_dump(mode="json") for i in server.recall_knowledge(**recall)]
    assert recalled[0]["id"] == written["id"]

    who = {"topic": "canary", "scope": {"system": "payments"}}
    found = client.post("/people/context", json=who).json()
    assert found == server.find_people_with_context(**who).model_dump(mode="json")


def test_approval_identity_must_be_separate_in_both_transports(client):
    response = client.post(
        "/knowledge",
        json={"candidate": candidate(approved_by="Morgan Lee"), "approved_by": "Morgan Lee"},
    )
    assert response.status_code == 422
    with pytest.raises(ValidationError):
        server.write_knowledge(candidate=candidate(approved_by="Morgan Lee"), approved_by="M")
