from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ingestion.store import connect

from . import __version__
from .contracts import (
    ApprovedBy,
    BeliefSamples,
    ContextChars,
    ElicitationModel,
    EvidenceLimit,
    EvidenceQuote,
    IncludeSuperseded,
    Kind,
    Kinds,
    KnowledgeItem,
    KnowledgeRecord,
    MemoryDiff,
    MemoryDiffRequest,
    PeopleLimit,
    PeopleRequest,
    PeopleResult,
    Proposition,
    Queries,
    Rationale,
    RecalledKnowledge,
    RecallLimit,
    RecallRequest,
    Replicates,
    Scope,
    ScopeFilter,
    SourceMessageId,
    SourceTurns,
    StrictScope,
    SurpriseRequest,
    Topic,
    WriteKnowledgeRequest,
)
from .store import BreadcrumbsStore
from .surprise import SurpriseMetrics, calculate_surprise


@lru_cache(maxsize=1)
def get_store() -> BreadcrumbsStore:
    """Initialize runtime resources on startup, never while importing tool definitions."""

    return BreadcrumbsStore()


BREADCRUMBS_INSTRUCTIONS = """
Breadcrumbs is the organization's internal memory for decisions, constraints, exceptions,
abandoned approaches, and belief revisions.

READ BEFORE ACTING
- Before related planning or execution, call recall_knowledge with a few queries for the same
  need (exact names, rephrasings, synonyms) and only the scope fields stated in the conversation.
  Quote a phrase to require it. Each result's matches show which queries found it.
- Treat recalled items as scoped organizational evidence, not universal truth. Preserve their
  rationale, conditions, source IDs, and abandonment reasons. If source_drifted is true, re-review
  the evidence before relying on it.
- For questions about who has relevant context, call find_people_with_context. Preserve whether
  each person authored, reviewed, or started the source conversation. These relationships are
  evidence of context, not claims of expertise or organizational role.

CAPTURE DURING THE CONVERSATION
- On every substantive turn, check for an explicit decision, correction, constraint, exception,
  abandoned approach, or belief revision that would change what somebody should believe or do.
- When one appears, call prepare_memory_diff with the relevant exact recent turns and the exact
  quote that supports it. Preparation stores provenance but does not approve knowledge.
- Run the returned elicitation: sample the prior and posterior packets `replicates` times each,
  then call score_surprise. The prior already includes related approved knowledge.
- Present one Memory Diff per candidate and call write_knowledge only after explicit human
  approval. approved_by must come from that approval event or authenticated host identity.
- Do not ask users for database IDs, quotes, model choices, or run IDs. Never fabricate evidence.
- Use supersedes_id for corrections so prior approved memory remains auditable.
""".strip()

mcp = FastMCP(
    "Breadcrumbs — Organizational Knowledge Memory",
    instructions=BREADCRUMBS_INSTRUCTIONS,
    json_response=True,
    stateless_http=True,
    streamable_http_path="/",
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
)
CAPTURE = ToolAnnotations(
    readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False
)


