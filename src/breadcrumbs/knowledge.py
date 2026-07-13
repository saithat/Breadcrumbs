"""Validation and deterministic helpers for interaction-derived knowledge.

Candidate generation stays in the approved K Pro/agent host.  This module owns the parts that
must not depend on model judgment: reproducible surprise calculations, structured action diffs,
scope matching, and lexical retrieval features.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from .surprise import calculate_surprise, jensen_shannon_bits


KNOWLEDGE_KINDS = frozenset(
    {"decision", "constraint", "exception", "abandoned", "belief_revision"}
)
APPROVED_ELICITATION_MODELS = frozenset({"claude-sonnet-5"})
MIN_BELIEF_SAMPLES = 3
MAX_SAMPLES = 50
SURPRISE_METHOD = "beta_fractional_jsd_v1"
CONDITION_OPERATORS = frozenset({"eq", "ne", "lt", "lte", "gt", "gte", "in"})

DERIVED_FIELDS = frozenset(
    {
        "source_session_id",
        "source_message_hash",
        "scoring_method",
        "prior_mean",
        "posterior_mean",
        "belief_shift",
        "absolute_shift",
        "bayesian_surprise_bits",
        "prior_entropy_bits",
        "posterior_entropy_bits",
        "certainty_gain_bits",
        "action_delta",
        "action_surprise_bits",
        "created_at",
    }
)

_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "with",
    }
)


def nonempty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def json_object(value: Any, field: str, *, allow_empty: bool = False) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be a JSON object")
    if not allow_empty and not value:
        raise ValueError(f"{field} must not be empty")
    if any(not isinstance(key, str) or not key.strip() for key in value):
        raise ValueError(f"{field} keys must be non-empty strings")
    # This also rejects non-JSON values and NaN/Infinity before they reach SQLite.
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain only finite JSON values") from exc
    return value


def json_dumps(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def alias_list(value: Any) -> list[str]:
    """Validate approved retrieval aliases without treating generated synonyms as authority."""

    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ValueError("aliases must be a JSON array of strings")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        alias = nonempty_text(raw, "aliases[]")
        key = alias.casefold()
        if key not in seen:
            result.append(alias)
            seen.add(key)
    return result


def condition_list(value: Any) -> list[dict[str, Any]]:
    """Validate machine-readable applicability predicates for an approved patch."""

    if value is None:
        return []
    if isinstance(value, (str, bytes, Mapping)) or not isinstance(value, Iterable):
        raise ValueError("conditions must be a JSON array")
    result: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            raise ValueError(f"conditions[{index}] must be a JSON object")
        unknown = set(raw) - {"field", "field_aliases", "operator", "value", "unit"}
        if unknown:
            raise ValueError(
                f"conditions[{index}] has unknown field(s): " + ", ".join(sorted(unknown))
            )
        field = nonempty_text(raw.get("field"), f"conditions[{index}].field")
        field_aliases = alias_list(raw.get("field_aliases"))
        operator = nonempty_text(
            raw.get("operator"), f"conditions[{index}].operator"
        ).casefold()
        if operator not in CONDITION_OPERATORS:
            raise ValueError(
                f"conditions[{index}].operator must be one of: "
                + ", ".join(sorted(CONDITION_OPERATORS))
            )
        operand = raw.get("value")
        try:
            json.dumps(operand, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"conditions[{index}].value must be finite JSON"
            ) from exc
        if operator == "in":
            if not isinstance(operand, list) or not operand:
                raise ValueError(f"conditions[{index}].value must be a non-empty array for 'in'")
        elif isinstance(operand, (Mapping, list)) or operand is None:
            raise ValueError(
                f"conditions[{index}].value must be a scalar for operator '{operator}'"
            )
        if operator in {"lt", "lte", "gt", "gte"} and (
            isinstance(operand, bool) or not isinstance(operand, (int, float))
        ):
            raise ValueError(
                f"conditions[{index}].value must be numeric for operator '{operator}'"
            )
        unit_value = raw.get("unit")
        unit = None if unit_value is None else nonempty_text(unit_value, f"conditions[{index}].unit")
        result.append(
            {
                "field": field,
                "field_aliases": field_aliases,
                "operator": operator,
                "value": operand,
                "unit": unit,
            }
        )
    return result


def sample_list(
    values: Any,
    field: str,
    *,
    minimum: int = MIN_BELIEF_SAMPLES,
) -> list[Any]:
    if isinstance(values, (str, bytes, Mapping)) or not isinstance(values, Iterable):
        raise ValueError(f"{field} must be a JSON array")
    result = list(values)
    if not minimum <= len(result) <= MAX_SAMPLES:
        raise ValueError(f"{field} must contain between {minimum} and {MAX_SAMPLES} samples")
    try:
        json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain only finite JSON values") from exc
    return result


def score_samples(
    prior_samples: Any,
    posterior_samples: Any,
    *,
    prior_action_samples: Any = None,
    posterior_action_samples: Any = None,
) -> dict[str, Any]:
    """Calculate flat, JSON-clean metrics from validated repeated judgments."""

    prior = sample_list(prior_samples, "prior_samples")
    posterior = sample_list(posterior_samples, "posterior_samples")
    if len(prior) != len(posterior):
        raise ValueError("prior_samples and posterior_samples must have the same length")
    result = calculate_surprise(prior, posterior).to_dict()
    result["scoring_method"] = SURPRISE_METHOD

    if (prior_action_samples is None) != (posterior_action_samples is None):
        raise ValueError(
            "prior_action_samples and posterior_action_samples must be supplied together"
        )
    if prior_action_samples is not None:
        before_actions = sample_list(prior_action_samples, "prior_action_samples", minimum=1)
        after_actions = sample_list(posterior_action_samples, "posterior_action_samples", minimum=1)
        if len(before_actions) != len(after_actions):
            raise ValueError(
                "prior_action_samples and posterior_action_samples must have the same length"
            )
        result["action_surprise_bits"] = jensen_shannon_bits(before_actions, after_actions)
    else:
        result["action_surprise_bits"] = None
    return result


def action_delta(before: Any, after: Any) -> list[dict[str, Any]]:
    """Return a stable, path-addressed JSON diff for a before/after action object."""

    if before is None and after is None:
        return []
    before_obj = json_object(before, "action_before")
    after_obj = json_object(after, "action_after")
    changes: list[dict[str, Any]] = []

    def walk(left: Any, right: Any, path: str) -> None:
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                child_path = f"{path}.{key}" if path else key
                if key not in left:
                    changes.append({"path": child_path, "before": None, "after": right[key]})
                elif key not in right:
                    changes.append({"path": child_path, "before": left[key], "after": None})
                else:
                    walk(left[key], right[key], child_path)
        elif left != right:
            changes.append({"path": path, "before": left, "after": right})

    walk(before_obj, after_obj, "")
    return changes


def scope_matches(stored: Mapping[str, Any], requested: Mapping[str, Any]) -> bool:
    """Return whether ``stored`` contains the requested structured scope.

    Scalars compare case-insensitively for strings.  A scalar matches an element of a stored list;
    two lists match when they overlap.  Nested request objects are treated as recursive subsets.
    """

    def equal(left: Any, right: Any) -> bool:
        if isinstance(left, str) and isinstance(right, str):
            return left.casefold() == right.casefold()
        return left == right

    def contains(have: Any, want: Any) -> bool:
        if isinstance(want, Mapping):
            return isinstance(have, Mapping) and all(
                key in have and contains(have[key], child) for key, child in want.items()
            )
        if isinstance(want, list):
            if not isinstance(have, list):
                return any(equal(have, item) for item in want)
            return any(equal(left, right) for left in have for right in want)
        if isinstance(have, list):
            return any(equal(item, want) for item in have)
        return equal(have, want)

    return contains(stored, requested)


def scope_compatibility(
    stored: Mapping[str, Any],
    requested: Mapping[str, Any],
    conditions: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify requested scope as compatible, incompatible, or unknown.

    Unknown inferred fields deliberately do not exclude a candidate. A requested point value can
    satisfy a stored typed predicate, e.g. ``buffer_pH=6.5`` is compatible with
    ``buffer_pH < 7.0``.
    """

    def field_key(value: str) -> str:
        return "".join(character for character in value.casefold() if character.isalnum())

    def equal(left: Any, right: Any) -> bool:
        if isinstance(left, str) and isinstance(right, str):
            return left.casefold() == right.casefold()
        return left == right

    def contains(have: Any, want: Any) -> bool:
        if isinstance(want, Mapping):
            return isinstance(have, Mapping) and all(
                key in have and contains(have[key], child) for key, child in want.items()
            )
        if isinstance(want, list):
            if not isinstance(have, list):
                return any(equal(have, item) for item in want)
            return any(equal(left, right) for left in have for right in want)
        if isinstance(have, list):
            return any(equal(item, want) for item in have)
        return equal(have, want)

    def numeric(value: Any) -> float | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            number = float(value)
            return number if math.isfinite(number) else None
        if isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                return None
            return number if math.isfinite(number) else None
        return None

    def predicate_accepts(condition: Mapping[str, Any], requested_value: Any) -> bool | None:
        if isinstance(requested_value, Mapping):
            requested_unit = requested_value.get("unit")
            requested_value = requested_value.get("value")
            condition_unit = condition.get("unit")
            if requested_unit and condition_unit and not equal(requested_unit, condition_unit):
                return None
        operator = condition["operator"]
        operand = condition["value"]
        if operator == "in":
            return any(equal(requested_value, option) for option in operand)
        if operator in {"eq", "ne"}:
            matched = equal(requested_value, operand)
            return matched if operator == "eq" else not matched
        point = numeric(requested_value)
        threshold = numeric(operand)
        if point is None or threshold is None:
            return None
        return {
            "lt": point < threshold,
            "lte": point <= threshold,
            "gt": point > threshold,
            "gte": point >= threshold,
        }[operator]

    stored_by_key = {field_key(key): value for key, value in stored.items()}
    conditions_by_key: dict[str, list[Mapping[str, Any]]] = {}
    for condition in conditions:
        names = [condition["field"], *condition.get("field_aliases", [])]
        for name in names:
            conditions_by_key.setdefault(field_key(str(name)), []).append(condition)

    compatible: list[str] = []
    incompatible: list[str] = []
    unknown: list[str] = []
    for field, requested_value in requested.items():
        key = field_key(field)
        if key in stored_by_key:
            target = compatible if contains(stored_by_key[key], requested_value) else incompatible
            target.append(field)
            continue
        predicates = conditions_by_key.get(key, [])
        if predicates:
            outcomes = [predicate_accepts(predicate, requested_value) for predicate in predicates]
            if all(outcome is True for outcome in outcomes):
                compatible.append(field)
            elif any(outcome is False for outcome in outcomes):
                incompatible.append(field)
            else:
                unknown.append(field)
            continue
        unknown.append(field)

    denominator = max(1, len(requested))
    score = (len(compatible) - len(incompatible)) / denominator
    return {
        "compatible": compatible,
        "incompatible": incompatible,
        "unknown": unknown,
        "score": round(score, 6),
    }


