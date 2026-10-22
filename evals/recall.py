"""Recall eval: does the expected approved item come back for realistic agent queries?

Loads the synthetic corpus in ``recall_cases.json`` into the dedicated test database and reports
hit@1, hit@5, and MRR@10 per retrieval configuration and query category:

    TEST_DATABASE_URL=postgresql://.../breadcrumbs_test uv run python -m evals.recall

"first query" uses only each case's first query; "rephrasings" passes all of them in one call.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from breadcrumbs.contracts import KnowledgeRecord, RecallRequest
from breadcrumbs.store import BreadcrumbsStore
from ingestion.models import Conversation
from ingestion.store import connect, upsert_session
from tests.postgres import reset_database

CASES = json.loads(Path(__file__).with_name("recall_cases.json").read_text())
type Search = Callable[[list[str]], list[str]]


def load(store: BreadcrumbsStore) -> dict[str, str]:
    """Store each item's rationale as a source turn and approve the item citing it."""

    ids: dict[str, str] = {}
    for slug, item in CASES["items"].items():
        with connect(store.database_url) as connection:
            upsert_session(
                connection,
                Conversation.model_validate(
                    {
                        "id": slug,
                        "source_uri": f"eval://{slug}",
                        "messages": [{"role": "user", "content": item["rationale"]}],
                    }
                ),
            )
        record = KnowledgeRecord.model_validate(
            {
                **item,
                "evidence_quote": item["rationale"],
                "source_message_id": f"{slug}:0",
                "prior_samples": ["uncertain"] * 3,
                "posterior_samples": ["believe"] * 3,
                "elicitation_model": "claude-sonnet-5",
                "elicitation_run_id": "eval",
                "author": "Eval",
            }
        )
        ids[slug] = store.write_knowledge(record, "Eval").id
    return ids


def recall(store: BreadcrumbsStore, *, rephrasings: bool) -> Search:
    def search(queries: list[str]) -> list[str]:
        request = RecallRequest(queries=queries if rephrasings else queries[:1], limit=10)
        return [item.id for item in store.recall_knowledge(request)]

    return search


def score(search: Search, ids: dict[str, str]) -> dict[str, dict[str, float]]:
    totals: dict[str, list[float]] = {}
    for case in CASES["cases"]:
        results = search(case["queries"])
        expected = ids[case["expected"]]
        rank = results.index(expected) + 1 if expected in results else None
        for group in ("all", case["category"]):
            row = totals.setdefault(group, [0, 0, 0, 0])
            row[0] += rank == 1
            row[1] += rank is not None and rank <= 5
            row[2] += 1 / rank if rank else 0
            row[3] += 1
    return {
        group: {"hit@1": h1 / n, "hit@5": h5 / n, "mrr@10": mrr / n, "n": n}
        for group, (h1, h5, mrr, n) in totals.items()
    }


def main() -> None:
    database_url = reset_database()
    store = BreadcrumbsStore(database_url)
    ids = load(store)
    configurations: dict[str, Search] = {
        "first query": recall(store, rephrasings=False),
        "rephrasings": recall(store, rephrasings=True),
    }
    groups = ["all", "keyword", "agentic", "paraphrase"]
    print(f"{'configuration':<26}" + "".join(f"{g + ' (n)':>22}" for g in groups))
    report: dict[str, Any] = {}
    for name, search in configurations.items():
        metrics = score(search, ids)
        report[name] = metrics
        cells = "".join(
            f"{metrics[g]['hit@1']:>6.2f} {metrics[g]['hit@5']:>5.2f} {metrics[g]['mrr@10']:>5.2f}"
            f" ({metrics[g]['n']:.0f})"
            for g in groups
        )
        print(f"{name:<26}{cells}")
    print("\ncells: hit@1 hit@5 mrr@10 (cases)")


if __name__ == "__main__":
    main()
