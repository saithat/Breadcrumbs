"""Dependency-free metrics for evidence-informed belief surprise.

The functions in this module operate only on already-structured scores and
action counts.  They do not send research data to a model or infer scientific
importance.  A numeric belief score is treated as a fractional Bernoulli
observation when fitting a Beta distribution.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
import math
from numbers import Real
from types import MappingProxyType
from typing import Any, Hashable


_LOG_2 = math.log(2.0)

# These UI/spec labels are the canonical serialized form.  Input parsing also
# accepts case differences, surrounding whitespace, hyphens, and the original
# human-readable aliases ("definitely false", "maybe true", and so on).
BELIEF_LABELS: Mapping[str, float] = MappingProxyType(
    {
        "strongly_disbelieve": 0.0,
        "disbelieve": 0.25,
        "uncertain": 0.5,
        "believe": 0.75,
        "strongly_believe": 1.0,
    }
)

_BELIEF_SCORES_BY_NORMALIZED_LABEL: Mapping[str, float] = MappingProxyType(
    {
        "strongly disbelieve": BELIEF_LABELS["strongly_disbelieve"],
        "disbelieve": BELIEF_LABELS["disbelieve"],
        "uncertain": BELIEF_LABELS["uncertain"],
        "believe": BELIEF_LABELS["believe"],
        "strongly believe": BELIEF_LABELS["strongly_believe"],
        "definitely false": BELIEF_LABELS["strongly_disbelieve"],
        "maybe false": BELIEF_LABELS["disbelieve"],
        "maybe true": BELIEF_LABELS["believe"],
        "definitely true": BELIEF_LABELS["strongly_believe"],
    }
)


def _positive_finite(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError(f"{name} must be a real number")
    result = float(value)
    if not math.isfinite(result) or result <= 0.0:
        raise ValueError(f"{name} must be finite and greater than zero")
    return result


def _score(value: object) -> float:
    if isinstance(value, str):
        normalized = " ".join(value.strip().casefold().replace("_", " ").replace("-", " ").split())
        try:
            return _BELIEF_SCORES_BY_NORMALIZED_LABEL[normalized]
        except KeyError as exc:
            labels = ", ".join(BELIEF_LABELS)
            raise ValueError(f"unknown belief label {value!r}; expected one of: {labels}") from exc

    if isinstance(value, bool) or not isinstance(value, Real):
        raise TypeError("belief scores must be real numbers or belief labels")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("numeric belief scores must be finite and between 0 and 1")
    return result


@dataclass(frozen=True, slots=True)
class BetaDistribution:
    """The two positive shape parameters of a Beta distribution."""

    alpha: float
    beta: float

    def __post_init__(self) -> None:
        alpha = _positive_finite(self.alpha, "alpha")
        beta = _positive_finite(self.beta, "beta")
        if not math.isfinite(alpha + beta):
            raise ValueError("alpha + beta must be finite")
        object.__setattr__(self, "alpha", alpha)
        object.__setattr__(self, "beta", beta)

    @property
    def mean(self) -> float:
        return self.alpha / (self.alpha + self.beta)


@dataclass(frozen=True, slots=True)
class SurpriseMetrics:
    """Serializable before/after belief metrics, all information values in bits."""

    prior: BetaDistribution
    posterior: BetaDistribution
    prior_mean: float
    posterior_mean: float
    belief_shift: float
    absolute_shift: float
    bayesian_surprise_bits: float
    prior_entropy_bits: float
    posterior_entropy_bits: float
    certainty_gain_bits: float

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-ready nested dictionary."""

        return asdict(self)


def fit_beta(
    scores: Iterable[float | str],
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
) -> BetaDistribution:
    """Fit a Beta distribution using fractional pseudo-count updates.

    ``alpha`` and ``beta`` are the starting pseudo-counts.  For each score
    ``s``, the update adds ``s`` to alpha and ``1 - s`` to beta.
    """

    starting_alpha = _positive_finite(alpha, "alpha")
    starting_beta = _positive_finite(beta, "beta")
    if not math.isfinite(starting_alpha + starting_beta):
        raise ValueError("alpha + beta must be finite")
    if isinstance(scores, (str, bytes)) or not isinstance(scores, Iterable):
        raise TypeError("scores must be a non-string iterable")

    parsed_scores = [_score(value) for value in scores]
    if not parsed_scores:
        raise ValueError("scores must contain at least one belief score")

    fitted_alpha = starting_alpha + math.fsum(parsed_scores)
    fitted_beta = starting_beta + math.fsum(1.0 - value for value in parsed_scores)
    return BetaDistribution(fitted_alpha, fitted_beta)


