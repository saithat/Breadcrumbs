import math

import pytest

from breadcrumbs.surprise import Beta, beta_kl_bits, calculate_surprise, fit_beta


def test_beta_kl_matches_closed_form_in_bits():
    # KL(Beta(2,1) || Beta(1,1)) is the integral of 2x ln(2x) on [0,1] = ln(2) - 1/2 nats.
    expected = 1 - 1 / (2 * math.log(2))
    assert beta_kl_bits(Beta(2, 1), Beta(1, 1)) == pytest.approx(expected, abs=1e-11)
    assert fit_beta(["definitely false", "maybe_true", 0.5, 1]) == Beta(3.25, 2.75)


def test_surprisal_uses_empirical_mean_shift_and_threshold():
    small = calculate_surprise(["uncertain"] * 5, ["believe"] * 5)
    assert (small.prior_mean, small.posterior_mean, small.belief_shift) == (0.5, 0.75, 0.25)
    assert not small.surprising
    assert small.bayesian_surprise_bits > 0

    up = calculate_surprise(["uncertain"] * 5, ["strongly_believe"] * 5)
    down = calculate_surprise(["uncertain"] * 5, ["strongly_disbelieve"] * 5)
    assert up.surprising
    assert down.surprising
    assert up.belief_shift == -down.belief_shift == 0.5
    assert calculate_surprise([0.2] * 3, [0.5] * 3).surprising  # exactly at τ = 0.3

    same = calculate_surprise([0, 0.5, 1], [0, 0.5, 1])
    assert same.belief_shift == same.bayesian_surprise_bits == 0
