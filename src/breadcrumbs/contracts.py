"""Typed, UI-compatible contracts for the Breadcrumbs MCP surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import models_json_schema

FindingStatus = Literal["confirmed", "in_progress", "abandoned"]
StorageStatus = Literal["confirmed", "in-progress", "abandoned", "open"]
Relationship = Literal["duplicate_of", "extends", "related", "contradicts"]
DuplicationVerdict = Literal["match", "open"]
KnowledgeKind = Literal["decision", "constraint", "exception", "abandoned", "belief_revision"]
BeliefLabel = Literal[
    "strongly_disbelieve",
    "disbelieve",
    "uncertain",
    "believe",
    "strongly_believe",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Match(ContractModel):
    id: str
    status: FindingStatus
    relationship: Literal["duplicate_of", "extends", "related"]
    hypothesis_text: str
    effect: str
    reason: str | None
    author: str
    disease: str


class CheckDuplicationInput(ContractModel):
    hypothesis_text: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)


class DuplicationResult(ContractModel):
    verdict: DuplicationVerdict
    matches: list[Match]
    searched: int = Field(ge=0)
    markdown: str | None = None


class Resource(ContractModel):
    type: Literal["paper", "database"]
    citation: str = Field(min_length=1)
    url: str | None = None


class WriteFindingInput(ContractModel):
    id: str | None = None
    category: str
    disease: str
    hypothesis_text: str
    entities: list[str]
    effect: str
    status: StorageStatus
    author: str
    source_session_id: str
    source_type: Literal["internal", "external"]
    created_at: str | None = None
    signature: str | None = None
    n: int | None = None
    provenance: str | None = None
    reason: str | None = None
    note: str | None = None
    markdown: str | None = None
    resources: list[Resource] | None = None


class RecallFindingsInput(ContractModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)


class FindingRelationship(ContractModel):
    finding_id: str
    relationship: Relationship


class RecallFinding(ContractModel):
    id: str
    disease: str
    hypothesis_text: str
    signature: str | None = None
    effect: str | None = None
    n: int | None = None
    status: StorageStatus
    author: str
    timestamp: str
    provenance: str | None = None
    reason: str | None = None
    note: str | None = None
    category: str | None = None
    entities: list[str]
    source_session_id: str | None = None
    source_type: Literal["internal", "external"] | None = None
    markdown: str | None = None
    resources: list[Resource]
    relationships: list[FindingRelationship] = Field(default_factory=list)
    score: float = Field(ge=0)


class RecallFindingsResult(ContractModel):
    query: str
    findings: list[RecallFinding]
    searched: int = Field(ge=0)
    sources_searched: list[str]


class RenderWikiInput(ContractModel):
    finding_ids: list[str] | None = None
    title: str = "Breadcrumbs research memory"


class RenderWikiResult(ContractModel):
    markdown: str
    finding_ids: list[str]


class LiveInteractionTurn(ContractModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50000)
    created_at: str | None = None


class MemoryDiffPreparationInput(ContractModel):
    proposition: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    scope: dict[str, Any]
    kind: KnowledgeKind = "decision"
    evidence_query: str | None = None
    source_session_id: str | None = None
    live_context: list[LiveInteractionTurn] | None = Field(
        default=None, min_length=1, max_length=12
    )
    live_session_title: str | None = Field(default=None, max_length=240)
    current_actor: str | None = None
    elicitation_model: str = "claude-sonnet-5"
    replicates: int = Field(default=5, ge=3, le=20)
    context_chars: int = Field(default=6000, ge=1000, le=20000)
    candidate_limit: int = Field(default=3, ge=1, le=5)
    candidate_rank: int = Field(default=1, ge=1, le=5)


class EvidenceCandidate(ContractModel):
    rank: int = Field(ge=1)
    source_message_id: str
    source_session_id: str
    source_message_hash: str
    message_seq: int
    role: Literal["user", "assistant"]
    evidence_quote: str
    quote_start: int = Field(ge=0)
    quote_end: int = Field(ge=1)
    relevance_score: float = Field(ge=0)
    session_title: str | None = None
    source_researcher: str | None = None


class ElicitationPacket(ContractModel):
    phase: Literal["prior", "posterior"]
    proposition: str
    context: str
    context_truncated: bool
    instruction: str
    allowed_labels: list[BeliefLabel]


class ElicitationProtocol(ContractModel):
    model: str
    run_id: str
    replicates: int = Field(ge=3, le=20)
    scoring_method: Literal["beta_fractional_jsd_v1"]
    prior: ElicitationPacket
    posterior: ElicitationPacket


class MemoryDiffPreparationResult(ContractModel):
    draft_id: str
    source_origin: Literal["stored_interaction", "captured_live_context"]
    captured_turn_count: int = Field(ge=0)
    selected_evidence: EvidenceCandidate
    evidence_candidates: list[EvidenceCandidate]
    elicitation: ElicitationProtocol
    author_hint: str | None
    author_hint_source: Literal["authenticated_actor", "unavailable"]
    record_template: dict[str, Any]
    missing_record_fields: list[str]
    selection_warning: str


CONTRACT_MODELS = (
    CheckDuplicationInput,
    DuplicationResult,
    WriteFindingInput,
    RecallFindingsInput,
    RecallFindingsResult,
    RenderWikiInput,
    RenderWikiResult,
    MemoryDiffPreparationInput,
    MemoryDiffPreparationResult,
)


def contract_schema() -> dict:
    """Return one checked schema bundle for every public MCP model."""

    _, schema = models_json_schema(
        [(model, "validation") for model in CONTRACT_MODELS],
        title="Breadcrumbs MCP contracts",
    )
    return schema


def write_contract_schema(path: str | Path) -> Path:
    import json

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(contract_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