def _digamma(value: float) -> float:
    """Approximate digamma for positive finite values.

    Recurrence moves small arguments to the asymptotic region.  The expansion
    through x^-10 is comfortably more accurate than the surrounding float
    calculations for the pseudo-count sizes used here.
    """

    result = 0.0
    x = value
    while x < 8.0:
        result -= 1.0 / x
        x += 1.0

    inverse = 1.0 / x
    inverse_squared = inverse * inverse
    correction = inverse_squared * (
        -1.0 / 12.0
        + inverse_squared
        * (
            1.0 / 120.0
            + inverse_squared
            * (-1.0 / 252.0 + inverse_squared * (1.0 / 240.0 - inverse_squared / 132.0))
        )
    )
    return result + math.log(x) - 0.5 * inverse + correction


def _require_beta(value: object, name: str) -> BetaDistribution:
    if not isinstance(value, BetaDistribution):
        raise TypeError(f"{name} must be a BetaDistribution")
    return value


def _log_beta(distribution: BetaDistribution) -> float:
    return math.fsum(
        (
            math.lgamma(distribution.alpha),
            math.lgamma(distribution.beta),
            -math.lgamma(distribution.alpha + distribution.beta),
        )
    )


def beta_kl_bits(posterior: BetaDistribution, prior: BetaDistribution) -> float:
    """Return ``KL(posterior || prior)`` in bits."""

    post = _require_beta(posterior, "posterior")
    pre = _require_beta(prior, "prior")
    if post == pre:
        return 0.0

    post_total = post.alpha + post.beta
    divergence_nats = math.fsum(
        (
            _log_beta(pre),
            -_log_beta(post),
            (post.alpha - pre.alpha) * _digamma(post.alpha),
            (post.beta - pre.beta) * _digamma(post.beta),
            (pre.alpha + pre.beta - post_total) * _digamma(post_total),
        )
    )
    # Analytically non-negative; clamp cancellation noise from floating point.
    return max(0.0, divergence_nats / _LOG_2)


def beta_entropy_bits(distribution: BetaDistribution) -> float:
    """Return the differential entropy of a Beta distribution in bits."""

    dist = _require_beta(distribution, "distribution")
    total = dist.alpha + dist.beta
    entropy_nats = math.fsum(
        (
            _log_beta(dist),
            -(dist.alpha - 1.0) * _digamma(dist.alpha),
            -(dist.beta - 1.0) * _digamma(dist.beta),
            (total - 2.0) * _digamma(total),
        )
    )
    return entropy_nats / _LOG_2


def calculate_surprise(
    prior_scores: Iterable[float | str],
    posterior_scores: Iterable[float | str],
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
) -> SurpriseMetrics:
    """Fit prior/posterior beliefs and return their evidence-informed change."""

    if isinstance(prior_scores, (str, bytes)) or not isinstance(prior_scores, Iterable):
        raise TypeError("prior_scores must be a non-string iterable")
    if isinstance(posterior_scores, (str, bytes)) or not isinstance(posterior_scores, Iterable):
        raise TypeError("posterior_scores must be a non-string iterable")
    prior_values = list(prior_scores)
    posterior_values = list(posterior_scores)
    if len(prior_values) != len(posterior_values):
        raise ValueError("prior_scores and posterior_scores must contain the same number of samples")

    prior = fit_beta(prior_values, alpha=alpha, beta=beta)
    posterior = fit_beta(posterior_values, alpha=alpha, beta=beta)
    prior_mean = prior.mean
    posterior_mean = posterior.mean
    belief_shift = posterior_mean - prior_mean
    prior_entropy = beta_entropy_bits(prior)
    posterior_entropy = beta_entropy_bits(posterior)

    return SurpriseMetrics(
        prior=prior,
        posterior=posterior,
        prior_mean=prior_mean,
        posterior_mean=posterior_mean,
        belief_shift=belief_shift,
        absolute_shift=abs(belief_shift),
        bayesian_surprise_bits=beta_kl_bits(posterior, prior),
        prior_entropy_bits=prior_entropy,
        posterior_entropy_bits=posterior_entropy,
        certainty_gain_bits=prior_entropy - posterior_entropy,
    )


