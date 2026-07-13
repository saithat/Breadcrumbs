# Breadcrumbs

An internal research-memory layer for Owkin's K Pro AI-scientist platform: before a researcher
runs a hypothesis, Breadcrumbs checks — internally first — whether someone in the org already
explored it (including abandoned attempts), then whether the published world has. Full pitch,
demo script, and role breakdown:
[`References/Breadcrumbs-v2.pdf`](References/Breadcrumbs-v2.pdf) (newest;
[`Breadcrumbs.pdf`](References/Breadcrumbs.pdf) is the earlier version, kept for provenance).

## Spec-driven development — start here

**The specs are the source of truth, not the PDF.** Before writing code, read
[`AGENTS.md`](AGENTS.md), then your feature under `specs/features/<feature-id>/`. Four features,
one per owner, so the team works in parallel without colliding:

| Feature | Owns |
|---|---|
| [`graph-store`](specs/features/graph-store/) | The SQLite single source of truth |
| [`research-memory-tools`](specs/features/research-memory-tools/) | The MCP server and its tools — the moat |
| [`survival-analysis`](specs/features/survival-analysis/) | TCGA slice → survival stratification → finding object |
| [`demo-flow`](specs/features/demo-flow/) | The two-session demo, the wiki, the video |

Each feature folder holds `spec.tech.md` (what to build, with `file:line` cites and honest
`not-built` / `gap` statuses), `components.md` (the ordered registry), `feature.json` (the machine
instance), `scenarios.json`, `evidence.json` (a feature is **done only when evidenced**), and
`review-queue.md` (open divergences — an open `error` row blocks completion).

```powershell
python scripts/setupref_validate.py     # component parity + evidence gate; run before you push
python -m pytest tests/
```

Methodology ported from AgenticFlow's `setupref`. The repo is self-contained: `.spec/` carries its
own schemas and templates, and the validator is a Python port so this stays a Node-free repo.

## Repo map

- **`specs/`** — the four feature specs. Start here.
- **`.spec/`** — repo-local setupref config, schemas, templates, and pointer evidence.
- **`ingestion/`** — pulls real K Pro chat sessions (prompts + answers) into a local SQLite
  store. This is the source material the graph is built from. See
  [`ingestion/README.md`](ingestion/README.md) for full setup and usage.
- **`schema/`** — the SQLite schema (`graph_schema.sql`) for the raw chat store and the
  findings graph, plus a worked example of a reviewed finding-extraction file
  (`example_finding_extraction.json`).
- **`demo/`** — sample Session 1 / Session 2 conversation transcripts matching the pitch's
  demo script, for rehearsal or driving a thin chat UI.
- **`src/breadcrumbs/`** — the MCP server exposing the shared SQLite findings graph to agents.
- **`ui/`** — the Breadcrumbs demo surface: a Next.js chat UI (sidebar history, retrace chat,
  live trail graph). Runs standalone on a seeded mock, and points at the real backend via one
  env var. See [`ui/README.md`](ui/README.md).
- **`scripts/`** — `setupref_validate.py`, the spec-tree gate.

## Breadcrumbs MCP server

The MCP server is a thin adapter over the same `ingestion/breadcrumbs.db` used by the ingestion
pipeline. It does not create a second database or schema. It exposes ten tools:

- `check_duplication(hypothesis_text, limit)` searches the internal findings graph and returns the
  UI-compatible `match | open` contract without calling an external literature service.
- `write_finding(record)` validates and inserts one reviewed finding using
  `ingestion/write_findings.py`.
- `recall_findings(query, limit)` retrieves related findings and their graph edges.
- `render_wiki(finding_ids, title)` generates a cited, read-only Markdown view of the graph.
- `read(column, value)` performs an allowlisted, parameterized equality query against the
  `findings` table.
- `prepare_memory_diff(...)` takes a host-inferred proposition, rationale, and scope from ordinary
  scientific dialogue. The agent passes only the exact relevant recent turns; Breadcrumbs stores
  them as an idempotent content-addressed source snapshot, selects exact supporting evidence, and
  returns deterministic prior/posterior packets plus approved model/run provenance. The researcher
  is not asked to sync or supply transcript text, IDs, quotes, samples, a model, or a run ID. Source
  capture does not create approved knowledge.
- `score_surprise(...)` fits before/after Beta beliefs from repeated fixed-label judgments and
  reports belief shift, `KL(posterior || prior)`, entropy change, and optional action divergence in
  bits. The same samples always produce the same result.
