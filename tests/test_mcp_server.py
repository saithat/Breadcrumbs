from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from breadcrumbs.contracts import DuplicationResult, Match, contract_schema
from breadcrumbs.store import BreadcrumbsStore
from ingestion.store import connect


def finding(**changes):
    value = {
        "id": "F-118",
        "category": "LUAD-immune",
        "disease": "LUAD",
        "hypothesis_text": "High cytotoxic T-cell infiltration predicts better overall survival in LUAD",
        "entities": ["CD8A", "GZMB", "PRF1", "GZMK"],
        "effect": "HR 0.58 (95% CI 0.41-0.82), p=0.002",
        "status": "confirmed",
        "reason": None,
        "author": "Aisha Rahman",
        "created_at": "2027-01-02T03:04:05+00:00",
        "source_session_id": "sess-2027",
        "source_type": "internal",
    }
    value.update(changes)
    return value


class MCPContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "breadcrumbs.db"
        connection = connect(self.path)
        connection.execute(
            "INSERT INTO chat_sessions(id,url,title,scraped_at,raw_json) VALUES (?,?,?,?,?)",
            ("sess-2027", "https://example.test/chat/sess-2027", "test", "2027-01-01", "{}"),
        )
        connection.commit()
        connection.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def store(self) -> BreadcrumbsStore:
        return BreadcrumbsStore(self.path)

    def test_registration_lists_all_ten_contract_tools(self) -> None:
        old = os.environ.get("BREADCRUMBS_DB")
        os.environ["BREADCRUMBS_DB"] = str(self.path)
        try:
            sys.modules.pop("breadcrumbs.server", None)
            server = importlib.import_module("breadcrumbs.server")
            instructions = server.BREADCRUMBS_INSTRUCTIONS.casefold()
            self.assertNotIn("nov" + "el", instructions)
            self.assertIn("turn-level write-back check", instructions)
            self.assertIn("before research, web lookups, or the next answer", instructions)
            self.assertIn("settled correction is independently reviewable", instructions)
            self.assertLess(
                instructions.index("turn-level write-back check"),
                instructions.index("reading"),
            )
            self.assertIn("do not impose a hard per-session candidate cap", instructions)
            self.assertIn("default batch size of three", instructions)
            self.assertNotIn("Propose no more than three", server.BREADCRUMBS_INSTRUCTIONS)
            host_skill = Path("skills/check-internal-biomedical-research-memory/SKILL.md").read_text(
                encoding="utf-8"
            )
            host_skill_folded = host_skill.casefold()
            self.assertIn("run the write-back check before answering", host_skill_folded)
            self.assertIn("before continuing the scientific analysis", host_skill_folded)
            self.assertIn("settled correction is independently reviewable", host_skill_folded)
            self.assertLess(
                host_skill_folded.index("run the write-back check before answering"),
                host_skill_folded.index("decide when to check"),
            )
            self.assertIn("Do not impose a hard per-session candidate cap", host_skill)
            self.assertIn("default presentation batch size", host_skill)
            self.assertNotIn("propose at most one", host_skill.casefold())
            registered_tools = asyncio.run(server.mcp.list_tools())
            names = [tool.name for tool in registered_tools]
            self.assertEqual(
                names,
                [
                    "check_duplication",
                    "write_finding",
                    "recall_findings",
                    "render_wiki",
                    "read",
                    "prepare_memory_diff",
                    "score_surprise",
                    "write_knowledge",
                    "recall_knowledge",
                    "find_experts",
                ],
            )
            prepare_tool = next(
                tool for tool in registered_tools if tool.name == "prepare_memory_diff"
            )
            self.assertIn("Immediately prepare", prepare_tool.description)
            self.assertIn("user corrects", prepare_tool.description)
            response = TestClient(server.app).post(
                "/check_duplication", json={"hypothesis_text": "unmatched endpoint smoke test"}
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"verdict", "matches", "searched", "markdown"})
        finally:
            if old is None:
                os.environ.pop("BREADCRUMBS_DB", None)
            else:
                os.environ["BREADCRUMBS_DB"] = old

    def test_contract_schema_matches_ui_enums(self) -> None:
        schema_text = json.dumps(contract_schema(), sort_keys=True)
        self.assertIn('"match"', schema_text)
        self.assertIn('"open"', schema_text)
        self.assertIn('"in_progress"', schema_text)
        self.assertIn('"duplicate_of"', schema_text)
        checked_in = json.loads(Path("schema/mcp_contracts.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(checked_in, contract_schema())
        ui_contract = Path("ui/lib/data.ts").read_text(encoding="utf-8")
        for fragment in (
            'export type Status = "confirmed" | "in_progress" | "abandoned"',
            'export type Relationship = "duplicate_of" | "extends" | "related"',
            'verdict: "match" | "open"',
        ):
            self.assertIn(fragment, ui_contract)
        self.assertEqual(
            set(Match.model_fields),
            {"id", "status", "relationship", "hypothesis_text", "effect", "reason", "author", "disease"},
        )
        self.assertEqual(
            set(DuplicationResult.model_fields),
            {"verdict", "matches", "searched", "markdown"},
        )

    def test_internal_graph_match_uses_only_internal_source(self) -> None:
        store = self.store()
        store.write(finding())
        result = store.check_duplication("Does CD8 T cell infiltration improve survival in lung adenocarcinoma?")
        self.assertEqual(result["verdict"], "match")
        self.assertEqual(result["matches"][0]["id"], "F-118")
        self.assertNotIn("external", result)

    def test_semantic_match_distinguishes_unrelated_question(self) -> None:
        store = self.store()
        store.write(finding())
        same = store.check_duplication("Does CD8 T cell infiltration improve survival in lung adenocarcinoma?")
        unrelated = store.check_duplication("Does BRAF alter melanoma response to radiotherapy?")
        self.assertEqual(same["verdict"], "match")
        self.assertEqual(unrelated["verdict"], "open")

    def test_abandoned_result_is_first_class_and_keeps_reason(self) -> None:
        store = self.store()
        store.write(finding())
        store.write(
            finding(
                id="F-093",
                status="abandoned",
                effect="Signal collapsed after adjustment.",
                reason="The effect collapsed after stage adjustment.",
            )
        )
        result = store.check_duplication("CD8 T cell infiltration survival in lung adenocarcinoma")
        abandoned = next(match for match in result["matches"] if match["id"] == "F-093")
        self.assertEqual(abandoned["status"], "abandoned")
        self.assertEqual(abandoned["reason"], "The effect collapsed after stage adjustment.")
        self.assertLess(result["matches"].index(abandoned), 1)

    def test_ui_result_puts_primary_first_then_graph_neighbors(self) -> None:
        store = self.store()
        store.write(finding())
        store.write(
            finding(
                id="F-093",
                disease="BLCA",
                hypothesis_text="H and E morphology alone did not separate response groups",
                entities=["H&E"],
                effect="No robust separator found.",
                status="abandoned",
                reason="The subgroup was underpowered.",
            )
        )
        connection = connect(self.path)
        connection.execute(
            "INSERT INTO finding_edges VALUES (?,?,?,?)",
            ("F-118", "F-093", "extends", "2027-01-03"),
        )
        connection.commit()
        connection.close()
        result = store.check_duplication("CD8 T cell survival in lung adenocarcinoma")
        self.assertEqual([match["id"] for match in result["matches"][:2]], ["F-118", "F-093"])
        self.assertEqual(result["matches"][1]["relationship"], "extends")
        self.assertEqual(result["matches"][1]["status"], "abandoned")

    def test_never_novel_in_user_facing_outputs(self) -> None:
        store = self.store()
        result = store.check_duplication("Unmatched radiomics question")
        wiki = store.render_wiki()
        forbidden = "nov" + "el"
        self.assertNotIn(forbidden, json.dumps(result).lower())
        self.assertNotIn(forbidden, json.dumps(wiki).lower())

    def test_no_external_literature_surface(self) -> None:
        store = self.store()
        result = store.check_duplication("unmatched radiomics question")
        recall = store.recall_findings("unmatched radiomics question")
        self.assertNotIn("external", result)
        self.assertNotIn("literature", recall)
        self.assertEqual(recall["sources_searched"], ["internal Breadcrumbs graph"])

    def test_write_gate_is_shared_with_reviewed_writer(self) -> None:
        store = self.store()
        with self.assertRaisesRegex(ValueError, "requires a reason"):
            store.write(finding(status="abandoned", reason=None))
        with self.assertRaisesRegex(ValueError, "Unknown source session"):
            store.write(finding(source_session_id="missing"))

    def test_cross_indication_recall_uses_signature_context(self) -> None:
        store = self.store()
        store.write(finding())
        result = store.recall_findings("Could the CD8A GZMB signature predict survival in LUSC?")
        self.assertEqual(result["findings"][0]["id"], "F-118")

    def test_render_wiki_is_reproducible_and_cites_findings(self) -> None:
        store = self.store()
        store.write(finding())
        first = store.render_wiki()
        second = store.render_wiki()
        self.assertEqual(first, second)
        self.assertIn("Generated read-only view", first["markdown"])
        self.assertIn("`F-118`", first["markdown"])


if __name__ == "__main__":
    unittest.main()
