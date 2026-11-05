"""Property-based tests for the pure scoring and applicability functions."""

import math
import random

import pytest
from hypothesis import example, given, settings
from hypothesis import strategies as st

from breadcrumbs.contracts import Condition
from breadcrumbs.knowledge import scope_compatibility
from breadcrumbs.surprise import (
    BELIEF_LABELS,
    SURPRISE_THRESHOLD,
    Beta,
    _digamma,
    belief_score,
    beta_kl_bits,
    calculate_surprise,
    fit_beta,
)

probabilities = st.floats(min_value=0.0, max_value=1.0)
beliefs = st.one_of(probabilities, st.sampled_from(list(BELIEF_LABELS)))
belief_samples = st.lists(beliefs, min_size=3, max_size=50)


@st.composite
def respelled_labels(draw):
    label = draw(st.sampled_from(list(BELIEF_LABELS)))
    separators = st.sampled_from(["_", "-", " ", "  ", "\t"])
    words = [
        "".join(c.upper() if draw(st.booleans()) else c for c in word) for word in label.split("_")
    ]
    spelled = words[0] + "".join(draw(separators) + word for word in words[1:])
    padding = st.text(alphabet=" \t\n", max_size=2)
    return label, draw(padding) + spelled + draw(padding)


@st.composite
def paired_samples(draw):
    prior = draw(belief_samples)
    posterior = draw(st.lists(beliefs, min_size=len(prior), max_size=len(prior)))
    return prior, posterior


@given(respelled_labels())
def test_belief_labels_ignore_case_separators_and_padding(case):
    label, spelled = case
    assert belief_score(spelled) == BELIEF_LABELS[label]


@given(
    st.one_of(
        st.floats().filter(lambda value: not 0.0 <= value <= 1.0),
        st.booleans(),
    )
)
def test_belief_scores_outside_unit_interval_are_rejected(value):
    with pytest.raises(ValueError, match="between 0 and 1"):
        belief_score(value)


@given(paired_samples(), st.randoms(use_true_random=False))
def test_surprise_ignores_sample_order(samples, rng: random.Random):
    prior, posterior = samples
    shuffled_prior, shuffled_posterior = list(prior), list(posterior)
    rng.shuffle(shuffled_prior)
    rng.shuffle(shuffled_posterior)
    assert calculate_surprise(prior, posterior) == calculate_surprise(
        shuffled_prior, shuffled_posterior
    )


@given(paired_samples())
def test_surprise_shift_is_bounded_and_antisymmetric(samples):
    prior, posterior = samples
    forward = calculate_surprise(prior, posterior)
    backward = calculate_surprise(posterior, prior)
    assert 0.0 <= forward.prior_mean <= 1.0
    assert 0.0 <= forward.posterior_mean <= 1.0
    assert backward.belief_shift == -forward.belief_shift
    assert backward.surprising == forward.surprising
    if abs(forward.belief_shift) >= SURPRISE_THRESHOLD + 1e-9:
        assert forward.surprising
    if abs(forward.belief_shift) <= SURPRISE_THRESHOLD - 1e-9:
        assert not forward.surprising


@given(paired_samples())
def test_bayesian_surprise_is_nonnegative_and_zero_without_change(samples):
    prior, posterior = samples
    assert calculate_surprise(prior, posterior).bayesian_surprise_bits >= 0.0
    assert calculate_surprise(prior, prior).bayesian_surprise_bits == 0.0


def _numerical_kl_bits(posterior: Beta, prior: Beta, steps: int = 20000) -> float:
    def log_density(distribution: Beta, x: float) -> float:
        return (
            (distribution.alpha - 1) * math.log(x)
            + (distribution.beta - 1) * math.log1p(-x)
            - math.lgamma(distribution.alpha)
            - math.lgamma(distribution.beta)
            + math.lgamma(distribution.alpha + distribution.beta)
        )

    # Midpoint rule: fitted Betas have alpha, beta >= 1, so both densities are bounded.
    total = math.fsum(
        math.exp(log_p) * (log_p - log_density(prior, x))
        for x in ((i + 0.5) / steps for i in range(steps))
        for log_p in (log_density(posterior, x),)
    )
    return total / steps / math.log(2.0)