@mcp.tool(annotations=CAPTURE)
def prepare_memory_diff(
    proposition: Proposition,
    rationale: Rationale,
    scope: Scope,
    evidence_quote: EvidenceQuote,
    kind: Kind = "decision",
    source_turns: SourceTurns = None,
    source_message_id: SourceMessageId = None,
    session_title: str | None = None,
    current_actor: str | None = None,
    elicitation_model: ElicitationModel = "claude-sonnet-5",
    replicates: Replicates = 5,
    context_chars: ContextChars = 6000,
) -> MemoryDiff:
    """Prepare a reviewable Memory Diff when a conversation changes future decisions or actions."""

    return get_store().prepare_memory_diff(
        MemoryDiffRequest(
            proposition=proposition,
            rationale=rationale,
            scope=scope,
            evidence_quote=evidence_quote,
            kind=kind,
            source_turns=source_turns,
            source_message_id=source_message_id,
            session_title=session_title,
            current_actor=current_actor,
            elicitation_model=elicitation_model,
            replicates=replicates,
            context_chars=context_chars,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def score_surprise(
    prior_samples: BeliefSamples, posterior_samples: BeliefSamples
) -> SurpriseMetrics:
    """Score belief movement from the elicitation; this is not importance or originality."""

    request = SurpriseRequest(prior_samples=prior_samples, posterior_samples=posterior_samples)
    return calculate_surprise(request.prior_samples, request.posterior_samples)


@mcp.tool()
def write_knowledge(candidate: KnowledgeRecord, approved_by: ApprovedBy) -> KnowledgeItem:
    """Persist one reviewed Memory Diff; never call before explicit human approval."""

    request = WriteKnowledgeRequest(candidate=candidate, approved_by=approved_by)
    return get_store().write_knowledge(request.candidate, request.approved_by)


@mcp.tool(annotations=READ_ONLY)
def recall_knowledge(
    queries: Queries = None,
    scope: ScopeFilter = None,
    kinds: Kinds = None,
    limit: RecallLimit = 10,
    include_superseded: IncludeSuperseded = False,
    strict_scope: StrictScope = False,
) -> list[RecalledKnowledge]:
    """Recall approved knowledge with full-text search, history, and applicability."""

    return get_store().recall_knowledge(
        RecallRequest(
            queries=queries,
            scope=scope,
            kinds=kinds,
            limit=limit,
            include_superseded=include_superseded,
            strict_scope=strict_scope,
        )
    )


@mcp.tool(annotations=READ_ONLY)
def find_people_with_context(
    topic: Topic,
    scope: ScopeFilter = None,
    limit: PeopleLimit = 5,
    evidence_limit: EvidenceLimit = 5,
) -> PeopleResult:
    """Return people with relevant source-linked context and the relationship behind each."""

    return get_store().find_people_with_context(
        PeopleRequest(topic=topic, scope=scope, limit=limit, evidence_limit=evidence_limit)
    )


mcp_http_app = mcp.streamable_http_app()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    get_store()
    async with mcp_http_app.router.lifespan_context(app):
        yield


app = FastAPI(title="Breadcrumbs MCP", version=__version__, lifespan=lifespan)


@app.exception_handler(ValueError)
async def value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.get("/health")
def health() -> dict[str, str]:
    with connect(get_store().database_url) as connection:
        connection.execute("SELECT 1")
    return {"status": "ok", "database": "postgresql", "mcp_endpoint": "/mcp"}


# REST routes take the same request models the MCP tools build.
@app.post("/knowledge/prepare")
def prepare_memory_diff_http(request: MemoryDiffRequest) -> MemoryDiff:
    return get_store().prepare_memory_diff(request)


@app.post("/knowledge/score")
def score_surprise_http(request: SurpriseRequest) -> SurpriseMetrics:
    return calculate_surprise(request.prior_samples, request.posterior_samples)


@app.post("/knowledge")
def write_knowledge_http(request: WriteKnowledgeRequest) -> KnowledgeItem:
    return get_store().write_knowledge(request.candidate, request.approved_by)


@app.post("/knowledge/recall")
def recall_knowledge_http(request: RecallRequest) -> list[RecalledKnowledge]:
    return get_store().recall_knowledge(request)


@app.post("/people/context")
def find_people_with_context_http(request: PeopleRequest) -> PeopleResult:
    return get_store().find_people_with_context(request)


app.mount("/mcp", mcp_http_app)


def main() -> None:
    get_store()
    transport = os.getenv("BREADCRUMBS_TRANSPORT", "stdio")
    if transport == "stdio":
        mcp.run(transport="stdio")
    elif transport == "http":
        import uvicorn

        uvicorn.run(
            "breadcrumbs.server:app",
            host=os.getenv("HOST", "127.0.0.1"),
            port=int(os.getenv("PORT", "8000")),
        )
    else:
        raise SystemExit("BREADCRUMBS_TRANSPORT must be 'stdio' or 'http'")


if __name__ == "__main__":
    main()
