import math
import unittest

from breadcrumbs.surprise import (
    BELIEF_LABELS,
    BetaDistribution,
    beta_entropy_bits,
    beta_kl_bits,
    calculate_surprise,
    fit_beta,
    jensen_shannon_bits,
)


class BeliefScoreTests(unittest.TestCase):
    def test_five_canonical_labels_have_expected_scores(self) -> None:
        self.assertEqual(
            dict(BELIEF_LABELS),
            {
                "strongly_disbelieve": 0.0,
                "disbelieve": 0.25,
                "uncertain": 0.5,
                "believe": 0.75,
                "strongly_believe": 1.0,
            },
        )

    def test_original_belief_vocabulary_is_accepted_as_aliases(self) -> None:
        aliases = (
            ("definitely false", "strongly_disbelieve"),
            ("maybe false", "disbelieve"),
            ("uncertain", "uncertain"),
            ("maybe true", "believe"),
            ("definitely true", "strongly_believe"),
        )

        for alias, canonical in aliases:
            with self.subTest(alias=alias, canonical=canonical):
                self.assertEqual(fit_beta([alias]), fit_beta([canonical]))

    def test_fit_beta_accepts_labels_and_numeric_scores(self) -> None:
        fitted = fit_beta(["definitely false", "maybe_true", 0.5, 1])

        self.assertEqual(fitted, BetaDistribution(alpha=3.25, beta=2.75))
        self.assertAlmostEqual(fitted.mean, 3.25 / 6.0)

    def test_fit_beta_normalizes_label_spelling(self) -> None:
        fitted = fit_beta(["  Definitely-False ", "MAYBE_TRUE"])

        self.assertEqual(fitted, BetaDistribution(alpha=1.75, beta=2.25))

    def test_fit_beta_uses_configurable_pseudo_counts(self) -> None:
        fitted = fit_beta([0.25, 0.75], alpha=2.0, beta=3.0)

        self.assertEqual(fitted, BetaDistribution(alpha=3.0, beta=4.0))


class BetaMetricTests(unittest.TestCase):
    def test_identical_distributions_have_zero_surprise(self) -> None:
        metrics = calculate_surprise(
            ["maybe false", "uncertain", "maybe true"],
            ["maybe false", "uncertain", "maybe true"],
        )

        self.assertEqual(metrics.belief_shift, 0.0)
        self.assertEqual(metrics.absolute_shift, 0.0)
        self.assertEqual(metrics.bayesian_surprise_bits, 0.0)
        self.assertEqual(metrics.certainty_gain_bits, 0.0)

    def test_belief_shift_preserves_direction(self) -> None:
        upward = calculate_surprise([0.0, 0.25], [0.75, 1.0])
        downward = calculate_surprise([0.75, 1.0], [0.0, 0.25])

        self.assertGreater(upward.belief_shift, 0.0)
        self.assertLess(downward.belief_shift, 0.0)
        self.assertAlmostEqual(upward.belief_shift, -downward.belief_shift)
        self.assertAlmostEqual(upward.absolute_shift, downward.absolute_shift)

    def test_known_beta_kl_value_is_in_bits(self) -> None:
        # Integral of 2x log(2x) on [0,1] is ln(2) - 1/2 nats.
        actual = beta_kl_bits(BetaDistribution(2.0, 1.0), BetaDistribution(1.0, 1.0))
        expected = 1.0 - 1.0 / (2.0 * math.log(2.0))

        self.assertAlmostEqual(actual, expected, places=11)

    def test_beta_entropy_and_certainty_gain_are_in_bits(self) -> None:
        uniform = BetaDistribution(1.0, 1.0)
        concentrated = BetaDistribution(2.0, 1.0)

        self.assertAlmostEqual(beta_entropy_bits(uniform), 0.0, places=12)
        expected_entropy = (0.5 - math.log(2.0)) / math.log(2.0)
        self.assertAlmostEqual(beta_entropy_bits(concentrated), expected_entropy, places=11)

    def test_boundary_scores_produce_finite_metrics(self) -> None:
        metrics = calculate_surprise([0.0] * 100, [1.0] * 100)

        for value in (
            metrics.prior_mean,
            metrics.posterior_mean,
            metrics.belief_shift,
            metrics.absolute_shift,
            metrics.bayesian_surprise_bits,
            metrics.prior_entropy_bits,
            metrics.posterior_entropy_bits,
            metrics.certainty_gain_bits,
        ):
            self.assertTrue(math.isfinite(value))
        self.assertGreater(metrics.bayesian_surprise_bits, 0.0)

    def test_result_serializes_to_nested_dictionary(self) -> None:
        result = calculate_surprise(["uncertain"], ["maybe true"]).to_dict()

        self.assertEqual(result["prior"], {"alpha": 1.5, "beta": 1.5})
        self.assertEqual(result["posterior"], {"alpha": 1.75, "beta": 1.25})
        self.assertIn("bayesian_surprise_bits", result)
        self.assertIn("certainty_gain_bits", result)


