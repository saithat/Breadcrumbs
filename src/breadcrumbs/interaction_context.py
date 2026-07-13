"""Deterministic evidence search and context packets for Memory Diff elicitation.

The model host decides whether an interaction is worth proposing as knowledge. This module gives
that host the source material it should not ask a researcher to supply: exact stored evidence and
matched before/after transcript contexts for repeated belief judgments.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from .knowledge import tokens


BELIEF_LABELS = [
    "strongly_disbelieve",
    "disbelieve",
    "uncertain",
    "believe",
    "strongly_believe",
]
MAX_EVIDENCE_CHARS = 1600
MIN_EVIDENCE_CHARS = 20
TRUNCATION_MARKER = "[… context truncated …]\n"


def _bounded_spans(content: str) -> Iterable[tuple[int, int, str]]:
    """Yield exact, line-oriented source spans without manufacturing paraphrases."""

    for line in re.finditer(r"[^\n]+", content):
        raw = line.group(0)
        left = len(raw) - len(raw.lstrip())
        right = len(raw.rstrip())
        start = line.start() + left
        end = line.start() + right
        if end - start < MIN_EVIDENCE_CHARS:
            continue
        while end - start > MAX_EVIDENCE_CHARS:
            proposed = start + MAX_EVIDENCE_CHARS
            whitespace = content.rfind(" ", start + MAX_EVIDENCE_CHARS // 2, proposed)
            cut = whitespace if whitespace > start else proposed
            quote = content[start:cut].rstrip()
            if len(quote) >= MIN_EVIDENCE_CHARS:
                yield start, start + len(quote), quote
            start = cut
            while start < end and content[start].isspace():
                start += 1
        quote = content[start:end]
        if len(quote) >= MIN_EVIDENCE_CHARS:
            yield start, end, quote


def rank_evidence(
    messages: Iterable[Mapping[str, Any]],
    query: str,
    scope: Mapping[str, Any],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Rank exact stored spans with a deterministic TF-IDF-style lexical score."""

    query_tokens = tokens(query)
    if not query_tokens:
        raise ValueError("evidence query must contain searchable terms")
    scope_text = json.dumps(scope, ensure_ascii=False, sort_keys=True)
    scope_tokens = tokens(scope_text)
    candidates: list[dict[str, Any]] = []
    for message in messages:
        content = str(message["content"])
        message_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        for start, end, quote in _bounded_spans(content):
            quote_tokens = tokens(quote)
            overlap = query_tokens & quote_tokens
            minimum_overlap = 1 if len(query_tokens) < 3 else 2
            if len(overlap) < minimum_overlap:
                continue
            candidates.append(
                {
                    "source_message_id": str(message["id"]),
                    "source_session_id": str(message["session_id"]),
                    "source_message_hash": message_hash,
                    "message_seq": int(message["seq"]),
                    "role": str(message["role"]),
                    "evidence_quote": quote,
                    "quote_start": start,
                    "quote_end": end,
                    "session_title": message.get("session_title"),
                    "source_researcher": message.get("source_researcher"),
                    "_tokens": quote_tokens,
                    "_overlap": overlap,
                    "_scope_overlap": scope_tokens & quote_tokens,
                    "_title_tokens": tokens(message.get("session_title") or ""),
                }
            )
    if not candidates:
        return []

    document_frequency = Counter(
        token
        for token in query_tokens
        for candidate in candidates
        if token in candidate["_tokens"]
    )
    total = len(candidates)
    query_lower = query.casefold().strip()
    numeric_query_tokens = {token for token in query_tokens if token.isdigit()}
    for candidate in candidates:
        idf_overlap = sum(
            math.log((total + 1) / (document_frequency[token] + 1)) + 1.0
            for token in candidate["_overlap"]
        )
        normalization = math.sqrt(
            max(len(query_tokens), 1) * max(len(candidate["_tokens"]), 1)
        )
        score = idf_overlap / normalization
        score += 0.12 * len(candidate["_scope_overlap"])
        score += 0.08 * len(query_tokens & candidate["_title_tokens"])
        score += 0.10 * len(numeric_query_tokens & candidate["_tokens"])
        if query_lower and query_lower in candidate["evidence_quote"].casefold():
            score += 1.0
        candidate["relevance_score"] = round(score, 6)

    candidates.sort(
        key=lambda item: (
            -item["relevance_score"],
            item["source_session_id"],
            item["message_seq"],
            item["quote_start"],
        )
    )
    result: list[dict[str, Any]] = []
    for rank, candidate in enumerate(candidates[:limit], start=1):
        clean = {key: value for key, value in candidate.items() if not key.startswith("_")}
        clean["rank"] = rank
        result.append(clean)
    return result


def _message_block(message: Mapping[str, Any], content: str | None = None) -> str:
    body = str(message["content"]) if content is None else content
    return f"[{message['role']} | {message['id']}]\n{body}".rstrip()


def _clip_left(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return TRUNCATION_MARKER + value[-limit:], True


def _clip_right(value: str, limit: int) -> tuple[str, bool]:
    if len(value) <= limit:
        return value, False
    return value[:limit] + "\n[… context truncated …]", True


def build_context_packets(
    messages: Iterable[Mapping[str, Any]],
    selected: Mapping[str, Any],
    proposition: str,
    *,
    context_chars: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build matched contexts immediately before and after one exact evidence span."""

    ordered = sorted(messages, key=lambda item: (int(item["seq"]), str(item["id"])))
    source_id = str(selected["source_message_id"])
    source = next((message for message in ordered if str(message["id"]) == source_id), None)
    if source is None:
        raise ValueError(f"source message {source_id} is not in its source session")
    start = int(selected["quote_start"])
    end = int(selected["quote_end"])
    content = str(source["content"])
    quote = str(selected["evidence_quote"])
    if content[start:end] != quote:
        raise ValueError("selected evidence offsets no longer match the stored source")

    before_blocks: list[str] = []
    after_blocks: list[str] = []
    for message in ordered:
        seq = int(message["seq"])
        source_seq = int(source["seq"])
        if seq < source_seq:
            before_blocks.append(_message_block(message))
        elif str(message["id"]) == source_id:
            if content[:start].strip():
                before_blocks.append(_message_block(message, content[:start].rstrip()))
            if content[end:].strip():
                after_blocks.append(_message_block(message, content[end:].lstrip()))
        elif seq > source_seq:
            after_blocks.append(_message_block(message))

    prior_raw = "\n\n".join(before_blocks)
    prior_context, prior_truncated = _clip_left(prior_raw, context_chars)
    after_raw = "\n\n".join(after_blocks)
    after_context, after_truncated = _clip_right(after_raw, context_chars)
    evidence_block = f"[evidence | {source_id}]\n{quote}"
    posterior_context = "\n\n".join(
        part for part in (prior_context, evidence_block, after_context) if part
    )
    instruction = (
        "Using only the supplied context, assess the proposition and return exactly one allowed "
        "belief label. Do not add explanation or use knowledge outside the context."
    )
    prior = {
        "phase": "prior",
        "proposition": proposition,
        "context": prior_context,
        "context_truncated": prior_truncated,
        "instruction": instruction,
        "allowed_labels": BELIEF_LABELS,
    }
    posterior = {
        "phase": "posterior",
        "proposition": proposition,
        "context": posterior_context,
        "context_truncated": prior_truncated or after_truncated,
        "instruction": instruction,
        "allowed_labels": BELIEF_LABELS,
    }
    return prior, posterior


__all__ = ["BELIEF_LABELS", "build_context_packets", "rank_evidence"]
