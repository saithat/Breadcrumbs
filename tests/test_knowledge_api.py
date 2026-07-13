from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient


_TEMP = tempfile.TemporaryDirectory()
_ORIGINAL_DB = os.environ.get("BREADCRUMBS_DB")
_ORIGINAL_EMBEDDINGS = os.environ.get("BREADCRUMBS_EMBEDDINGS")
os.environ["BREADCRUMBS_DB"] = str(Path(_TEMP.name) / "server.db")
os.environ["BREADCRUMBS_EMBEDDINGS"] = "0"

# The server constructs its canonical store at import time, so the isolated DB environment must be
# set first. No production or tracked database is touched by these contract tests.
from breadcrumbs.server import app, mcp  # noqa: E402
from ingestion.store import connect  # noqa: E402


QUOTE = "The patient-level cohort is too small for confirmatory inference."
LIVE_DECISION = (
    "Use TCGA-CDR PFI as the primary endpoint and keep OS as a sensitivity analysis."
)


def payload():
    return {
        "kind": "constraint",
        "proposition": "Treat the TP53 comparison as exploratory at patient level.",
        "rationale": "Spot counts are not independent patient replication.",
        "scope": {"disease": "BLCA", "unit": "patient"},
        "aliases": ["patient-level TP53 constraint"],
        "conditions": [
            {"field": "independent_patients", "operator": "lt", "value": 4, "unit": "patients"}
        ],
        "evidence_quote": QUOTE,
        "source_message_id": "api-session:1",
        "prior_samples": ["disbelieve", "uncertain", "uncertain"],
        "posterior_samples": ["believe", "strongly_believe", "believe"],
        "elicitation_model": "claude-sonnet-5",
        "elicitation_run_id": "api-elicitation-001",
        "action_before": {"interpretation": "confirmatory"},
        "action_after": {"interpretation": "exploratory"},
        "author": "Dr. Chen",
    }


class KnowledgeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        connection = connect(os.environ["BREADCRUMBS_DB"])
        connection.execute(
            "INSERT INTO chat_sessions("
            "id,url,title,scraped_at,researcher,raw_json"
            ") VALUES (?,?,?,?,?,?)",
            (
                "api-session",
                "https://example.test/api",
                "API",
                "2027-01-01T00:00:00Z",
                "Dr. Chen",
                "{}",
            ),
        )
        connection.executemany(
            "INSERT INTO chat_messages(id,session_id,seq,role,content) VALUES (?,?,?,?,?)",
            [
                (
                    "api-session:q",
                    "api-session",
                    0,
                    "user",
                    "How should I interpret TP53 at patient level?",
                ),
                ("api-session:1", "api-session", 1, "assistant", QUOTE),
            ],
        )
        connection.commit()
        connection.close()
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_context.__exit__(None, None, None)
        _TEMP.cleanup()
        if _ORIGINAL_DB is None:
            os.environ.pop("BREADCRUMBS_DB", None)
        else:
            os.environ["BREADCRUMBS_DB"] = _ORIGINAL_DB
        if _ORIGINAL_EMBEDDINGS is None:
            os.environ.pop("BREADCRUMBS_EMBEDDINGS", None)
        else:
            os.environ["BREADCRUMBS_EMBEDDINGS"] = _ORIGINAL_EMBEDDINGS

    def test_mcp_registers_surprise_write_and_recall_tools(self) -> None:
        tools = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
        self.assertTrue(
            {
                "prepare_memory_diff",
                "score_surprise",
                "write_knowledge",
                "recall_knowledge",
                "find_experts",
            }
            <= set(tools)
        )
        prepare = tools["prepare_memory_diff"]
        self.assertFalse(prepare.annotations.readOnlyHint)
        self.assertFalse(prepare.annotations.destructiveHint)
        self.assertTrue(prepare.annotations.idempotentHint)
        self.assertIn("live_context", prepare.inputSchema["properties"])
        for caller_supplied_field in (
            "source_message_id",
            "evidence_quote",
            "prior_samples",
            "posterior_samples",
            "elicitation_run_id",
        ):
            self.assertNotIn(caller_supplied_field, prepare.inputSchema["properties"])
        write_schema = tools["write_knowledge"].inputSchema
        self.assertEqual(set(write_schema["required"]), {"record", "approved_by"})
        self.assertNotIn("approved_by", write_schema["properties"]["record"].get("properties", {}))
        recall = tools["recall_knowledge"]
        self.assertTrue(recall.annotations.readOnlyHint)
        self.assertIn("strict_scope", recall.inputSchema["properties"])
        self.assertTrue(tools["find_experts"].annotations.readOnlyHint)
        self.assertIn("evidence_limit", tools["find_experts"].inputSchema["properties"])

    def test_rest_prepare_resolves_source_without_technical_metadata(self) -> None:
        response = self.client.post(
            "/knowledge/prepare",
            json={
                "proposition": "Treat TP53 inference as exploratory at patient level.",
                "rationale": "Independent patient replication is too small.",
                "scope": {"disease": "BLCA", "unit": "patient"},
                "evidence_query": "patient cohort too small confirmatory inference",
                "current_actor": "Dr. Chen",
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["selected_evidence"]["source_message_id"], "api-session:1")
        self.assertEqual(result["selected_evidence"]["evidence_quote"], QUOTE)
        self.assertEqual(result["record_template"]["author"], "Dr. Chen")
        self.assertEqual(result["author_hint_source"], "authenticated_actor")
        self.assertNotIn("prior_samples", result["record_template"])
        self.assertNotIn("posterior_samples", result["record_template"])

    def test_rest_prepare_captures_live_agent_context_without_sync(self) -> None:
        response = self.client.post(
            "/knowledge/prepare",
            json={
                "proposition": "Use PFI as the primary TCGA-BRCA endpoint.",
                "rationale": "The public endpoint guidance cautions against relying on OS alone.",
                "scope": {"disease": "BRCA", "dataset": "TCGA-CDR"},
                "evidence_query": "TCGA-CDR PFI primary endpoint OS sensitivity",
                "live_context": [
                    {
                        "role": "assistant",
                        "content": "TCGA-CDR recommends PFI for BRCA and cautions on OS.",
                    },
                    {"role": "user", "content": LIVE_DECISION},
                ],
                "current_actor": "Dr. Chen",
            },
        )

        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["source_origin"], "captured_live_context")
        self.assertEqual(result["captured_turn_count"], 2)
        self.assertEqual(result["selected_evidence"]["evidence_quote"], LIVE_DECISION)
        self.assertTrue(
            result["selected_evidence"]["source_session_id"].startswith("LIVE-")
        )
        self.assertEqual(result["record_template"]["author"], "Dr. Chen")

    def test_rest_score_uses_flat_ui_contract(self) -> None:
        response = self.client.post(
            "/knowledge/score",
            json={
                "prior_samples": ["disbelieve", "uncertain", "uncertain"],
                "posterior_samples": ["believe", "strongly_believe", "believe"],
            },
        )
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertGreater(result["posterior_mean"], result["prior_mean"])
        self.assertIn("bayesian_surprise_bits", result)

    def test_rest_approve_then_recall(self) -> None:
        approved = self.client.post(
            "/knowledge", json={"candidate": payload(), "approved_by": "Dr. Chen"}
        )
        recalled = self.client.post(
            "/knowledge/recall",
            json={"query": "TP53 patient", "scope": {"disease": "BLCA"}},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(recalled.status_code, 200)
        self.assertEqual(recalled.json()[0]["id"], approved.json()["id"])
        self.assertIn("retrieval_components", recalled.json()[0])

    def test_rest_rejects_unapproved_candidate(self) -> None:
        candidate = payload()
        response = self.client.post(
            "/knowledge", json={"candidate": candidate, "approved_by": ""}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("approved_by", response.json()["detail"])

    def test_rest_finds_expert_with_calibrated_evidence(self) -> None:
        approved = self.client.post(
            "/knowledge", json={"candidate": payload(), "approved_by": "Dr. Chen"}
        )
        response = self.client.post(
            "/experts/find",
            json={"topic": "TP53 patient inference", "scope": {"disease": "BLCA"}},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.assertEqual(result["experts"][0]["display_name"], "Dr. Chen")
        self.assertEqual(result["experts"][0]["confidence"], "low")
        self.assertEqual(
            result["active_investigators"][0]["display_name"], "Dr. Chen"
        )
        self.assertFalse(result["active_investigators"][0]["signal_is_expertise"])
        self.assertGreater(
            result["experts"][0]["score_components"]["investigation_activity_bonus"],
            0.0,
        )
        self.assertIn("sources searched", result["message"])

    def test_rest_rejects_reviewer_identity_inside_model_candidate(self) -> None:
        candidate = payload()
        candidate["approved_by"] = "Dr. Model"
        response = self.client.post(
            "/knowledge", json={"candidate": candidate, "approved_by": "Dr. Chen"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("separate approval event", response.json()["detail"])


if __name__ == "__main__":
    unittest.main()
