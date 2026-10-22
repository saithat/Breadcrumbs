"""Bayesian surprise from repeated, evidence-informed belief judgments.

Follows the elicitation in "Evidence-Informed LLM Beliefs for Continual Scientific Discovery"
(arXiv:2606.29182). Each sampled label is a fractional Bernoulli observation of a Beta belief.
Surprisal is the change in the empirical mean belief, and a candidate is *surprising* when that
change is at least ``SURPRISE_THRESHOLD``. Bayesian surprise is ``KL(posterior || prior)`` between
the fitted Betas, in bits. The prior is evidence-informed because the elicitation context includes
previously approved, related organizational knowledge with its own posterior belief.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

type ScoringMethod = Literal["beta_kl_mean_shift_v2"]
SCORING_METHOD: ScoringMethod = "beta_kl_mean_shift_v2"
SURPRISE_THRESHOLD = 0.3

BeliefLabel = Literal[
    "strongly_disbelieve",
    "disbelieve",
    "uncertain",
    "believe",
    "strongly_believe",
]
BELIEF_LABELS: dict[BeliefLabel, float] = {
    "strongly_disbelieve": 0.0,
    "disbelieve": 0.25,
    "uncertain": 0.5,
    "believe": 0.75,
    "strongly_believe": 1.0,
}
# The paper's wording is accepted as an alias of each canonical label.
_SCORES_BY_LABEL: dict[str, float] = {
    **{label.replace("_", " "): score for label, score in BELIEF_LABELS.items()},
    "definitely false": 0.0,
    "maybe false": 0.25,
    "maybe true": 0.75,
    "definitely true": 1.0,
}
_LOG_2 = math.log(2.0)


def belief_score(value: float | str) -> float:
    """Map a belief label or a probability in [0, 1] to its numeric belief."""

    if isinstance(value, str):
        normalized = " ".join(value.casefold().replace("_", " ").replace("-", " ").split())
        if normalized not in _SCORES_BY_LABEL:
            raise ValueError(
                f"unknown belief label {value!r}; expected one of: {', '.join(BELIEF_LABELS)}"
            )
        return _SCORES_BY_LABEL[normalized]
    if isinstance(value, bool) or not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("numeric belief scores must be finite and between 0 and 1")
    return float(value)


@dataclass(frozen=True, slots=True)
class Beta:
    alpha: float
    beta: float


def fit_beta(scores: Sequence[float | str], *, alpha: float = 1.0, beta: float = 1.0) -> Beta:
    """Add each score ``s`` to alpha and ``1 - s`` to beta, from the starting pseudo-counts."""

    if not scores:
        raise ValueError("at least one belief score is required")
    values = [belief_score(value) for value in scores]
    return Beta(alpha + math.fsum(values), beta + math.fsum(1.0 - value for value in values))


def _digamma(value: float) -> float:
    # Recurrence into the asymptotic region, then the expansion through x^-10.
    result = 0.0
    x = value
    while x < 8.0:
        result -= 1.0 / x
        x += 1.0
    inverse_squared = 1.0 / (x * x)
    correction = inverse_squared * (
        -1.0 / 12.0
        + inverse_squared
        * (
            1.0 / 120.0
            + inverse_squared
            * (-1.0 / 252.0 + inverse_squared * (1.0 / 240.0 - inverse_squared / 132.0))
        )
    )
    return result + math.log(x) - 0.5 / x + correction


def _log_beta(distribution: Beta) -> float:
    return (
        math.lgamma(distribution.alpha)
        + math.lgamma(distribution.beta)
        - math.lgamma(distribution.alpha + distribution.beta)
    )


def beta_kl_bits(posterior: Beta, prior: Beta) -> float:
    """Return ``KL(posterior || prior)`` in bits."""

    if posterior == prior:
        return 0.0
    posterior_total = posterior.alpha + posterior.beta
    divergence_nats = math.fsum(
        (
            _log_beta(prior),
            -_log_beta(posterior),
            (posterior.alpha - prior.alpha) * _digamma(posterior.alpha),
            (posterior.beta - prior.beta) * _digamma(posterior.beta),
            (prior.alpha + prior.beta - posterior_total) * _digamma(posterior_total),
        )
    )
    # Analytically non-negative; clamp floating-point cancellation noise.
    return max(0.0, divergence_nats / _LOG_2)


class SurpriseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    scoring_method: ScoringMethod = SCORING_METHOD
    prior_mean: float
    posterior_mean: float
    belief_shift: float
    bayesian_surprise_bits: float
    surprising: bool


def calculate_surprise(
    prior_scores: Sequence[float | str], posterior_scores: Sequence[float | str]
) -> SurpriseMetrics:
    if len(prior_scores) != len(posterior_scores):
        raise ValueError("prior and posterior samples must have the same length")
    prior_mean = math.fsum(map(belief_score, prior_scores)) / len(prior_scores)
    posterior_mean = math.fsum(map(belief_score, posterior_scores)) / len(posterior_scores)
    shift = posterior_mean - prior_mean
    return SurpriseMetrics(
        prior_mean=prior_mean,
        posterior_mean=posterior_mean,
        belief_shift=shift,
        bayesian_surprise_bits=beta_kl_bits(fit_beta(posterior_scores), fit_beta(prior_scores)),
        # Rounding keeps label means such as 0.8 - 0.5 from missing the threshold by 1 ulp.
        surprising=round(abs(shift), 9) >= SURPRISE_THRESHOLD,
    )
