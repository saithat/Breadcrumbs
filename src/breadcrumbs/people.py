"""Stable provisional identities and transparent expertise-ranking constants."""

from __future__ import annotations

import hashlib
import unicodedata

EXPERTISE_METHOD = "expertise_evidence_v2"
INVESTIGATION_EXPERTISE_WEIGHT = 0.1
MIN_EXPERTISE_FINDING_COVERAGE = 0.25
EXPERTISE_QUERY_NOISE = frozenset(
    {
        "company",
        "demonstrated",
        "expert",
        "experts",
        "expertise",
        "experience",
        "has",
        "investigating",
        "investigator",
        "investigators",
        "our",
        "strongest",
        "who",
    }
)
ROLE_WEIGHTS = {
    "knowledge_author": 1.0,
    "finding_author": 0.9,
    "knowledge_reviewer": 0.35,
}
PRIMARY_ROLES = frozenset({"knowledge_author", "finding_author"})


def clean_person_name(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("person name must be a string")
    cleaned = " ".join(unicodedata.normalize("NFKC", value).split())
    if not cleaned:
        raise ValueError("person name must not be empty")
    return cleaned


def normalize_person_name(value: str) -> str:
    return clean_person_name(value).casefold()


def provisional_person_id(value: str) -> str:
    normalized = normalize_person_name(value)
    return "P-" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:12].upper()


def evidence_confidence(*, distinct_sessions: int, primary_evidence_count: int) -> str:
    if distinct_sessions >= 3 and primary_evidence_count >= 3:
        return "high"
    if distinct_sessions >= 2 and primary_evidence_count >= 2:
        return "moderate"
    return "low"


__all__ = [
    "EXPERTISE_METHOD",
    "EXPERTISE_QUERY_NOISE",
    "INVESTIGATION_EXPERTISE_WEIGHT",
    "MIN_EXPERTISE_FINDING_COVERAGE",
    "PRIMARY_ROLES",
    "ROLE_WEIGHTS",
    "clean_person_name",
    "evidence_confidence",
    "normalize_person_name",
    "provisional_person_id",
]