@settings(deadline=None, max_examples=40)
@given(paired_samples())
def test_beta_kl_matches_numerical_integration(samples):
    prior, posterior = (fit_beta(scores) for scores in samples)
    expected = _numerical_kl_bits(posterior, prior)
    assert beta_kl_bits(posterior, prior) == pytest.approx(expected, rel=1e-3, abs=1e-3)


@given(st.floats(min_value=0.01, max_value=1e5))
def test_digamma_matches_derivative_of_log_gamma(x):
    step = 1e-4 * x
    expected = (math.lgamma(x + step) - math.lgamma(x - step)) / (2 * step)
    assert _digamma(x) == pytest.approx(expected, rel=1e-6, abs=1e-6)


# Lowercase alphanumeric keys normalize to themselves, so distinct keys never collide.
scope_keys = st.text(alphabet="abcdefgh", min_size=1, max_size=4)
json_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.text(max_size=8),
)
json_values = st.recursive(
    json_scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=3), st.dictionaries(scope_keys, children, max_size=3)
    ),
    max_leaves=8,
)
scopes = st.dictionaries(scope_keys, json_values, max_size=5)
numbers = st.one_of(
    st.integers(min_value=-1000, max_value=1000),
    st.floats(min_value=-1e6, max_value=1e6, allow_nan=False),
)


@st.composite
def conditions(draw):
    operator = draw(st.sampled_from(["eq", "ne", "lt", "lte", "gt", "gte", "in"]))
    if operator == "in":
        value = draw(st.lists(json_scalars, min_size=1, max_size=3))
    elif operator in {"lt", "lte", "gt", "gte"}:
        value = draw(numbers)
    else:
        value = draw(json_scalars.filter(lambda scalar: scalar is not None))
    return Condition.model_validate(
        {"field": draw(scope_keys), "operator": operator, "value": value}
    )


@given(scopes, scopes, st.lists(conditions(), max_size=4))
def test_every_requested_field_is_classified_exactly_once(stored, requested, typed):
    result = scope_compatibility(stored, requested, typed)
    classified = result.compatible + result.incompatible + result.unknown
    assert sorted(classified) == sorted(requested)
    assert -1.0 <= result.score <= 1.0
    if not requested:
        assert result.score == 0.0


@example(scope={"tags": []})
@example(scope={"limits": {"cpu": []}})
@given(st.dictionaries(scope_keys, json_values, min_size=1, max_size=5))
def test_scope_is_compatible_with_itself(scope):
    result = scope_compatibility(scope, scope)
    assert result.compatible == list(scope)
    assert result.score == 1.0


@given(numbers, numbers)
def test_numeric_conditions_partition_the_number_line(point, threshold):
    def accepts(operator):
        condition = Condition.model_validate(
            {"field": "x", "operator": operator, "value": threshold}
        )
        result = scope_compatibility({}, {"x": point}, [condition])
        assert result.unknown == []
        return result.compatible == ["x"]

    assert accepts("lt") != accepts("gte")
    assert accepts("gt") != accepts("lte")


def _condition(operator, value, **extra):
    return Condition.model_validate({"field": "x", "operator": operator, "value": value, **extra})


@given(conditions(), json_values)
def test_a_matching_scope_field_never_hides_a_failing_condition(condition, value):
    requested = {condition.field: value}
    alone = scope_compatibility({}, requested, [condition])
    with_scope = scope_compatibility(requested, requested, [condition])
    if alone.incompatible:
        assert with_scope.incompatible == [condition.field]
    if alone.unknown:
        assert with_scope.compatible == []


@given(conditions(), st.lists(json_scalars, min_size=1, max_size=4))
def test_a_requested_list_satisfies_a_condition_only_if_every_element_does(condition, values):
    def outcome(value):
        result = scope_compatibility({}, {condition.field: value}, [condition])
        return "compatible" if result.compatible else "incompatible" if result.incompatible else "?"

    each = [outcome(value) for value in values]
    expected = "incompatible" if "incompatible" in each else "?" if "?" in each else "compatible"
    assert outcome(values) == expected


