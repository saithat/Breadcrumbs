"""Search text, full-text queries, and scope applicability for approved knowledge."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Iterable, Mapping

from pydantic import JsonValue

from .contracts import Condition, KnowledgeRecord, ScopeCompatibility

# Any-term tsquery from free text. PostgreSQL's english configuration drops stopwords and stems;
# the resulting lexemes are OR-ed so recall is not limited to items containing every word.
ANY_TERM_TSQUERY = (
    "(SELECT to_tsquery('simple', string_agg(quote_literal(lexeme), ' | ')) AS query "
    "FROM unnest(to_tsvector('english', %(query)s)))"
)

# Every phrase quoted in the query must occur in the item; phrases of only stopwords are ignored.
_HAS_ALL_PHRASES = """
    NOT EXISTS (
        SELECT 1 FROM unnest(%(phrases)s::text[]) phrase
        WHERE numnode(phraseto_tsquery('english', phrase)) > 0
          AND NOT k.search_document @@ phraseto_tsquery('english', phrase)
    )
"""

# Full-text search over the GIN-indexed english tsvector, ranked by cover density: items matching
# more query terms, closer together, rank higher.
SEARCH_SQL = f"""
SELECT k.id, ts_rank_cd(k.search_document, t.query) AS score
FROM knowledge_items k CROSS JOIN {ANY_TERM_TSQUERY} t
WHERE k.search_document @@ t.query AND {_HAS_ALL_PHRASES}
ORDER BY score DESC, k.id
LIMIT %(limit)s
"""


def quoted_phrases(query: str) -> list[str]:
    return re.findall(r'"([^"]+)"', query)


def json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def search_text(record: KnowledgeRecord) -> str:
    """One stable document for full-text retrieval, from approved fields only."""

    fields: list[tuple[str, object]] = [
        ("kind", record.kind),
        ("proposition", record.proposition),
        ("rationale", record.rationale),
        ("scope", record.scope),
        ("aliases", record.aliases),
        ("conditions", [condition.model_dump() for condition in record.conditions]),
        ("evidence_quote", record.evidence_quote),
        ("reason", record.reason),
    ]
    return "\n".join(
        f"{name}: {value if isinstance(value, str) else json_text(value)}"
        for name, value in fields
        if value not in (None, "", [], {})
    )


def _equal(left: JsonValue, right: JsonValue) -> bool:
    """Scalar equality; strings ignore case, and booleans never equal numbers."""

    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    if isinstance(left, bool) or isinstance(right, bool):
        return left is right
    return left == right


def _field_key(name: str) -> str:
    """Field names match ignoring case and punctuation; names of only symbols match as written."""

    folded = name.casefold()
    return "".join(character for character in folded if character.isalnum()) or folded.strip()


def _by_key(values: Mapping[str, JsonValue]) -> dict[str, list[JsonValue]]:
    """Group values by normalized field name; spellings that collide are all kept."""

    grouped: dict[str, list[JsonValue]] = {}
    for key, value in values.items():
        grouped.setdefault(_field_key(key), []).append(value)
    return grouped


def _contains(have: JsonValue, want: JsonValue) -> bool:
    """Strings compare case-insensitively, lists match on overlap (an empty list matches only an
    empty list), and objects match as subsets with field names normalized at every depth."""

    if isinstance(want, list):
        if not want:
            return have == []
        options = have if isinstance(have, list) else [have]
        return any(_contains(option, wanted) for option in options for wanted in want)
    if isinstance(have, list):
        return any(_contains(option, want) for option in have)
    if isinstance(want, dict):
        if not isinstance(have, dict):
            return False
        fields = _by_key(have)
        return all(
            any(_contains(option, child) for option in fields.get(_field_key(key), []))
            for key, child in want.items()
        )
    return not isinstance(have, dict) and _equal(have, want)


def _number(value: JsonValue) -> float | None:
    # Only JSON numbers compare numerically; strings such as "3" or "1_000" are unknown.
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _accepts(condition: Condition, requested: JsonValue) -> bool | None:
    """Whether a requested value satisfies a typed condition; None when it cannot say.

    A requested list satisfies the condition only if every element does. A requested object must
    carry ``value`` and, when the condition has a unit, the same ``unit``.
    """

    if isinstance(requested, list):
        outcomes = [_accepts(condition, element) for element in requested]
        if False in outcomes:
            return False
        return None if not outcomes or None in outcomes else True
    unit: JsonValue = None
    if isinstance(requested, dict):
        if "value" not in requested:
            return None
        unit, requested = requested.get("unit"), requested["value"]
    if requested is None or isinstance(requested, dict | list):
        return None
    if condition.unit is not None and not (isinstance(unit, str) and _equal(unit, condition.unit)):
        return None
    operand = condition.value
    match condition.operator:
        case "in":
            return isinstance(operand, list) and any(_equal(requested, o) for o in operand)
        case "eq":
            return _equal(requested, operand)
        case "ne":
            return not _equal(requested, operand)
    point, threshold = _number(requested), _number(operand)
    if point is None or threshold is None:
        return None
    return {
        "lt": point < threshold,
        "lte": point <= threshold,
        "gt": point > threshold,
        "gte": point >= threshold,
    }[condition.operator]


def scope_compatibility(
    stored: Mapping[str, JsonValue],
    requested: Mapping[str, JsonValue],
    conditions: Iterable[Condition] = (),
) -> ScopeCompatibility:
    """Classify each requested scope field as compatible, incompatible, or unknown.

    A requested point value can satisfy a stored typed condition, e.g. ``workers=3`` is compatible
    with ``workers < 4``. Fields the item says nothing about are unknown, not incompatible.
    """

    stored_by_key = _by_key(stored)
    conditions_by_key: dict[str, list[Condition]] = {}
    for condition in conditions:
        for name in dict.fromkeys(
            _field_key(n) for n in (condition.field, *condition.field_aliases)
        ):
            conditions_by_key.setdefault(name, []).append(condition)

    result = ScopeCompatibility(compatible=[], incompatible=[], unknown=[], score=0.0)
    for field, value in requested.items():
        key = _field_key(field)
        outcomes = [_accepts(condition, value) for condition in conditions_by_key.get(key, [])]
        if key in stored_by_key:
            outcomes.append(any(_contains(option, value) for option in stored_by_key[key]))
        # A matching scope field must never hide a failed or unevaluated condition.
        if False in outcomes:
            result.incompatible.append(field)
        elif outcomes and None not in outcomes:
            result.compatible.append(field)
        else:
            result.unknown.append(field)
    result.score = round(
        (len(result.compatible) - len(result.incompatible)) / max(1, len(requested)), 6
    )
    return result
