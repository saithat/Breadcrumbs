# AGENTS.md — Breadcrumbs

Breadcrumbs captures decisions and other tacit knowledge from conversations, gated by explicit
human approval, and recalls it for agents over MCP and REST. Human-facing usage is in `README.md`;
module docstrings describe each part of the code.

## Product invariants

- Conversation messages are provenance, not approved knowledge.
- `write_knowledge` is the only approval-gated knowledge write path.
- Evidence quotes must occur exactly in the linked source message.
- Abandoned approaches remain first-class and require a reason.
- Corrections append through `supersedes_id`; do not rewrite history silently.
- Recall must preserve scope, conditions, sources, and drift warnings.
- People results must label their source relationship and must not assert expertise.
- Never fabricate evidence, identity, metrics, or organizational conclusions.

## Boundaries

- Core is capture, approval, surprise scoring, recall, and people context. Literature search,
  domain analysis, project management, and transcript playback belong to hosts or adapters.
- Provider-specific parsing stays outside `src/breadcrumbs/`; adapters emit the generic
  conversation shape in `ingestion/models.py`.
- `knowledge_items` is the durable memory. Full-text vectors and people are derived
  and must stay rebuildable from it and the source conversations.
- Any future dataset export (evaluation, training, portable memory) must be a separate, consented
  pipeline that never weakens provenance or approval in the operational store.

## Working rules

- Keep the MCP and REST paths behaviorally aligned; both take the models in
  `src/breadcrumbs/contracts.py`. Avoid passing `dict[str, Any]` across module boundaries.
- PostgreSQL is the only database. Never add an embedded or file-backed fallback.
- Run integration tests only against a dedicated PostgreSQL test database, never production.
- Do not commit conversation exports, credentials, or transcript renderings.
- Run `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run mypy`.
- Keep documentation to `README.md` (people) and this file (agents); do not add other docs.