def _validated_counts(counts: object, name: str) -> dict[Hashable, float]:
    if not isinstance(counts, Mapping):
        raise TypeError(f"{name} must be a mapping of category to count")
    if not counts:
        raise ValueError(f"{name} must contain at least one category")

    validated: dict[Hashable, float] = {}
    has_observation = False
    for category, count in counts.items():
        if isinstance(count, bool) or not isinstance(count, Real):
            raise TypeError(f"count for category {category!r} must be a real number")
        numeric_count = float(count)
        if not math.isfinite(numeric_count) or numeric_count < 0.0:
            raise ValueError(f"count for category {category!r} must be finite and non-negative")
        validated[category] = numeric_count
        has_observation = has_observation or numeric_count > 0.0

    if not has_observation:
        raise ValueError(f"{name} must contain at least one positive count")
    return validated


def _categorical_counts(
    values: Mapping[Hashable, float] | Iterable[Hashable],
    name: str,
) -> dict[Hashable, float]:
    """Normalize either categorical samples or an explicit count mapping."""

    if isinstance(values, Mapping):
        return _validated_counts(values, name)
    if isinstance(values, (str, bytes)) or not isinstance(values, Iterable):
        raise TypeError(f"{name} must be a non-string iterable or count mapping")

    counts: dict[Hashable, float] = {}
    for index, category in enumerate(values):
        try:
            hash(category)
        except TypeError as exc:
            raise TypeError(f"category at index {index} must be hashable") from exc
        counts[category] = counts.get(category, 0.0) + 1.0

    if not counts:
        raise ValueError(f"{name} must contain at least one action")
    return counts


def _smoothed_probabilities(
    counts: Mapping[Hashable, float],
    categories: set[Hashable],
    smoothing: float,
) -> dict[Hashable, float]:
    # Scale before summing so even very large finite counts normalize without
    # overflowing.  Smooth the resulting empirical probabilities (rather than
    # raw counts), so proportional count mappings remain the same distribution.
    count_scale = max(counts.values())
    scaled_counts = {
        category: counts.get(category, 0.0) / count_scale for category in categories
    }
    scaled_total = math.fsum(scaled_counts.values())
    empirical = {
        category: count / scaled_total for category, count in scaled_counts.items()
    }

    smoothing_scale = max(1.0, smoothing)
    smoothing_scaled = smoothing / smoothing_scale
    weights = {
        category: probability / smoothing_scale + smoothing_scaled
        for category, probability in empirical.items()
    }
    total = math.fsum(weights.values())
    return {category: weight / total for category, weight in weights.items()}


def jensen_shannon_bits(
    before_actions: Mapping[Hashable, float] | Iterable[Hashable],
    after_actions: Mapping[Hashable, float] | Iterable[Hashable],
    *,
    smoothing: float = 1e-12,
) -> float:
    """Return smoothed Jensen-Shannon divergence between categorical actions.

    Each side may be a sequence of categorical samples or an explicit mapping
    from category to count.  Categories are aligned by their union.  Smoothing
    is added to every empirical category probability on both sides, making
    missing and zero-count categories safe without making the result depend on
    total sample count.  With base-two logarithms the result lies in ``[0, 1]``.
    """

    before = _categorical_counts(before_actions, "before_actions")
    after = _categorical_counts(after_actions, "after_actions")
    smoothing_value = _positive_finite(smoothing, "smoothing")
    categories = set(before) | set(after)

    p = _smoothed_probabilities(before, categories, smoothing_value)
    q = _smoothed_probabilities(after, categories, smoothing_value)
    if p == q:
        return 0.0

    contributions: list[float] = []
    for category in categories:
        p_value = p[category]
        q_value = q[category]
        midpoint = 0.5 * (p_value + q_value)
        if p_value > 0.0:
            contributions.append(0.5 * p_value * math.log2(p_value / midpoint))
        if q_value > 0.0:
            contributions.append(0.5 * q_value * math.log2(q_value / midpoint))

    # JSD is analytically bounded in this range.  Clamp only round-off noise.
    return min(1.0, max(0.0, math.fsum(contributions)))


__all__ = [
    "BELIEF_LABELS",
    "BetaDistribution",
    "SurpriseMetrics",
    "beta_entropy_bits",
    "beta_kl_bits",
    "calculate_surprise",
    "fit_beta",
    "jensen_shannon_bits",
]
