import asyncio

import pytest
from fastapi.testclient import TestClient
from mcp.server.fastmcp.exceptions import ToolError
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

LOCAL = "http://127.0.0.1"
ROUTES = {
    "prepare_memory_diff": ("/knowledge/prepare", MemoryDiffRequest),
    "score_surprise": ("/knowledge/score", SurpriseRequest),
    "write_knowledge": ("/knowledge", WriteKnowledgeRequest),
    "recall_knowledge": ("/knowledge/recall", RecallRequest),
    "find_people_with_context": ("/people/context", PeopleRequest),
}
SAMPLES = {"prior_samples": ["uncertain"] * 3, "posterior_samples": ["believe"] * 3}


def _schema(value):
    if isinstance(value, dict):
        return {k: _schema(v) for k, v in value.items() if k not in {"title"}}
    return [_schema(v) for v in value] if isinstance(value, list) else value


def test_mcp_tool_inputs_match_rest_request_models():
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name, (_, model) in ROUTES.items():
        # FastMCP ignores unknown top-level arguments, so only REST forbids additionalProperties.
        rest = _schema(model.model_json_schema())
        assert rest.pop("additionalProperties") is False
        assert _schema(tools[name].inputSchema) == rest, name
    assert tools["recall_knowledge"].annotations.readOnlyHint
    assert not tools["prepare_memory_diff"].annotations.readOnlyHint


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("score_surprise", {"prior_samples": [True] * 3, "posterior_samples": ["believe"] * 3}),
        ("score_surprise", {"prior_samples": ["0.5"] * 3, "posterior_samples": ["believe"] * 3}),
        ("recall_knowledge", {"limit": "10"}),
        ("recall_knowledge", {"limit": 10.0}),
        ("recall_knowledge", {"include_superseded": "true"}),
        ("find_people_with_context", {"topic": "canary", "evidence_limit": "5"}),
        ("write_knowledge", {"candidate": candidate(), "approved_by": "assistant"}),
    ],
)
def test_both_transports_reject_the_same_loose_inputs(tool, arguments):
    path, _ = ROUTES[tool]
    assert TestClient(server.app, base_url=LOCAL).post(path, json=arguments).status_code == 422
    with pytest.raises(ToolError, match="validation error"):
        asyncio.run(server.mcp.call_tool(tool, arguments))


def test_whole_number_beliefs_are_accepted_by_both_transports():
    samples = {"prior_samples": [0, 1, 0], "posterior_samples": [1, 1, 0.5]}
    rest = TestClient(server.app, base_url=LOCAL).post("/knowledge/score", json=samples)
    assert rest.json() == server.score_surprise(**samples).model_dump(mode="json")


@pytest.mark.parametrize(
    ("headers", "status"),
    [
        ({"host": "attacker.example"}, 421),
        ({"host": "127.0.0.1:8000", "origin": "http://attacker.example"}, 403),
        ({"host": "localhost", "origin": "null"}, 403),
    ],
)
@pytest.mark.parametrize("path", ["/knowledge", "/knowledge/score", "/mcp/"])
def test_http_rejects_dns_rebinding_and_foreign_origins(path, headers, status):
    client = TestClient(server.app, base_url=LOCAL)
    assert client.post(path, json=SAMPLES, headers=headers).status_code == status


def test_http_accepts_loopback_hosts_and_origins():
    client = TestClient(server.app, base_url=LOCAL)
    headers = {"host": "localhost:8000", "origin": "http://localhost:3000"}
    assert client.post("/knowledge/score", json=SAMPLES, headers=headers).status_code == 200


def test_http_requires_the_bearer_token_when_configured():
    guarded = TestClient(server.HttpGuard(server.api, server.LOOPBACK_HOSTS, "s3cret"))
    guarded.base_url = guarded.base_url.copy_with(host="127.0.0.1")
    assert guarded.post("/knowledge/score", json=SAMPLES).status_code == 401
    wrong = {"authorization": "Bearer nope"}
    assert guarded.post("/knowledge/score", json=SAMPLES, headers=wrong).status_code == 401
    right = {"authorization": "Bearer s3cret"}
    assert guarded.post("/knowledge/score", json=SAMPLES, headers=right).status_code == 200


def test_http_refuses_to_expose_a_public_host_without_a_token(monkeypatch):
    monkeypatch.setenv("BREADCRUMBS_TRANSPORT", "http")
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.delenv("BREADCRUMBS_API_TOKEN", raising=False)
    monkeypatch.setattr(server, "get_store", lambda: None)
    with pytest.raises(SystemExit, match="BREADCRUMBS_API_TOKEN"):
        server.main()


@pytest.fixture
def client(monkeypatch, store, add_session):
    monkeypatch.setattr(server, "get_store", lambda: store)
    add_session("source", TURNS, actor="Alex Rivera")
    return TestClient(server.app, base_url=LOCAL)


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

    samples = SAMPLES
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
