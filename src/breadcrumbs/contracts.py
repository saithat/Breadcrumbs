"""Typed contracts shared by the MCP tools and REST routes.

Field aliases below carry their constraints and descriptions so the MCP tool signatures and the
request models validate identically.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, Field, JsonValue, model_validator
from pydantic.json_schema import models_json_schema

from ingestion.models import JsonObject, Role, StrictModel, Text, Turn, VerbatimText

from .surprise import BeliefLabel, SurpriseMetrics, belief_score

KnowledgeKind = Literal["decision", "constraint", "exception", "abandoned", "belief_revision"]
ElicitationModel = Literal["claude-sonnet-5"]
Relationship = Literal["knowledge_author", "knowledge_reviewer", "session_actor"]


def _belief_sample(value: float | str) -> float | str:
    belief_score(value)
    return value


def _distinct_casefold(values: list[str]) -> list[str]:
    distinct: dict[str, str] = {}
    for value in values:
        distinct.setdefault(value.casefold(), value)
    return list(distinct.values())


Names = Annotated[list[Text], AfterValidator(_distinct_casefold)]
Kind = Annotated[
    KnowledgeKind,
    Field(description="decision, constraint, exception, abandoned, or belief_revision."),
]
Proposition = Annotated[
    Text, Field(description="Statement whose belief or action relevance changed.")
]
Rationale = Annotated[Text, Field(description="Why it should change future decisions or actions.")]
Scope = Annotated[
    JsonObject,
    Field(
        min_length=1,
        description=(
            "Non-empty structured applicability scope stated in the conversation, such as "
            "project, system, method, customer, or environment."
        ),
    ),
]
ScopeFilter = Annotated[
    JsonObject | None,
    Field(description="Optional scope subset stated in the conversation; never inferred."),
]
EvidenceQuote = Annotated[
    VerbatimText,
    Field(description="Text copied exactly from one source turn that supports the proposition."),
]
SourceTurns = Annotated[
    list[Turn] | None,
    Field(
        min_length=1,
        max_length=12,
        description=(
            "Relevant recent user/assistant turns copied exactly from the active conversation. "
            "Breadcrumbs stores an idempotent, content-addressed snapshot. Never ask the user to "
            "transcribe or quote them."
        ),
    ),
]
SourceMessageId = Annotated[
    Text | None,
    Field(description="Already-stored source message (<session-id>:<seq>) instead of turns."),
]
BeliefSample = Annotated[float | str, AfterValidator(_belief_sample)]
BeliefSamples = Annotated[
    list[BeliefSample],
    Field(
        min_length=3,
        max_length=50,
        description=(
            "Repeated judgments of the proposition: numbers in [0,1] or allowed belief labels."
        ),
    ),
]
Replicates = Annotated[int, Field(ge=3, le=20, description="Judgments per elicitation packet.")]
ContextChars = Annotated[
    int, Field(ge=1000, le=20000, description="Maximum conversation context per side.")
]
Queries = Annotated[
    list[Text] | None,
    Field(
        max_length=10,
        description=(
            "One or more searches for the same need, e.g. rephrasings, synonyms, or exact names; "
            "results are fused. Quote a phrase to require it. Omit to list the most recent items."
        ),
    ),
]
Kinds = Annotated[list[KnowledgeKind] | None, Field(description="Optional kinds to include.")]
RecallLimit = Annotated[int, Field(ge=1, le=100, description="Maximum results.")]
IncludeSuperseded = Annotated[
    bool, Field(description="Include history superseded by newer approved knowledge.")
]
StrictScope = Annotated[
    bool,
    Field(description="Require every requested scope field to be compatible, not just unknown."),
]
Topic = Annotated[
    Text,
    Field(
        description=(
            "The subject only, e.g. 'canary rollout' rather than 'who knows about canary rollout'."
        )
    ),
]
PeopleLimit = Annotated[int, Field(ge=1, le=20, description="Maximum ranked people.")]
EvidenceLimit = Annotated[int, Field(ge=1, le=20, description="Maximum evidence per person.")]
ApprovedBy = Annotated[
    Text,
    Field(
        description=(
            "Person who explicitly approved this exact Memory Diff, from the approval event or "
            "authenticated host identity."
        )
    ),
]


class Condition(StrictModel):
    field: Text
    field_aliases: Names = Field(default_factory=list)
    operator: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in"]
    value: JsonValue
    unit: Text | None = None

    @model_validator(mode="after")
    def _operand_matches_operator(self) -> Self:
        if self.operator == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("'in' conditions need a non-empty array value")
        elif self.operator in {"lt", "lte", "gt", "gte"}:
            if isinstance(self.value, bool) or not isinstance(self.value, int | float):
                raise ValueError(f"'{self.operator}' conditions need a numeric value")
        elif self.value is None or isinstance(self.value, dict | list):
            raise ValueError(f"'{self.operator}' conditions need a scalar value")
        return self


class _KnowledgeFields(StrictModel):
    kind: Kind
    proposition: Proposition
    rationale: Rationale
    scope: Scope
    aliases: Names = Field(default_factory=list)
    conditions: list[Condition] = Field(default_factory=list)
    reason: Annotated[Text | None, Field(description="Required only for abandoned.")] = None
    evidence_quote: EvidenceQuote
    source_message_id: Text
    elicitation_model: ElicitationModel
    elicitation_run_id: Text
    supersedes_id: Annotated[
        Text | None, Field(description="Approved item this corrects; history stays auditable.")
    ] = None


class KnowledgeDraft(_KnowledgeFields):
    """A prepared record; the host adds samples, author, and any reason before approval."""

    author: Text | None


class KnowledgeRecord(_KnowledgeFields):
    """One reviewed Memory Diff. Calculated fields are derived on write, never supplied."""

    prior_samples: BeliefSamples
    posterior_samples: BeliefSamples
    author: Text

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.kind == "abandoned") != (self.reason is not None):
            raise ValueError("reason is required for abandoned knowledge and allowed only there")
        if len(self.prior_samples) != len(self.posterior_samples):
            raise ValueError("prior and posterior samples must have the same length")
        return self


class KnowledgeItem(KnowledgeRecord):
    id: str
    approved_by: str
    source_session_id: str
    source_message_hash: str
    source_drifted: bool
    is_superseded: bool
    scoring_method: str
    prior_mean: float
    posterior_mean: float
    belief_shift: float
    bayesian_surprise_bits: float
    surprising: bool
    created_at: datetime


class ScopeCompatibility(BaseModel):
    compatible: list[str]
    incompatible: list[str]
    unknown: list[str]
    score: float


class QueryMatch(BaseModel):
    """Why one query matched, so the agent can judge results and rephrase."""

    query: str
    rank: int
    score: float


class RecalledKnowledge(KnowledgeItem):
    retrieval_score: float
    matches: list[QueryMatch]
    scope_compatibility: ScopeCompatibility
    matched_via_history: list[str]


class MemoryDiffRequest(StrictModel):
    proposition: Proposition
    rationale: Rationale
    scope: Scope
    evidence_quote: EvidenceQuote
    kind: Kind = "decision"
    source_turns: SourceTurns = None
    source_message_id: SourceMessageId = None
    session_title: Annotated[Text | None, Field(max_length=240)] = None
    current_actor: Annotated[
        Text | None, Field(description="Authenticated host actor; not a conversational input.")
    ] = None
    elicitation_model: ElicitationModel = "claude-sonnet-5"
    replicates: Replicates = 5
    context_chars: ContextChars = 6000

    @model_validator(mode="after")
    def _one_source(self) -> Self:
        if (self.source_turns is None) == (self.source_message_id is None):
            raise ValueError("supply exactly one of source_turns or source_message_id")
        if self.session_title is not None and self.source_turns is None:
            raise ValueError("session_title requires source_turns")
        return self


class EvidenceSource(BaseModel):
    source_session_id: str
    source_message_id: str
    source_message_hash: str
    role: Role
    evidence_quote: str
    quote_start: int
    quote_end: int


class PriorKnowledge(BaseModel):
    id: str
    kind: KnowledgeKind
    proposition: str
    scope: dict[str, JsonValue]
    posterior_mean: float


class ElicitationPacket(BaseModel):
    phase: Literal["prior", "posterior"]
    proposition: str
    context: str
    context_truncated: bool
    instruction: str
    allowed_labels: list[BeliefLabel]


class Elicitation(BaseModel):
    model: ElicitationModel
    run_id: str
    replicates: int
    scoring_method: str
    prior: ElicitationPacket
    posterior: ElicitationPacket


class MemoryDiff(BaseModel):
    source: EvidenceSource
    prior_knowledge: list[PriorKnowledge]
    elicitation: Elicitation
    record_template: KnowledgeDraft
    missing_record_fields: list[str]


class SurpriseRequest(StrictModel):
    prior_samples: BeliefSamples
    posterior_samples: BeliefSamples


class WriteKnowledgeRequest(StrictModel):
    candidate: KnowledgeRecord
    approved_by: ApprovedBy


class RecallRequest(StrictModel):
    queries: Queries = None
    scope: ScopeFilter = None
    kinds: Kinds = None
    limit: RecallLimit = 10
    include_superseded: IncludeSuperseded = False
    strict_scope: StrictScope = False


class PeopleRequest(StrictModel):
    topic: Topic
    scope: ScopeFilter = None
    limit: PeopleLimit = 5
    evidence_limit: EvidenceLimit = 5


class KnowledgeEvidence(BaseModel):
    evidence_type: Literal["approved_knowledge"] = "approved_knowledge"
    relationship: Literal["knowledge_author", "knowledge_reviewer"]
    knowledge_id: str
    kind: KnowledgeKind
    summary: str
    rationale: str
    reason: str | None
    scope: dict[str, JsonValue]
    conditions: list[Condition]
    scope_compatibility: ScopeCompatibility
    source_session_id: str
    source_message_id: str
    source_message_hash: str
    evidence_quote: str
    source_drifted: bool
    relevance: float
    created_at: datetime


class ConversationEvidence(BaseModel):
    evidence_type: Literal["conversation_activity"] = "conversation_activity"
    relationship: Literal["session_actor"] = "session_actor"
    session_id: str
    topic_message_id: str
    topic: str
    relevance: float
    created_at: datetime


class PersonContext(BaseModel):
    person_id: str
    display_name: str
    relationships: list[Relationship]
    best_relevance: float
    approved_knowledge_count: int
    conversation_count: int
    evidence_count: int
    evidence: list[KnowledgeEvidence | ConversationEvidence]


class PeopleResult(BaseModel):
    topic: str
    scope: dict[str, JsonValue] | None
    message: str
    people: list[PersonContext]


PUBLIC_MODELS: tuple[type[BaseModel], ...] = (
    MemoryDiffRequest,
    MemoryDiff,
    SurpriseRequest,
    SurpriseMetrics,
    WriteKnowledgeRequest,
    KnowledgeItem,
    RecallRequest,
    RecalledKnowledge,
    PeopleRequest,
    PeopleResult,
)


def contract_schema() -> dict[str, JsonValue]:
    _, schema = models_json_schema(
        [(model, "validation") for model in PUBLIC_MODELS], title="Breadcrumbs MCP contracts"
    )
    return schema


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[2] / "schema" / "mcp_contracts.schema.json"
    target.write_text(json.dumps(contract_schema(), indent=2, sort_keys=True) + "\n")