class JensenShannonTests(unittest.TestCase):
    def test_accepts_categorical_sample_sequences(self) -> None:
        before = ["run", "run", "revise"]
        after = ["revise", "revise", "stop"]

        forward = jensen_shannon_bits(before, after)
        reverse = jensen_shannon_bits(after, before)

        self.assertTrue(math.isfinite(forward))
        self.assertGreater(forward, 0.0)
        self.assertAlmostEqual(forward, reverse, places=15)

    def test_identical_action_distributions_are_zero(self) -> None:
        self.assertEqual(
            jensen_shannon_bits({"run": 2, "revise": 1}, {"run": 4, "revise": 2}),
            0.0,
        )

    def test_divergence_is_symmetric_and_bounded(self) -> None:
        before = {"all samples": 7, "pretreatment": 1}
        after = {"all samples": 1, "pretreatment": 9, "exclude": 2}

        forward = jensen_shannon_bits(before, after)
        reverse = jensen_shannon_bits(after, before)

        self.assertAlmostEqual(forward, reverse, places=15)
        self.assertGreaterEqual(forward, 0.0)
        self.assertLessEqual(forward, 1.0)

    def test_disjoint_categories_approach_one_bit(self) -> None:
        divergence = jensen_shannon_bits({"old": 10}, {"new": 10})

        self.assertGreater(divergence, 0.9999999999)
        self.assertLessEqual(divergence, 1.0)

    def test_smoothing_handles_missing_and_zero_categories(self) -> None:
        divergence = jensen_shannon_bits(
            {"keep": 10, "stop": 0},
            {"keep": 0, "stop": 10, "new": 5},
            smoothing=0.5,
        )

        self.assertTrue(math.isfinite(divergence))
        self.assertGreater(divergence, 0.0)
        self.assertLess(divergence, 1.0)

    def test_normalization_is_stable_for_very_large_counts(self) -> None:
        divergence = jensen_shannon_bits(
            {"a": 1e308, "b": 1e308},
            {"a": 1e308, "b": 0.0},
        )

        self.assertTrue(math.isfinite(divergence))
        self.assertGreaterEqual(divergence, 0.0)
        self.assertLessEqual(divergence, 1.0)


class ValidationTests(unittest.TestCase):
    def test_beta_distribution_rejects_invalid_shapes(self) -> None:
        for alpha, beta in ((0, 1), (-1, 1), (math.nan, 1), (math.inf, 1), (True, 1)):
            with self.subTest(alpha=alpha, beta=beta):
                with self.assertRaises((TypeError, ValueError)):
                    BetaDistribution(alpha, beta)

    def test_fit_beta_rejects_invalid_score_collections(self) -> None:
        invalid_collections = (
            [],
            "uncertain",
            ["not a belief"],
            [-0.01],
            [1.01],
            [math.nan],
            [math.inf],
            [True],
            [None],
        )
        for scores in invalid_collections:
            with self.subTest(scores=scores):
                with self.assertRaises((TypeError, ValueError)):
                    fit_beta(scores)  # type: ignore[arg-type]

    def test_fit_beta_rejects_invalid_pseudo_counts(self) -> None:
        for kwargs in ({"alpha": 0}, {"beta": -1}, {"alpha": math.nan}, {"beta": True}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises((TypeError, ValueError)):
                    fit_beta([0.5], **kwargs)

    def test_surprise_rejects_unequal_monte_carlo_sample_counts(self) -> None:
        with self.assertRaisesRegex(ValueError, "same number"):
            calculate_surprise(["uncertain"] * 3, ["uncertain"] * 50)

    def test_beta_metric_functions_require_beta_distributions(self) -> None:
        with self.assertRaises(TypeError):
            beta_entropy_bits((1.0, 1.0))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            beta_kl_bits(BetaDistribution(1.0, 1.0), (1.0, 1.0))  # type: ignore[arg-type]

    def test_js_rejects_invalid_mappings_and_counts(self) -> None:
        invalid = (
            {},
            {"a": 0},
            {"a": -1},
            {"a": math.nan},
            {"a": math.inf},
            {"a": True},
            {"a": "1"},
        )
        for counts in invalid:
            with self.subTest(counts=counts):
                with self.assertRaises((TypeError, ValueError)):
                    jensen_shannon_bits(counts, {"valid": 1})  # type: ignore[arg-type]

        for actions in ("run", [["unhashable"]]):
            with self.subTest(actions=actions):
                with self.assertRaises(TypeError):
                    jensen_shannon_bits(actions, ["run"])  # type: ignore[arg-type]

    def test_js_rejects_invalid_smoothing(self) -> None:
        for smoothing in (0, -1, math.nan, math.inf, True):
            with self.subTest(smoothing=smoothing):
                with self.assertRaises((TypeError, ValueError)):
                    jensen_shannon_bits({"a": 1}, {"b": 1}, smoothing=smoothing)


if __name__ == "__main__":
    unittest.main()
