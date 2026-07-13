"""Model-versioned dense embeddings for public approved knowledge patches."""

from __future__ import annotations

import hashlib
import json
import math
import os
import struct
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, Protocol

DEFAULT_EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RRF_K = 60
DENSE_MIN_SIMILARITY = 0.55


class EmbeddingBackend(Protocol):
    model_id: str

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedBackend:
    """CPU-local FastEmbed adapter; model loading is deferred until the backend is enabled."""

    def __init__(self, model_id: str = DEFAULT_EMBEDDING_MODEL):
        from fastembed import TextEmbedding

        self.model_id = model_id
        self._model = TextEmbedding(model_name=model_id)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [
            list(map(float, vector))
            for vector in self._model.passage_embed(list(texts))
        ]

    def embed_query(self, text: str) -> list[float]:
        return list(map(float, next(iter(self._model.query_embed(text)))))


def backend_from_environment() -> EmbeddingBackend | None:
    enabled = os.getenv("BREADCRUMBS_EMBEDDINGS", "1").strip().casefold()
    if enabled in {"0", "false", "no", "off"}:
        return None
    if enabled not in {"1", "true", "yes", "on"}:
        raise ValueError("BREADCRUMBS_EMBEDDINGS must be true/false")
    return FastEmbedBackend(
        os.getenv("BREADCRUMBS_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL)
    )


def knowledge_search_text(item: Mapping[str, Any]) -> str:
    """Render one stable search document from approved, source-linked fields only."""

    fields = (
        ("kind", item.get("kind")),
        ("proposition", item.get("proposition")),
        ("rationale", item.get("rationale")),
        ("scope", item.get("scope")),
        ("aliases", item.get("aliases")),
        ("conditions", item.get("conditions")),
        ("evidence_quote", item.get("evidence_quote")),
        ("action_after", item.get("action_after")),
        ("reason", item.get("reason")),
    )
    lines: list[str] = []
    for name, value in fields:
        if value in (None, "", [], {}):
            continue
        rendered = value if isinstance(value, str) else json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        lines.append(f"{name}: {rendered}")
    return "\n".join(lines)


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def pack_vector(values: Iterable[float]) -> tuple[bytes, int]:
    vector = [float(value) for value in values]
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError("embedding vector must contain finite values")
    return struct.pack(f"<{len(vector)}f", *vector), len(vector)


def unpack_vector(blob: bytes, dimensions: int) -> list[float]:
    if dimensions <= 0 or len(blob) != dimensions * 4:
        raise ValueError("stored embedding dimensions do not match vector bytes")
    return list(struct.unpack(f"<{dimensions}f", blob))


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("embedding dimensions must match")
    numerator = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return numerator / (left_norm * right_norm)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = [
    "DEFAULT_EMBEDDING_MODEL",
    "DENSE_MIN_SIMILARITY",
    "EmbeddingBackend",
    "FastEmbedBackend",
    "RRF_K",
    "backend_from_environment",
    "content_hash",
    "cosine_similarity",
    "knowledge_search_text",
    "pack_vector",
    "unpack_vector",
    "utc_now",
]
