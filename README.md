# Breadcrumbs

Breadcrumbs captures the decisions, constraints, exceptions, abandoned approaches, and belief
revisions that come up in conversations with agents, and surfaces them in later conversations.
Nothing becomes organizational memory without explicit human approval, and every approved item
keeps the exact quote and source message it came from.

## How it works

1. **Capture.** When a conversation changes what someone should believe or do, the agent calls
   `prepare_memory_diff` with the relevant turns and the exact supporting quote. Breadcrumbs stores
   the turns as provenance and returns prior/posterior elicitation prompts.
2. **Score.** The agent samples each prompt a few times and calls `score_surprise`. Surprise follows
   [Evidence-Informed LLM Beliefs for Continual Scientific Discovery](https://arxiv.org/abs/2606.29182):
   the prior already includes up to 25 related approved items, so knowledge the organization
   already has is not surprising. A candidate is `surprising` when the mean belief moves by at
   least 0.3; `bayesian_surprise_bits` is the KL divergence between the fitted Beta beliefs.
3. **Approve.** A person reviews the Memory Diff, and the agent calls `write_knowledge`.
   Corrections point at the item they replace with `supersedes_id`, so history stays auditable.
4. **Recall.** Later conversations call `recall_knowledge` with one or more queries. Each query is
   ranked with PostgreSQL full-text search, and the rankings are fused with reciprocal-rank
   fusion, so rephrasings and synonyms from the agent stand in for semantic search. Quoted phrases
   must match exactly. Results report which
   queries matched, scope compatibility, and source drift.
   `find_people_with_context` lists who authored, approved, or started a relevant conversation.

The same operations are available as REST endpoints under `/knowledge/*` and `/people/context`.

## Run

```bash
docker compose up -d postgres
export DATABASE_URL=postgresql://breadcrumbs:breadcrumbs@127.0.0.1:5432/breadcrumbs
uv sync
uv run breadcrumbs-mcp                                # MCP over stdio
BREADCRUMBS_TRANSPORT=http uv run breadcrumbs-mcp     # MCP at :8000/mcp, REST, /health
```

If port 5432 is taken, set `BREADCRUMBS_POSTGRES_PORT` for Compose and match it in the URL.
The schema is not migrated, so after a schema change recreate the database with
`docker compose down -v`.

## Import conversations

Existing conversations can be imported as provenance (they are never treated as approved
knowledge). Each file holds a session array, or `{"sessions": [...]}`, in the shape of
[`schema/example_conversations.json`](schema/example_conversations.json):

```bash
uv run breadcrumbs-import export.json
```

Re-importing a session replaces its messages (IDs are `<session-id>:<seq>`) but cannot delete a
message that approved knowledge cites. If the cited text changed, recall flags `source_drifted`.

## Development

```bash
uv sync --dev
TEST_DATABASE_URL=postgresql://breadcrumbs:breadcrumbs@127.0.0.1:5432/breadcrumbs_test uv run pytest
uv run ruff check . && uv run ruff format --check . && uv run mypy
```

Database tests only run against `TEST_DATABASE_URL`, whose database name must end in `_test`;
they never fall back to `DATABASE_URL`. Compose creates `breadcrumbs_test` for you.

`uv run python -m evals.recall` (with `TEST_DATABASE_URL` set) scores recall with one query and
with rephrasings on the synthetic corpus in `evals/recall_cases.json`. Add cases from real
conversations as they appear; the synthetic set is small and was written alongside the items, so
it overstates word overlap. If real paraphrases are missed even with rephrasings, embeddings
(pgvector) are the next step. After changing
`src/breadcrumbs/contracts.py`, regenerate `schema/mcp_contracts.schema.json` with
`uv run python -m breadcrumbs.contracts`.