@given(st.booleans(), st.integers(min_value=-2, max_value=2))
def test_booleans_never_match_numbers(flag, number):
    assert scope_compatibility({"x": flag}, {"x": number}).compatible == []
    assert scope_compatibility({"x": number}, {"x": flag}).compatible == []
    assert scope_compatibility({}, {"x": flag}, [_condition("eq", number)]).compatible == []
    assert scope_compatibility({}, {"x": number}, [_condition("in", [flag])]).compatible == []


@pytest.mark.parametrize(
    ("condition", "requested"),
    [
        (_condition("ne", "eu"), {"value": None}),
        (_condition("ne", "eu"), {"unit": "region"}),
        (_condition("lt", 200, unit="ms"), 2),
        (_condition("lt", 200, unit="ms"), {"value": 2, "unit": "s"}),
        (_condition("lt", 4), "3"),
        (_condition("gt", 4), "1_000"),
        (_condition("eq", "eu"), []),
    ],
)
def test_conditions_are_unknown_when_the_request_cannot_be_evaluated(condition, requested):
    assert scope_compatibility({}, {"x": requested}, [condition]).unknown == ["x"]


def test_condition_regressions_found_in_review():
    excluded = [_condition("ne", "eu")]
    assert scope_compatibility({}, {"x": ["eu"]}, excluded).incompatible == ["x"]
    assert scope_compatibility({}, {"x": ["us", "eu"]}, excluded).incompatible == ["x"]
    regions = [_condition("in", ["us", "eu"])]
    assert scope_compatibility({}, {"x": ["us"]}, regions).compatible == ["x"]
    latency = [_condition("lt", 200, unit="ms")]
    assert scope_compatibility({}, {"x": {"value": 150, "unit": "MS"}}, latency).compatible == ["x"]


spelled_keys = st.text(alphabet="aAbB_- ", min_size=1, max_size=4).filter(
    lambda key: any(character.isalnum() for character in key)
)


def _respell(value, draw):
    if isinstance(value, dict):
        return {_respell_key(key, draw): _respell(child, draw) for key, child in value.items()}
    if isinstance(value, list):
        return [_respell(child, draw) for child in value]
    return value


def _respell_key(key, draw):
    cased = "".join(c.upper() if draw(st.booleans()) else c for c in key)
    return draw(st.sampled_from(["", "_", "-"])).join([cased, ""]) if key else cased


@given(st.data(), st.dictionaries(scope_keys, json_values, min_size=1, max_size=4))
def test_field_names_match_ignoring_case_and_punctuation_at_every_depth(data, scope):
    respelled = _respell(scope, data.draw)
    assert sorted(scope_compatibility(scope, respelled).compatible) == sorted(respelled)


@given(st.dictionaries(spelled_keys, json_values, min_size=1, max_size=5))
def test_scope_with_colliding_field_spellings_is_compatible_with_itself(scope):
    assert sorted(scope_compatibility(scope, scope).compatible) == sorted(scope)


@pytest.mark.parametrize("keys", [("🚀", "🛰"), ("!!", "??")])
def test_symbol_only_field_names_do_not_collide(keys):
    stored = {keys[0]: "a", keys[1]: "b"}
    assert scope_compatibility(stored, {keys[1]: "a"}).incompatible == [keys[1]]


def test_nested_objects_inside_lists_compare_case_insensitively():
    stored = {"regions": [{"Name": "US"}, {"name": "EU"}]}
    assert scope_compatibility(stored, {"regions": {"name": "us"}}).compatible == ["regions"]


@given(st.dictionaries(scope_keys, st.text(alphabet="abcxyz -", min_size=1), min_size=1))
def test_scope_text_matches_case_insensitively(scope):
    shouted = {key: value.upper() for key, value in scope.items()}
    assert scope_compatibility(scope, shouted).compatible == list(scope)
