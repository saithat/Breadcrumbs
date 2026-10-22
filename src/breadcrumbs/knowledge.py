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
    if isinstance(left, str) and isinstance(right, str):
        return left.casefold() == right.casefold()
    return left == right


def _contains(have: JsonValue, want: JsonValue) -> bool:
    """Strings compare case-insensitively, lists match on overlap, objects match as subsets."""

    if isinstance(want, dict):
        return isinstance(have, dict) and all(
            key in have and _contains(have[key], child) for key, child in want.items()
        )
    wanted = want if isinstance(want, list) else [want]
    options = have if isinstance(have, list) else [have]
    return any(_equal(left, right) for left in options for right in wanted)


def _number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def _accepts(condition: Condition, requested: JsonValue) -> bool | None:
    """Whether a requested point value satisfies a typed condition; None when it cannot say."""

    if isinstance(requested, dict):
        unit = requested.get("unit")
        if unit and condition.unit and not _equal(unit, condition.unit):
            return None
        requested = requested.get("value")
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


def _field_key(name: str) -> str:
    return "".join(character for character in name.casefold() if character.isalnum())


def scope_compatibility(
    stored: Mapping[str, JsonValue],
    requested: Mapping[str, JsonValue],
    conditions: Iterable[Condition] = (),
) -> ScopeCompatibility:
    """Classify each requested scope field as compatible, incompatible, or unknown.

    A requested point value can satisfy a stored typed condition, e.g. ``workers=3`` is compatible
    with ``workers < 4``. Fields the item says nothing about are unknown, not incompatible.
    """

    stored_by_key = {_field_key(key): value for key, value in stored.items()}
    conditions_by_key: dict[str, list[Condition]] = {}
    for condition in conditions:
        for name in (condition.field, *condition.field_aliases):
            conditions_by_key.setdefault(_field_key(name), []).append(condition)

    result = ScopeCompatibility(compatible=[], incompatible=[], unknown=[], score=0.0)
    for field, value in requested.items():
        key = _field_key(field)
        outcomes = [_accepts(condition, value) for condition in conditions_by_key.get(key, [])]
        if key in stored_by_key:
            outcomes.append(_contains(stored_by_key[key], value))
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
