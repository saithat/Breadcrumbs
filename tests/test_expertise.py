from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from breadcrumbs.store import BreadcrumbsStore
from ingestion.store import connect


class FakeInvestigationEmbeddingBackend:
    """Make a dense-only investigation paraphrase deterministic without model downloads."""

    model_id = "test-investigation-semantic-v1"

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.casefold()
        if any(
            marker in normalized
            for marker in ("chromatin", "accessibility", "epigenomic", "feasibility screen")
        ):
            return [1.0, 0.0]
        return [0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class ExpertiseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "expertise.db"
        connection = connect(self.path)
        sessions = [
            (
                "expert-s1",
                "https://example.test/1",
                "One",
                "2027-01-01T00:00:00Z",
                "2027-01-01T01:00:00Z",
                "Dr. Chen",
                "{}",
            ),
            (
                "expert-s2",
                "https://example.test/2",
                "Two",
                "2027-01-02T00:00:00Z",
                "2027-01-02T01:00:00Z",
                "  dr.   chen ",
                "{}",
            ),
            (
                "expert-s3",
                "https://example.test/3",
                "Three",
                "2027-01-03T00:00:00Z",
                "2027-01-03T01:00:00Z",
                "Dr. Alvarez",
                "{}",
            ),
            (
                "expert-s4",
                "https://example.test/4",
                "Four",
                "2027-01-04T00:00:00Z",
                "2027-01-04T01:00:00Z",
                "Dr. Patel",
                "{}",
            ),
            (
                "expert-blank",
                "https://example.test/blank",
                "Blank researcher",
                "2027-01-05T00:00:00Z",
                "2027-01-05T01:00:00Z",
                None,
                "{}",
            ),
        ]
        connection.executemany(
            "INSERT INTO chat_sessions("
            "id,url,title,scraped_at,updated_at,researcher,raw_json"
            ") VALUES (?,?,?,?,?,?,?)",
            sessions,
        )
        self.quotes = {
            "expert-s1:1": "Use independent patients for TP53 inference, not spot counts alone.",
            "expert-s1:2": "The TP53 patient model should include cohort-level covariates.",
            "expert-s2:1": "We abandoned spot-count-only TP53 inference because patients confound it.",
            "expert-s3:1": "Replicate TP53 patient inference in an independent BLCA cohort.",
        }
        connection.executemany(
            "INSERT INTO chat_messages(id,session_id,seq,role,content) VALUES (?,?,?,?,?)",
            [
                (
                    "expert-s1:q",
                    "expert-s1",
                    0,
                    "user",
                    "How should I validate TP53 patient inference in BLCA?",
                ),
                (
                    "expert-s2:q",
                    "expert-s2",
                    0,
                    "user",
                    "Can spot counts support TP53 patient inference?",
                ),
                (
                    "expert-s3:q",
                    "expert-s3",
                    0,
                    "user",
                    "Can we replicate TP53 patient inference in another cohort?",
                ),
                (
                    "expert-s4:q",
                    "expert-s4",
                    0,
                    "user",
                    "Which chromatin accessibility pilot dataset should I inspect?",
                ),
                (
                    "expert-blank:q",
                    "expert-blank",
                    0,
                    "user",
                    "How should I validate TP53 patient inference in BLCA?",
                ),
            ]
            + [
                (
                    message_id,
                    message_id.split(":")[0],
                    int(message_id.split(":")[1]),
                    "assistant",
                    quote,
                )
                for message_id, quote in self.quotes.items()
            ],
        )
        connection.commit()
        connection.close()
        self.store = BreadcrumbsStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def knowledge(self, message_id: str, proposition: str, *, author: str, **changes):
        record = {
            "kind": "decision",
            "proposition": proposition,
            "rationale": "Independent patient evidence changes whether the TP53 result generalizes.",
            "scope": {"disease": "BLCA", "comparison": "TP53"},
            "aliases": [],
            "conditions": [],
            "evidence_quote": self.quotes[message_id],
            "source_message_id": message_id,
            "prior_samples": ["uncertain", "uncertain", "disbelieve"],
            "posterior_samples": ["believe", "strongly_believe", "believe"],
            "elicitation_model": "claude-sonnet-5",
            "elicitation_run_id": f"expertise-{message_id}",
            "action_before": {"interpretation": "confirmatory"},
            "action_after": {"interpretation": "patient-qualified"},
            "author": author,
            "approved_by": "Dr. Reviewer",
        }
        record.update(changes)
        return self.store.write_knowledge(record)

    def seed_expertise(self) -> None:
        self.knowledge(
            "expert-s1:1",
            "Require independent patients for TP53 inference rather than relying on spot counts.",
            author="Dr. Chen",
        )
        self.knowledge(
            "expert-s1:2",
            "Include cohort-level covariates in the TP53 patient model.",
            author="Dr. Chen",
        )
        self.knowledge(
            "expert-s2:1",
            "Do not use spot-count-only TP53 patient inference.",
            author="  dr.   chen ",
            kind="abandoned",
            reason="Patient-level confounding invalidated the approach.",
        )
        self.knowledge(
            "expert-s3:1",
            "Replicate TP53 patient inference in an independent BLCA cohort.",
            author="Dr. Alvarez",
        )
        self.store.write(
            {
                "id": "F-EXPERT-1",
                "category": "LUAD-immune",
                "disease": "BLCA",
                "hypothesis_text": "Test TP53 patient inference in an independent cohort.",
                "entities": ["TP53"],
                "effect": "No effect size was calculated in this synthetic fixture.",
                "status": "in-progress",
                "author": "Dr. Alvarez",
                "source_session_id": "expert-s3",
                "source_type": "internal",
            }
        )

    def test_storage_canonicalizes_people_and_backfills_contribution_roles(self) -> None:
        self.seed_expertise()
        connection = connect(self.path)
        try:
            chen = connection.execute(
                "SELECT id, display_name, identity_status FROM people "
                "WHERE normalized_name = 'dr. chen'"
            ).fetchall()
            roles = connection.execute(
                "SELECT role, COUNT(*) AS n FROM person_contributions "
                "GROUP BY role ORDER BY role"
            ).fetchall()
            investigations = connection.execute(
                "SELECT i.session_id, i.topic_message_id, i.topic_message_hash, "
                "p.normalized_name FROM person_investigations i "
                "JOIN people p ON p.id = i.person_id ORDER BY i.session_id"
            ).fetchall()
            with connection:
                connection.execute("DELETE FROM person_contributions")
                connection.execute("DELETE FROM people")
        finally:
            connection.close()

        self.assertEqual(len(chen), 1)
        self.assertEqual(chen[0]["identity_status"], "provisional")
        self.assertEqual(
            {row["role"]: row["n"] for row in roles},
            {"finding_author": 1, "knowledge_author": 4, "knowledge_reviewer": 4},
        )
        self.assertEqual(len(investigations), 4)
        self.assertEqual(
            [row["session_id"] for row in investigations],
            ["expert-s1", "expert-s2", "expert-s3", "expert-s4"],
        )
        self.assertEqual(investigations[0]["topic_message_id"], "expert-s1:q")
        self.assertEqual(len(investigations[0]["topic_message_hash"]), 64)
        self.assertEqual(
            [row["normalized_name"] for row in investigations[:2]],
            ["dr. chen", "dr. chen"],
        )

        BreadcrumbsStore(self.path)
        BreadcrumbsStore(self.path)
        connection = connect(self.path)
        try:
            people_count = connection.execute("SELECT COUNT(*) FROM people").fetchone()[0]
            edge_count = connection.execute(
                "SELECT COUNT(*) FROM person_contributions"
            ).fetchone()[0]
            investigation_count = connection.execute(
                "SELECT COUNT(*) FROM person_investigations"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(people_count, 4)
        self.assertEqual(edge_count, 9)
        self.assertEqual(investigation_count, 4)

    def test_find_experts_caps_sessions_keeps_abandoned_and_excludes_review_only(self) -> None:
        self.seed_expertise()

        result = self.store.find_experts(
            "TP53 patient inference", scope={"disease": "BLCA"}
        )

        self.assertEqual(result["ranking_method"]["id"], "expertise_evidence_v2")
        self.assertFalse(result["ranking_method"]["score_is_probability"])
        self.assertEqual(
            [person["display_name"] for person in result["experts"]],
            ["Dr. Chen", "Dr. Alvarez"],
        )
        chen = result["experts"][0]
        self.assertEqual(chen["distinct_sessions"], 2)
        self.assertEqual(chen["primary_evidence_count"], 3)
        self.assertEqual(chen["confidence"], "moderate")
        self.assertLessEqual(chen["score_components"]["session_capped_evidence"], 2.0)
        self.assertGreater(chen["score_components"]["investigation_activity_bonus"], 0.0)
        abandoned = [item for item in chen["evidence"] if item["kind"] == "abandoned"]
        self.assertEqual(len(abandoned), 1)
        self.assertIn("confounding", abandoned[0]["reason"])
        self.assertNotIn("Dr. Reviewer", [p["display_name"] for p in result["experts"]])
        self.assertIn("Highest evidence score", result["message"])
        self.assertIn("does not establish an organizational role", result["message"])
        active = {person["display_name"]: person for person in result["active_investigators"]}
        self.assertEqual(active["Dr. Chen"]["matching_session_count"], 2)
        self.assertEqual(active["Dr. Chen"]["activity_depth"], "repeated")
        self.assertFalse(active["Dr. Chen"]["signal_is_expertise"])
        self.assertNotIn("Dr. Patel", [p["display_name"] for p in result["experts"]])
        self.assertNotIn("Dr. Reviewer", active)
        natural_question = self.store.find_experts(
            "Who is the TP53 patient-inference expert at our company?",
            scope={"disease": "BLCA"},
        )
        self.assertEqual(natural_question["experts"][0]["display_name"], "Dr. Chen")

    def test_investigation_only_is_returned_separately_and_never_as_expertise(self) -> None:
        self.seed_expertise()

        result = self.store.find_experts("chromatin accessibility pilot dataset")

        self.assertEqual(result["experts"], [])
        self.assertEqual(
            [person["display_name"] for person in result["active_investigators"]],
            ["Dr. Patel"],
        )
        patel = result["active_investigators"][0]
        self.assertEqual(patel["matching_session_count"], 1)
        self.assertEqual(patel["activity_depth"], "single-session")
        self.assertFalse(patel["signal_is_expertise"])
        self.assertIn("not expertise evidence", result["message"])

    def test_find_experts_excludes_off_disease_low_coverage_and_non_people(self) -> None:
        self.seed_expertise()
        for finding_id, disease, author, hypothesis in (
            (
                "F-OFF-DISEASE",
                "OV",
                "Dr. Noise",
                "TP53 patient inference using spatial immune architecture.",
            ),
            (
                "F-NON-PERSON",
                "BLCA",
                "AI-agent",
                "TP53 patient inference using spatial immune architecture.",
            ),
            (
                "F-LOW-COVERAGE",
                "BLCA",
                "Dr. Incidental",
                "A spatial assay for an unrelated ovarian endpoint.",
            ),
        ):
            self.store.write(
                {
                    "id": finding_id,
                    "category": "LUAD-immune",
                    "disease": disease,
                    "hypothesis_text": hypothesis,
                    "entities": (
                        ["EGFR"] if finding_id == "F-LOW-COVERAGE" else ["TP53"]
                    ),
                    "effect": "No effect size was calculated in this retrieval fixture.",
                    "status": "in-progress",
                    "author": author,
                    "source_session_id": "expert-s3",
                    "source_type": "internal",
                }
            )

        result = self.store.find_experts(
            "TP53 patient inference spatial immune architecture",
            scope={"disease": "BLCA"},
        )

        names = [person["display_name"] for person in result["experts"]]
        self.assertNotIn("Dr. Noise", names)
        self.assertNotIn("AI-agent", names)
        self.assertNotIn("Dr. Incidental", names)
        self.assertEqual(
            result["ranking_method"]["minimum_finding_field_coverage"], 0.25
        )

    def test_dense_only_paraphrase_retrieves_investigation_activity(self) -> None:
        self.seed_expertise()
        dense_store = BreadcrumbsStore(
            self.path, embedding_backend=FakeInvestigationEmbeddingBackend()
        )

        result = dense_store.find_experts("epigenomic feasibility screen")

        self.assertEqual(result["experts"], [])
        self.assertEqual(
            [person["display_name"] for person in result["active_investigators"]],
            ["Dr. Patel"],
        )
        components = result["active_investigators"][0]["investigations"][0][
            "retrieval_components"
        ]
        self.assertEqual(components["field_coverage"], 0.0)
        self.assertEqual(components["dense_similarity"], 1.0)
        self.assertEqual(
            components["embedding_model"], "test-investigation-semantic-v1"
        )

    def test_find_experts_empty_result_is_calibrated_and_validates_limits(self) -> None:
        result = self.store.find_experts("metabolomics instrument calibration")
        self.assertEqual(result["experts"], [])
        self.assertEqual(result["active_investigators"], [])
        self.assertEqual(
            result["searched_sources"],
            ["knowledge_items", "findings", "chat_sessions"],
        )
        self.assertIn("sources searched", result["message"])
        with self.assertRaisesRegex(ValueError, "topic"):
            self.store.find_experts("...")
        with self.assertRaisesRegex(ValueError, "limit"):
            self.store.find_experts("TP53", limit=0)


if __name__ == "__main__":
    unittest.main()
