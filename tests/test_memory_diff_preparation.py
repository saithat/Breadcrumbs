from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from breadcrumbs.store import BreadcrumbsStore
from ingestion.store import connect


EVIDENCE_QUOTE = (
    "Use the spot-level TP53 comparison only as exploratory because there are three "
    "independent patients; thousands of spots do not provide patient-level replication."
)
SOURCE_CONTENT = "\n".join(
    (
        "The spot-level contrast is statistically precise.",
        EVIDENCE_QUOTE,
        "The next analysis should expand the independent patient cohort.",
    )
)
LIVE_DECISION = (
    "Let's use TCGA-CDR PFI as the primary endpoint for this exploratory BRCA screen. "
    "Keep OS as a secondary sensitivity analysis, and do not discard the signature from a "
    "null OS result alone."
)
LIVE_CONTEXT = [
    {
        "role": "user",
        "content": (
            "Which TCGA-BRCA survival endpoint is defensible for an exploratory "
            "immune-exclusion signature?"
        ),
    },
    {
        "role": "assistant",
        "content": (
            "The public TCGA Clinical Data Resource recommends PFI and DFI for BRCA, "
            "while OS and DSS require caution because follow-up yields fewer death events."
        ),
    },
    {"role": "user", "content": LIVE_DECISION},
]


class MemoryDiffPreparationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "breadcrumbs.db"
        connection = connect(self.path)
        connection.execute(
            "INSERT INTO chat_sessions("
            "id,url,title,scraped_at,researcher,raw_json"
            ") VALUES (?,?,?,?,?,?)",
            (
                "session-tp53",
                "https://example.test/tp53",
                "MOSAIC BLCA TP53 analysis",
                "2027-01-01T00:00:00Z",
                "Researcher From Transcript",
                "{}",
            ),
        )
        connection.executemany(
            "INSERT INTO chat_messages(id,session_id,seq,role,content) VALUES (?,?,?,?,?)",
            [
                (
                    "session-tp53:0",
                    "session-tp53",
                    0,
                    "user",
                    "Can we interpret the TP53 spot-level difference as confirmatory?",
                ),
                (
                    "session-tp53:1",
                    "session-tp53",
                    1,
                    "assistant",
                    SOURCE_CONTENT,
                ),
                (
                    "session-tp53:2",
                    "session-tp53",
                    2,
                    "user",
                    "Agreed; I will label this exploratory and recruit more patients.",
                ),
            ],
        )
        connection.commit()
        connection.close()
        self.store = BreadcrumbsStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _knowledge_count(self) -> int:
        connection = connect(self.path)
        try:
            return connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        finally:
            connection.close()

    @staticmethod
    def _request(**changes):
        request = {
            "proposition": (
                "Treat the spot-level TP53 comparison as exploratory until the independent "
                "patient cohort is larger."
            ),
            "rationale": "Spot counts are not independent patient replication.",
            "scope": {"dataset": "MOSAIC", "disease": "BLCA", "unit": "patient"},
        }
        request.update(changes)
        return request

    @staticmethod
    def _live_request(**changes):
        request = {
            "proposition": (
                "Use TCGA-CDR PFI as the primary endpoint for exploratory TCGA-BRCA "
                "immune-exclusion signature analyses; treat OS as sensitivity evidence."
            ),
            "rationale": (
                "The curated BRCA endpoint guidance supports PFI while limited death events "
                "make a null OS association insufficient to discard the signature."
            ),
            "scope": {
                "dataset": "TCGA-CDR",
                "disease": "BRCA",
                "analysis": "survival",
            },
            "evidence_query": (
                "TCGA-CDR PFI primary endpoint BRCA OS secondary sensitivity null OS "
                "discard signature"
            ),
            "live_context": LIVE_CONTEXT,
            "live_session_title": "TCGA-BRCA endpoint decision",
            "current_actor": "Dr. Chen",
        }
        request.update(changes)
        return request

    def test_natural_candidate_input_resolves_exact_source_and_context(self) -> None:
        # The host supplies a scientific proposition, not database IDs, quotes, or judgments.
        request = self._request()
        self.assertFalse(
            {
                "source_message_id",
                "evidence_quote",
                "prior_samples",
                "posterior_samples",
                "elicitation_run_id",
            }
            & set(request)
        )

        prepared = self.store.prepare_memory_diff(**request)

        self.assertEqual(prepared["source_origin"], "stored_interaction")
        self.assertEqual(prepared["captured_turn_count"], 0)
        selected = prepared["selected_evidence"]
        self.assertEqual(selected["source_message_id"], "session-tp53:1")
        self.assertEqual(selected["source_session_id"], "session-tp53")
        self.assertEqual(
            selected["source_message_hash"],
            hashlib.sha256(SOURCE_CONTENT.encode("utf-8")).hexdigest(),
        )
        self.assertEqual(selected["source_researcher"], "Researcher From Transcript")
        self.assertEqual(selected["evidence_quote"], EVIDENCE_QUOTE)
        start = SOURCE_CONTENT.index(EVIDENCE_QUOTE)
        self.assertEqual(selected["quote_start"], start)
        self.assertEqual(selected["quote_end"], start + len(EVIDENCE_QUOTE))
        self.assertEqual(
            SOURCE_CONTENT[selected["quote_start"] : selected["quote_end"]],
            EVIDENCE_QUOTE,
        )

        prior = prepared["elicitation"]["prior"]
        posterior = prepared["elicitation"]["posterior"]
        self.assertIn("confirmatory", prior["context"])
        self.assertIn("statistically precise", prior["context"])
        self.assertNotIn(EVIDENCE_QUOTE, prior["context"])
        self.assertIn(EVIDENCE_QUOTE, posterior["context"])
        self.assertIn("recruit more patients", posterior["context"])
        self.assertEqual(prior["proposition"], posterior["proposition"])
        self.assertEqual(prior["allowed_labels"], posterior["allowed_labels"])
        self.assertEqual(len(prior["allowed_labels"]), 5)

        # Source-session identity is evidence metadata, not an inferred knowledge author.
        self.assertIsNone(prepared["author_hint"])
        self.assertEqual(prepared["author_hint_source"], "unavailable")
        self.assertIn("author", prepared["missing_record_fields"])
        self.assertNotIn("author", prepared["record_template"])

    def test_distinct_candidates_have_no_hard_cap_and_reuse_one_live_snapshot(self) -> None:
        propositions = [
            "Use TCGA-CDR PFI as the primary endpoint for the exploratory BRCA screen.",
            "Keep overall survival as a secondary sensitivity analysis for the BRCA screen.",
            "Do not discard the immune-exclusion signature from a null overall-survival result alone.",
            "Treat the endpoint choice as specific to exploratory TCGA-BRCA survival analysis.",
            "Interpret PFI and overall survival as different evidence roles in this analysis.",
            "Preserve the null overall-survival result as sensitivity evidence rather than a primary endpoint decision.",
        ]
        draft_ids = set()
        source_session_ids = set()
        for proposition in propositions:
            prepared = self.store.prepare_memory_diff(
                **self._live_request(proposition=proposition)
            )
            draft_ids.add(prepared["draft_id"])
            source_session_ids.add(prepared["selected_evidence"]["source_session_id"])

        self.assertEqual(len(draft_ids), len(propositions))
        self.assertEqual(len(source_session_ids), 1)
        connection = connect(self.path)
        try:
            live_sessions = connection.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE id LIKE 'LIVE-%'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(live_sessions, 1)
        self.assertEqual(self._knowledge_count(), 0)

    def test_preparation_is_deterministic_and_host_actor_is_provenance(self) -> None:
        request = self._request(current_actor="Dr. Chen")

        first = self.store.prepare_memory_diff(**request)
        second = self.store.prepare_memory_diff(**request)

        self.assertEqual(first, second)
        self.assertTrue(first["draft_id"].startswith("MD-"))
        self.assertTrue(first["elicitation"]["run_id"])
        self.assertEqual(first["author_hint"], "Dr. Chen")
        self.assertEqual(first["author_hint_source"], "authenticated_actor")
        self.assertEqual(first["record_template"]["author"], "Dr. Chen")
        self.assertNotIn("author", first["missing_record_fields"])
        self.assertEqual(
            first["record_template"]["source_message_id"], "session-tp53:1"
        )
        self.assertEqual(
            first["record_template"]["evidence_quote"], EVIDENCE_QUOTE
        )
        self.assertNotIn("prior_samples", first["record_template"])
        self.assertNotIn("posterior_samples", first["record_template"])
        self.assertEqual(self._knowledge_count(), 0)

    def test_prepare_score_write_and_fresh_recall(self) -> None:
        prepared = self.store.prepare_memory_diff(
            **self._request(current_actor="Dr. Chen")
        )
        prior_samples = ["believe", "uncertain", "believe", "uncertain", "believe"]
        posterior_samples = [
            "strongly_believe",
            "believe",
            "strongly_believe",
            "strongly_believe",
            "believe",
        ]

        scored = self.store.score_surprise(prior_samples, posterior_samples)
        written = self.store.write_knowledge(
            {
                **prepared["record_template"],
                "prior_samples": prior_samples,
                "posterior_samples": posterior_samples,
                "approved_by": "Dr. Chen",
            }
        )
        recalled = self.store.recall_knowledge(
            "TP53 patient replication",
            scope={"disease": "BLCA", "dataset": "MOSAIC"},
        )

        self.assertGreater(scored["posterior_mean"], scored["prior_mean"])
        self.assertAlmostEqual(
            written["bayesian_surprise_bits"], scored["bayesian_surprise_bits"]
        )
        self.assertEqual(recalled[0]["id"], written["id"])
        self.assertEqual(recalled[0]["source_message_id"], "session-tp53:1")
        self.assertEqual(recalled[0]["evidence_quote"], EVIDENCE_QUOTE)

    def test_no_supported_source_does_not_write(self) -> None:
        request = self._request(
            proposition="Use an NRF2 radiotherapy response model in melanoma.",
            rationale="This unrelated candidate has no support in the ingested interaction.",
            scope={"disease": "SKCM"},
            evidence_query="NRF2 radiotherapy melanoma",
        )

        with self.assertRaises(ValueError):
            self.store.prepare_memory_diff(**request)

        self.assertEqual(self._knowledge_count(), 0)

    def test_live_context_is_captured_idempotently_without_manual_sync(self) -> None:
        request = self._live_request()

        first = self.store.prepare_memory_diff(**request)
        second = self.store.prepare_memory_diff(**request)

        self.assertEqual(first, second)
        self.assertEqual(first["source_origin"], "captured_live_context")
        self.assertEqual(first["captured_turn_count"], 3)
        selected = first["selected_evidence"]
        self.assertTrue(selected["source_session_id"].startswith("LIVE-"))
        self.assertEqual(selected["role"], "user")
        self.assertEqual(selected["evidence_quote"], LIVE_DECISION)
        self.assertEqual(first["record_template"]["author"], "Dr. Chen")
        self.assertNotIn(LIVE_DECISION, first["elicitation"]["prior"]["context"])
        self.assertIn(LIVE_DECISION, first["elicitation"]["posterior"]["context"])

        connection = connect(self.path)
        try:
            live_sessions = connection.execute(
                "SELECT COUNT(*) FROM chat_sessions WHERE id LIKE 'LIVE-%'"
            ).fetchone()[0]
            live_messages = connection.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE session_id = ?",
                (selected["source_session_id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(live_sessions, 1)
        self.assertEqual(live_messages, 3)
        self.assertEqual(self._knowledge_count(), 0)

    def test_live_context_prepare_approve_and_fresh_recall(self) -> None:
        prepared = self.store.prepare_memory_diff(**self._live_request())
        written = self.store.write_knowledge(
            {
                **prepared["record_template"],
                "prior_samples": ["uncertain"] * 5,
                "posterior_samples": ["strongly_believe"] * 5,
                "approved_by": "Dr. Chen",
            }
        )

        fresh_store = BreadcrumbsStore(self.path)
        recalled = fresh_store.recall_knowledge(
            "Should an immune exclusion score use overall survival only?",
            scope={"disease": "BRCA", "dataset": "TCGA-CDR"},
        )

        self.assertEqual(recalled[0]["id"], written["id"])
        self.assertEqual(recalled[0]["evidence_quote"], LIVE_DECISION)
        self.assertTrue(recalled[0]["source_session_id"].startswith("LIVE-"))

    def test_live_context_cannot_be_combined_with_stored_session_filter(self) -> None:
        with self.assertRaisesRegex(ValueError, "either live_context or source_session_id"):
            self.store.prepare_memory_diff(
                **self._live_request(source_session_id="session-tp53")
            )
        self.assertEqual(self._knowledge_count(), 0)


if __name__ == "__main__":
    unittest.main()
