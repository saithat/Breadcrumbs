from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from breadcrumbs.knowledge import APPROVED_ELICITATION_MODELS
from breadcrumbs.store import BreadcrumbsStore
from ingestion.store import connect, upsert_session


SOURCE_QUOTE = (
    "only 3 TP53-mutant patients are available — spot-level results are highly powered "
    "(14,054 vs 54,315 spots), but patient-level confounders cannot be excluded without a "
    "larger cohort."
)


class FakeEmbeddingBackend:
    """Deterministic semantic fixture; production tests do not download a model."""

    model_id = "test-semantic-v1"

    @staticmethod
    def _vector(text: str) -> list[float]:
        normalized = text.casefold()
        if any(
            marker in normalized
            for marker in ("acidic", "buffer_ph", "below 7.0", "proton-rich")
        ):
            return [1.0, 0.0, 0.0]
        if any(marker in normalized for marker in ("region-stratified", "tumor edge")):
            return [0.0, 1.0, 0.0]
        return [0.0, 0.0, 1.0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def candidate(**changes):
    value = {
        "kind": "constraint",
        "proposition": (
            "Treat spot-level TP53 differences in MOSAIC BLCA as exploratory until the "
            "patient-level cohort is larger."
        ),
        "rationale": "Spot counts do not replace independent patient-level replication.",
        "scope": {
            "dataset": "MOSAIC_WINDOW",
            "disease": "BLCA",
            "comparison": "TP53_mutant_vs_wild_type",
            "unit": "patient",
        },
        "aliases": ["MOSAIC Window TP53 patient cohort"],
        "conditions": [
            {
                "field": "independent_patients",
                "operator": "lt",
                "value": 4,
                "unit": "patients",
            }
        ],
        "evidence_quote": SOURCE_QUOTE,
        "source_message_id": "session-a:1",
        "prior_samples": ["uncertain", "disbelieve", "uncertain", "believe", "uncertain"],
        "posterior_samples": [
            "strongly_believe",
            "believe",
            "strongly_believe",
            "believe",
            "strongly_believe",
        ],
        "elicitation_model": "claude-sonnet-5",
        "elicitation_run_id": "test-elicitation-001",
        "action_before": {
            "interpretation": "confirmatory",
            "next_step": "report the spot-level group difference",
        },
        "action_after": {
            "interpretation": "exploratory",
            "next_step": "increase the patient-level cohort before inference",
        },
        "author": "Dr. Chen",
        "approved_by": "Dr. Chen",
    }
    value.update(changes)
    return value


class KnowledgeMemoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "breadcrumbs.db"
        connection = connect(self.path)
        connection.executemany(
            "INSERT INTO chat_sessions(id,url,title,scraped_at,raw_json) VALUES (?,?,?,?,?)",
            [
                ("session-a", "https://example.test/a", "A", "2027-01-01T00:00:00Z", "{}"),
                ("session-b", "https://example.test/b", "B", "2027-01-02T00:00:00Z", "{}"),
            ],
        )
        connection.executemany(
            "INSERT INTO chat_messages(id,session_id,seq,role,content) VALUES (?,?,?,?,?)",
            [
                (
                    "session-a:1",
                    "session-a",
                    1,
                    "assistant",
                    "Important caveat: " + SOURCE_QUOTE,
                ),
                (
                    "session-b:1",
                    "session-b",
                    1,
                    "user",
                    "We now have 28 independent patients and should update the interpretation.",
                ),
                (
                    "session-b:2",
                    "session-b",
                    2,
                    "assistant",
                    "For OV, prefer a region-stratified model because tumor edge and islet differ.",
                ),
                (
                    "session-b:3",
                    "session-b",
                    3,
                    "user",
                    "A second correction should not branch from the same historical item.",
                ),
                (
                    "session-b:4",
                    "session-b",
                    4,
                    "assistant",
                    "For synthetic Assay X, when buffer pH is below 7.0, use Protocol B; "
                    "Protocol A caused unstable fluorescence.",
                ),
            ],
        )
        connection.commit()
        connection.close()
        self.store = BreadcrumbsStore(self.path)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def count(self) -> int:
        connection = connect(self.path)
        try:
            return connection.execute("SELECT COUNT(*) FROM knowledge_items").fetchone()[0]
        finally:
            connection.close()

    def test_gate_elicitation_models_match_repo_policy(self) -> None:
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / ".spec" / "repo.json").read_text(encoding="utf-8"))
        self.assertEqual(
            APPROVED_ELICITATION_MODELS,
            frozenset(config["specConfig"]["approvedModels"]),
        )

    def test_storage_round_trip_preserves_provenance_inputs_and_derived_metrics(self) -> None:
        written = self.store.write_knowledge(candidate())

        self.assertTrue(written["id"].startswith("K-"))
        self.assertEqual(written["source_session_id"], "session-a")
        self.assertEqual(len(written["source_message_hash"]), 64)
        self.assertEqual(written["evidence_quote"], SOURCE_QUOTE)
        self.assertEqual(written["scope"]["disease"], "BLCA")
        self.assertEqual(written["aliases"], ["MOSAIC Window TP53 patient cohort"])
        self.assertEqual(written["conditions"][0]["operator"], "lt")
        self.assertEqual(written["prior_samples"][0], "uncertain")
        self.assertEqual(written["scoring_method"], "beta_fractional_jsd_v1")
        self.assertEqual(written["elicitation_model"], "claude-sonnet-5")
        self.assertEqual(written["elicitation_run_id"], "test-elicitation-001")
        self.assertGreater(written["posterior_mean"], written["prior_mean"])
        self.assertGreater(written["bayesian_surprise_bits"], 0.0)
        self.assertEqual(
            {change["path"] for change in written["action_delta"]},
            {"interpretation", "next_step"},
        )
        self.assertFalse(written["is_superseded"])

        connection = connect(self.path)
        try:
            indexed = connection.execute(
                "SELECT COUNT(*) FROM knowledge_fts WHERE item_id = ?", (written["id"],)
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(indexed, 1)

        expected = self.store.score_surprise(
            written["prior_samples"], written["posterior_samples"]
        )
        self.assertAlmostEqual(
            written["bayesian_surprise_bits"], expected["bayesian_surprise_bits"]
        )

    def test_storage_migrates_retrieval_fields_without_rebuilding_knowledge(self) -> None:
        connection = sqlite3.connect(self.path)
        try:
            for trigger in ("knowledge_fts_ai", "knowledge_fts_ad", "knowledge_fts_au"):
                connection.execute(f"DROP TRIGGER IF EXISTS {trigger}")
            connection.execute("DROP TABLE knowledge_fts")
            connection.execute("DROP TABLE knowledge_embeddings")
            connection.execute("ALTER TABLE knowledge_items DROP COLUMN aliases")
            connection.execute("ALTER TABLE knowledge_items DROP COLUMN conditions")
            connection.commit()
        finally:
            connection.close()

        migrated = connect(self.path)
        try:
            columns = {row[1] for row in migrated.execute("PRAGMA table_info(knowledge_items)")}
            fts = migrated.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'knowledge_fts'"
            ).fetchone()
            embeddings = migrated.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'knowledge_embeddings'"
            ).fetchone()
        finally:
            migrated.close()
        self.assertTrue({"aliases", "conditions"} <= columns)
        self.assertIsNotNone(fts)
        self.assertIsNotNone(embeddings)

    def test_storage_retry_is_idempotent(self) -> None:
        first = self.store.write_knowledge(candidate())
        second = self.store.write_knowledge(candidate(approved_by="Dr. Chen"))
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(self.count(), 1)

    def test_gate_rejects_conflicting_retry_instead_of_hiding_human_edit(self) -> None:
        first = self.store.write_knowledge(candidate())
        with self.assertRaisesRegex(ValueError, "different reviewed field"):
            self.store.write_knowledge(candidate(rationale="A materially edited rationale."))
        self.assertEqual(self.count(), 1)
        recalled = self.store.recall_knowledge("TP53 patient")
        self.assertEqual(recalled[0]["id"], first["id"])
        self.assertNotEqual(recalled[0]["rationale"], "A materially edited rationale.")

    def test_storage_abandoned_reason_round_trips(self) -> None:
        written = self.store.write_knowledge(
            candidate(kind="abandoned", reason="Patient-level cohort is underpowered.")
        )
        self.assertEqual(written["kind"], "abandoned")
        self.assertEqual(written["reason"], "Patient-level cohort is underpowered.")

    def test_storage_source_survives_normal_session_reingest(self) -> None:
        written = self.store.write_knowledge(candidate())
        connection = connect(self.path)
        try:
            with patch(
                "ingestion.store.TRANSCRIPTS_DIR", Path(self.temp.name) / "transcripts"
            ):
                upsert_session(
                    connection,
                    session_id="session-a",
                    url="https://example.test/a",
                    title="A revised",
                    raw_payload={"revision": 2},
                    messages=[
                        {
                            "seq": 1,
                            "role": "assistant",
                            "content": "Important caveat: " + SOURCE_QUOTE,
                        },
                        {"seq": 2, "role": "user", "content": "What should we do next?"},
                    ],
                )
        finally:
            connection.close()

        recalled = self.store.recall_knowledge("TP53 patient cohort")
        self.assertEqual(recalled[0]["id"], written["id"])
        self.assertEqual(recalled[0]["source_message_hash"], written["source_message_hash"])
        self.assertFalse(recalled[0]["source_drifted"])

    def test_storage_reingest_flags_same_id_content_drift(self) -> None:
        written = self.store.write_knowledge(candidate())
        connection = connect(self.path)
        try:
            with patch(
                "ingestion.store.TRANSCRIPTS_DIR", Path(self.temp.name) / "transcripts"
            ):
                upsert_session(
                    connection,
                    session_id="session-a",
                    url="https://example.test/a",
                    title="A edited",
                    raw_payload={"revision": 4},
                    messages=[
                        {
                            "seq": 1,
                            "role": "assistant",
                            "content": "This answer was edited after approval.",
                        }
                    ],
                )
        finally:
            connection.close()

        recalled = self.store.recall_knowledge("TP53 patient cohort")
        self.assertEqual(recalled[0]["id"], written["id"])
        self.assertEqual(recalled[0]["evidence_quote"], SOURCE_QUOTE)
        self.assertTrue(recalled[0]["source_drifted"])

    def test_storage_reingest_cannot_silently_remove_approved_source(self) -> None:
        self.store.write_knowledge(candidate())
        connection = connect(self.path)
        try:
            with patch(
                "ingestion.store.TRANSCRIPTS_DIR", Path(self.temp.name) / "transcripts"
            ):
                with self.assertRaises(sqlite3.IntegrityError):
                    upsert_session(
                        connection,
                        session_id="session-a",
                        url="https://example.test/a",
                        title="A malformed revision",
                        raw_payload={"revision": 3},
                        messages=[
                            {"seq": 2, "role": "user", "content": "Source turn omitted."}
                        ],
                    )
        finally:
            connection.close()
        self.assertEqual(self.count(), 1)

    def test_gate_requires_named_approval(self) -> None:
        with self.assertRaisesRegex(ValueError, "approved_by"):
            self.store.write_knowledge(candidate(approved_by=""))
        self.assertEqual(self.count(), 0)

    def test_gate_requires_existing_source_message(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unknown source message"):
            self.store.write_knowledge(candidate(source_message_id="missing:1"))
        self.assertEqual(self.count(), 0)

    def test_gate_requires_verbatim_evidence(self) -> None:
        with self.assertRaisesRegex(ValueError, "verbatim"):
            self.store.write_knowledge(candidate(evidence_quote="A paraphrase is not provenance."))
        self.assertEqual(self.count(), 0)

    def test_gate_abandoned_requires_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "reason"):
            self.store.write_knowledge(candidate(kind="abandoned"))
        self.assertEqual(self.count(), 0)

    def test_gate_recomputes_instead_of_accepting_metric_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "calculated fields"):
            self.store.write_knowledge(candidate(bayesian_surprise_bits=999.0))
        self.assertEqual(self.count(), 0)

    def test_gate_requires_balanced_before_after_sampling(self) -> None:
        with self.assertRaisesRegex(ValueError, "same length"):
            self.store.write_knowledge(
                candidate(posterior_samples=["believe", "believe", "believe"])
            )
        with self.assertRaisesRegex(ValueError, "same length"):
            self.store.write_knowledge(
                candidate(
                    prior_action_samples=["run"],
                    posterior_action_samples=["stop", "stop"],
                )
            )
        self.assertEqual(self.count(), 0)

    def test_gate_validates_aliases_and_typed_conditions(self) -> None:
        with self.assertRaisesRegex(ValueError, "aliases"):
            self.store.write_knowledge(candidate(aliases="not-an-array"))
        with self.assertRaisesRegex(ValueError, "operator"):
            self.store.write_knowledge(
                candidate(
                    conditions=[
                        {"field": "buffer_pH", "operator": "approximately", "value": 7.0}
                    ]
                )
            )
        self.assertEqual(self.count(), 0)

    def test_gate_requires_logged_approved_elicitation(self) -> None:
        with self.assertRaisesRegex(ValueError, "elicitation_run_id"):
            self.store.write_knowledge(candidate(elicitation_run_id=""))
        with self.assertRaisesRegex(ValueError, "approved for reproducible elicitation"):
            self.store.write_knowledge(candidate(elicitation_model="unapproved-model"))
        self.assertEqual(self.count(), 0)

    def test_recall_filters_scope_and_unrelated_text(self) -> None:
        target = self.store.write_knowledge(candidate())
        self.store.write_knowledge(
            candidate(
                kind="decision",
                proposition="Use region-stratified modeling for the MOSAIC OV analysis.",
                rationale="Tumor edge and islet regions differ.",
                scope={"dataset": "MOSAIC_WINDOW", "disease": "OV", "unit": "region"},
                aliases=["MOSAIC OV region stratification"],
                conditions=[],
                action_before={"model": "pooled regions"},
                action_after={"model": "region-stratified"},
                evidence_quote=(
                    "prefer a region-stratified model because tumor edge and islet differ"
                ),
                source_message_id="session-b:2",
            )
        )

        rows = self.store.recall_knowledge(
            "TP53 patient cohort", scope={"disease": "BLCA"}
        )
        self.assertEqual([row["id"] for row in rows], [target["id"]])
        self.assertGreater(rows[0]["retrieval_score"], 0.0)
        soft = self.store.recall_knowledge("TP53", scope={"disease": "OV"})
        self.assertEqual([row["id"] for row in soft], [target["id"]])
        self.assertEqual(soft[0]["scope_compatibility"]["incompatible"], ["disease"])
        self.assertEqual(
            self.store.recall_knowledge(
                "TP53", scope={"disease": "OV"}, strict_scope=True
            ),
            [],
        )

    def test_recall_soft_scope_typed_condition_alias_and_bm25(self) -> None:
        target = self.store.write_knowledge(
            candidate(
                kind="decision",
                proposition=(
                    "For synthetic Assay X at buffer pH below 7.0, use Protocol B instead "
                    "of Protocol A."
                ),
                rationale="Protocol A caused unstable fluorescence in the acidic regime.",
                scope={"assay": "Assay X"},
                aliases=["AuroraDecisionToken", "acidic Assay X protocol"],
                conditions=[
                    {
                        "field": "buffer_pH",
                        "field_aliases": ["pH"],
                        "operator": "lt",
                        "value": 7.0,
                        "unit": "pH",
                    }
                ],
                action_before={"protocol": "Protocol A"},
                action_after={"protocol": "Protocol B"},
                evidence_quote=(
                    "For synthetic Assay X, when buffer pH is below 7.0, use Protocol B; "
                    "Protocol A caused unstable fluorescence."
                ),
                source_message_id="session-b:4",
            )
        )

        rows = self.store.recall_knowledge(
            "synthetic Assay X protocol buffer pH 6.5 prior decisions constraints",
            scope={"assay": "Assay X", "method": "synthetic assay", "pH": 6.5},
        )
        self.assertEqual(rows[0]["id"], target["id"])
        self.assertEqual(
            set(rows[0]["scope_compatibility"]["compatible"]),
            {"assay", "pH"},
        )
        self.assertEqual(rows[0]["scope_compatibility"]["unknown"], ["method"])
        self.assertIsNotNone(rows[0]["retrieval_components"]["bm25_rank"])

        alias_rows = self.store.recall_knowledge("AuroraDecisionToken")
        self.assertEqual([row["id"] for row in alias_rows], [target["id"]])
        self.assertEqual(
            self.store.recall_knowledge(
                "synthetic Assay X protocol",
                scope={"assay": "Assay X", "method": "synthetic assay"},
                strict_scope=True,
            ),
            [],
        )

    def test_storage_dense_embedding_supports_recall_paraphrase(self) -> None:
        dense_store = BreadcrumbsStore(
            self.path, embedding_backend=FakeEmbeddingBackend()
        )
        record = candidate(
            kind="decision",
            proposition=(
                "For synthetic Assay X at buffer pH below 7.0, use Protocol B instead "
                "of Protocol A."
            ),
            rationale="Protocol A caused unstable fluorescence in the acidic regime.",
            scope={"assay": "Assay X"},
            aliases=[],
            conditions=[
                {
                    "field": "buffer_pH",
                    "field_aliases": ["pH"],
                    "operator": "lt",
                    "value": 7.0,
                    "unit": "pH",
                }
            ],
            action_before={"protocol": "Protocol A"},
            action_after={"protocol": "Protocol B"},
            evidence_quote=(
                "For synthetic Assay X, when buffer pH is below 7.0, use Protocol B; "
                "Protocol A caused unstable fluorescence."
            ),
            source_message_id="session-b:4",
        )
        target = dense_store.write_knowledge(record)

        rows = dense_store.recall_knowledge("guidance for a proton-rich medium")

        self.assertEqual([row["id"] for row in rows], [target["id"]])
        components = rows[0]["retrieval_components"]
        self.assertEqual(components["field_coverage"], 0.0)
        self.assertIsNone(components["bm25_rank"])
        self.assertEqual(components["dense_rank"], 1)
        self.assertEqual(components["dense_similarity"], 1.0)
        self.assertEqual(components["embedding_model"], "test-semantic-v1")
        self.assertGreater(components["rank_fusion"], 0.0)
        self.assertEqual(dense_store.recall_knowledge("quarterly revenue forecast"), [])

        connection = connect(self.path)
        try:
            embedded = connection.execute(
                "SELECT model, dimensions, content_hash, length(vector) AS bytes "
                "FROM knowledge_embeddings WHERE item_id = ?",
                (target["id"],),
            ).fetchone()
        finally:
            connection.close()
        self.assertEqual(embedded["model"], "test-semantic-v1")
        self.assertEqual(embedded["dimensions"], 3)
        self.assertEqual(embedded["bytes"], 12)
        self.assertEqual(len(embedded["content_hash"]), 64)

        connection = connect(self.path)
        try:
            with connection:
                connection.execute(
                    "DELETE FROM knowledge_embeddings WHERE item_id = ?", (target["id"],)
                )
        finally:
            connection.close()
        retried = dense_store.write_knowledge(record)
        self.assertEqual(retried["id"], target["id"])
        connection = connect(self.path)
        try:
            repaired = connection.execute(
                "SELECT COUNT(*) FROM knowledge_embeddings WHERE item_id = ?",
                (target["id"],),
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(repaired, 1)

    def test_recall_defaults_to_active_patch_but_can_audit_history(self) -> None:
        prior = self.store.write_knowledge(candidate())
        revision = self.store.write_knowledge(
            candidate(
                kind="belief_revision",
                proposition=(
                    "With 28 independent patients, re-evaluate whether the TP53 difference "
                    "supports patient-level inference."
                ),
                rationale="The independent-patient evidence base has materially changed.",
                scope={
                    "dataset": "MOSAIC_WINDOW",
                    "disease": "BLCA",
                    "comparison": "TP53_mutant_vs_wild_type",
                    "unit": "patient",
                },
                evidence_quote=(
                    "We now have 28 independent patients and should update the interpretation."
                ),
                source_message_id="session-b:1",
                supersedes_id=prior["id"],
            )
        )

        active = self.store.recall_knowledge("TP53 patient inference")
        self.assertEqual([row["id"] for row in active], [revision["id"]])
        history = self.store.recall_knowledge(
            "TP53 patient inference", include_superseded=True
        )
        self.assertEqual({row["id"] for row in history}, {prior["id"], revision["id"]})
        old = next(row for row in history if row["id"] == prior["id"])
        self.assertTrue(old["is_superseded"])
        historical_alias = self.store.recall_knowledge("spot level")
        self.assertEqual([row["id"] for row in historical_alias], [revision["id"]])
        self.assertEqual(historical_alias[0]["matched_via_history"], [prior["id"]])

    def test_gate_rejects_branching_patch_history(self) -> None:
        prior = self.store.write_knowledge(candidate())
        revision = self.store.write_knowledge(
            candidate(
                kind="belief_revision",
                proposition="Re-evaluate TP53 patient-level inference with the expanded cohort.",
                rationale="The evidence base changed.",
                evidence_quote=(
                    "We now have 28 independent patients and should update the interpretation."
                ),
                source_message_id="session-b:1",
                supersedes_id=prior["id"],
            )
        )
        with self.assertRaisesRegex(ValueError, "already has a successor"):
            self.store.write_knowledge(
                candidate(
                    kind="belief_revision",
                    proposition="Create an alternative branch from the old TP53 constraint.",
                    rationale="This would make active memory ambiguous.",
                    evidence_quote=(
                        "A second correction should not branch from the same historical item."
                    ),
                    source_message_id="session-b:3",
                    supersedes_id=prior["id"],
                )
            )
        active = self.store.recall_knowledge("TP53 patient")
        self.assertEqual([row["id"] for row in active], [revision["id"]])


if __name__ == "__main__":
    unittest.main()