- `write_knowledge(record, approved_by)` persists a decision, constraint, exception, abandoned approach, or
  belief revision only after named human approval supplied as a separate tool argument. The
  evidence quote must occur verbatim in its ingested source message; approved aliases and typed
  applicability conditions are optional; all metrics and action deltas are recomputed server-side.
- `recall_knowledge(...)` fuses a local SQLite FTS5/BM25 index with exact-cosine dense retrieval
  from a pinned 384-dimensional ONNX model, then adds deterministic field coverage and
  condition-aware scope scoring. Vectors stay inside the local SQLite store with their model and
  content hash. Unknown inferred scope fields do not erase candidates;
  `strict_scope=true` is available for explicit exact-subset filtering. Superseded patches are
  hidden by default but remain available for audit.
- `find_experts(...)` aggregates source-linked authored knowledge and findings across canonical
  provisional people. It caps repeated evidence per session, keeps abandoned work, excludes
  review-only identities, and returns calibrated confidence plus the supporting artifacts rather
  than asserting that somebody is definitively the organization's expert. Named researchers'
  initial session questions are retrieved separately as `active_investigators`; this weak activity
  signal can modestly enrich an already-demonstrated expert's score but never creates expertise by
  itself.

Session identity is provenance-backed. `session_identity_candidates` records accepted direct
`chat_sessions.researcher` metadata separately from proposed artifact-author and exact-question
evidence, with canonical JSON and a SHA256 digest. Proposed candidates never rewrite session
ownership or create `person_investigations`; production deployments should supply the authenticated
organization actor during ingestion rather than infer identity from writing style.

Install and run over stdio:

```bash
uv sync
.venv/bin/breadcrumbs-mcp
```

Run over Streamable HTTP:

```bash
BREADCRUMBS_TRANSPORT=http .venv/bin/breadcrumbs-mcp
```

The HTTP MCP endpoint is `http://127.0.0.1:8000/mcp`; health is available at `/health`.
The backend exposes equivalent REST seams at `/check_duplication`, `/knowledge/prepare`,
`/knowledge/score`, `/knowledge`, `/knowledge/recall`, and `/experts/find`. The current browser demo
uses only the duplication seam; interaction knowledge and evidence-backed expertise remain agent
host capabilities. The checked public contract is generated at `schema/mcp_contracts.schema.json`.
Set `BREADCRUMBS_DB` to override the default `ingestion/breadcrumbs.db` path. Dense retrieval is
enabled by default with `BAAI/bge-small-en-v1.5`; set `BREADCRUMBS_EMBEDDINGS=0` to disable it or
`BREADCRUMBS_EMBEDDING_MODEL` to an explicitly reviewed local FastEmbed model. The first start
downloads the public model; subsequent inference is local to the host or organization.

The MCP accepts `created_at` and `source_session` as read aliases for the physical
`timestamp` and `source_session_id` columns. Writes use the reviewed extraction shape in
`schema/example_finding_extraction.json`; `id` and `created_at` are optional for MCP writes.

The server publishes initialization and tool guidance telling agents when to read and write,
how to prepare an interaction-grounded Memory Diff without asking the researcher for provenance
plumbing, how to summarize confirmed/in-progress/abandoned work, and how to avoid novelty or
causality overclaims. For proactive use in Claude, upload the intent-named skill under
`skills/check-internal-biomedical-research-memory/`.

## Get the chat ingestor running

`.env` already has the Owkin credentials the ingestor needs — nothing to configure beyond
installing dependencies.

```powershell
python -m pip install -r ingestion/requirements.txt
python -m playwright install chromium
python ingestion/capture_session.py                 # one-time: log in, save session
python ingestion/ingest_chat.py --recent             # pull recent chats into SQLite
sqlite3 ingestion/breadcrumbs.db "SELECT session_id, role, substr(content,1,80) FROM chat_messages;"
```

Full detail (single-chat ingestion, recovery from a saved HAR/JSON, troubleshooting): see
[`ingestion/README.md`](ingestion/README.md).

## Writing findings into the graph

Ingestion only stores raw chat turns — it does not call an LLM or write graph findings
automatically. To add a reviewed finding, write a JSON file shaped like
[`schema/example_finding_extraction.json`](schema/example_finding_extraction.json) (findings +
typed edges, `source_session_id` pointing at an ingested chat) and run:

```powershell
python ingestion/write_findings.py path/to/your_findings.json
```

## Run the UI

```bash
cd ui
npm install
npm run dev            # http://localhost:3000
```

Works standalone out of the box against a seeded mock. To point it at the real backend, set
`BREADCRUMBS_MCP_URL` in `ui/.env.local` (see [`ui/README.md`](ui/README.md) for the expected
response shape).
