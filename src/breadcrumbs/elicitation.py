"""Prior and posterior context packets for evidence-informed belief elicitation.

Both packets start with related, previously approved knowledge and the belief recorded after its
own evidence (arXiv:2606.29182), so the prior reflects what the organization already knows. The
prior then sees the conversation up to the evidence span; the posterior also sees the evidence and
what followed it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import get_args

from ingestion.models import Role

from .contracts import ElicitationPacket, PriorKnowledge
from .knowledge import json_text
from .surprise import BeliefLabel

INSTRUCTION = (
    "Using only the supplied context, assess whether the proposition holds for this organization. "
    "Treat the previously approved organizational knowledge and its recorded beliefs as evidence. "
    "Return exactly one allowed belief label and nothing else."
)
TRUNCATED = "[… context truncated …]"


@dataclass(frozen=True, slots=True)
class SourceMessage:
    id: str
    seq: int
    role: Role
    content: str


def _block(message: SourceMessage, body: str) -> str:
    return f"[{message.role} | {message.id}]\n{body}".rstrip()


def _knowledge_block(prior_knowledge: Sequence[PriorKnowledge]) -> str:
    if not prior_knowledge:
        return ""
    lines = ["Previously approved organizational knowledge (belief after its evidence):"]
    lines += [
        f"- [{item.kind}] {item.proposition} "
        f"(scope: {json_text(item.scope)}; belief: {item.posterior_mean:.2f})"
        for item in prior_knowledge
    ]
    return "\n".join(lines)


def build_packets(
    messages: Sequence[SourceMessage],
    source: SourceMessage,
    quote_start: int,
    quote_end: int,
    proposition: str,
    prior_knowledge: Sequence[PriorKnowledge],
    *,
    context_chars: int,
) -> tuple[ElicitationPacket, ElicitationPacket]:
    before: list[str] = []
    after: list[str] = []
    for message in sorted(messages, key=lambda m: m.seq):
        if message.seq < source.seq:
            before.append(_block(message, message.content))
        elif message.seq > source.seq:
            after.append(_block(message, message.content))
    if source.content[:quote_start].strip():
        before.append(_block(source, source.content[:quote_start]))
    if source.content[quote_end:].strip():
        after.insert(0, _block(source, source.content[quote_end:].lstrip()))

    before_text = "\n\n".join(before)
    after_text = "\n\n".join(after)
    before_truncated = len(before_text) > context_chars
    after_truncated = len(after_text) > context_chars
    if before_truncated:
        before_text = f"{TRUNCATED}\n{before_text[-context_chars:]}"
    if after_truncated:
        after_text = f"{after_text[:context_chars]}\n{TRUNCATED}"
    knowledge = _knowledge_block(prior_knowledge)
    evidence = f"[evidence | {source.id}]\n{source.content[quote_start:quote_end]}"

    def packet(phase: str, *parts: str, truncated: bool) -> ElicitationPacket:
        return ElicitationPacket.model_validate(
            {
                "phase": phase,
                "proposition": proposition,
                "context": "\n\n".join(part for part in parts if part),
                "context_truncated": truncated,
                "instruction": INSTRUCTION,
                "allowed_labels": list(get_args(BeliefLabel)),
            }
        )

    return (
        packet("prior", knowledge, before_text, truncated=before_truncated),
        packet(
            "posterior",
            knowledge,
            before_text,
            evidence,
            after_text,
            truncated=before_truncated or after_truncated,
        ),
    )