def tokens(value: Any) -> set[str]:
    """Tokenize text or JSON-shaped values for small-corpus, local lexical recall."""

    if isinstance(value, str):
        text = value
    else:
        text = json_dumps(value)
    return {
        match.group(0).casefold()
        for match in _TOKEN_RE.finditer(text)
        if match.group(0).casefold() not in _STOPWORDS
    }


def lexical_score(query: str, item: Mapping[str, Any]) -> float:
    """Score query-token coverage with explicit field weights; zero means no lexical match."""

    query_tokens = tokens(query)
    if not query_tokens:
        return 0.0
    weighted_fields = (
        (3.0, item.get("proposition", "")),
        (2.0, item.get("rationale", "")),
        (2.0, item.get("scope", {})),
        (2.5, item.get("aliases", [])),
        (2.0, item.get("conditions", [])),
        (1.5, item.get("action_after") or {}),
        (1.0, item.get("reason") or ""),
        (0.5, item.get("evidence_quote", "")),
    )
    matched_weight = 0.0
    for token in query_tokens:
        matched_weight += max(
            (weight for weight, value in weighted_fields if token in tokens(value)),
            default=0.0,
        )
    score = matched_weight / (3.0 * len(query_tokens))
    normalized_query = " ".join(query.casefold().split())
    proposition = " ".join(str(item.get("proposition", "")).casefold().split())
    if len(normalized_query) >= 8 and (
        normalized_query in proposition or proposition in normalized_query
    ):
        score += 0.25
    return round(score, 6)


__all__ = [
    "DERIVED_FIELDS",
    "APPROVED_ELICITATION_MODELS",
    "KNOWLEDGE_KINDS",
    "SURPRISE_METHOD",
    "alias_list",
    "action_delta",
    "condition_list",
    "json_dumps",
    "json_object",
    "lexical_score",
    "nonempty_text",
    "sample_list",
    "scope_matches",
    "scope_compatibility",
    "score_samples",
    "tokens",
]
