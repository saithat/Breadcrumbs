# AGENTS.md — Breadcrumbs (repo root)

Breadcrumbs is an internal research-memory layer for Owkin's K Pro. Before a researcher runs a
hypothesis, it checks — **internally first** — whether someone in their own org already explored it,
*including abandoned attempts*, and only then whether the published world has.

This repo follows the setupref spec-driven methodology. **The specs are the source of truth**, not
the pitch PDF and not this file. `References/Breadcrumbs-v2.pdf` is the newest pitch and the origin
of the specs; where the two disagree, the spec wins and the divergence belongs in a
`review-queue.md`.

## Before any substantive work

1. Read `.spec/repo.json` — layers, `data_classification`, `specConfig`.
2. Read the feature you're working on under `specs/features/<feature-id>/`: `spec.tech.md`,
   `components.md`, `feature.json`, `scenarios.json`, `evidence.json`, `review-queue.md`.
3. Honor **single-spec parity**: the same component IDs, in the same order, across `spec.tech.md`,
   `components.md`, and `feature.json`. Run `python scripts/setupref_validate.py` before you push.
4. Nearest `AGENTS.md` wins — a layer folder's `AGENTS.md` overrides this one for files beneath it.

The four features and their owners:

| Feature | Owns | Layers |
|---|---|---|
| `graph-store` | The SQLite single source of truth | contract, database |
| `research-memory-tools` | The MCP server and its tools — **the moat** | contract, backend |
| `survival-analysis` | TCGA slice → survival stratification → finding object | backend |
| `demo-flow` | The two-session demo, the `ui/` chat UI, the wiki, the video | frontend |

Behavior flows contract → database → backend → frontend. Keep them in parity.

**The demo surface is `ui/`** — the Next.js chat UI, which already runs standalone against a seeded
mock and points at the real backend via `BREADCRUMBS_MCP_URL`. Claude Desktop wiring is optional.

**Before you write `mcp_server/contracts.py`, read `ui/lib/data.ts`.** The `check_duplication`
response shape is already defined there in TypeScript and the UI is built against it. A Python
contract that disagrees breaks the demo the moment the UI is pointed at the real server.

## The Breadcrumbs rules — these are the product, not style preferences

**Calibrated language, always.** The system reports *"no prior work found in [sources]"* and names
the sources it actually searched. It **never** says *"this is novel."* It cannot know that. One
overclaim on stage costs more than any missing feature, and this is enforced by an executable check
(`REQ-006` in `research-memory-tools`) rather than left to good intentions.

**Internal research memory is the MCP boundary.** A duplication check queries the org's own graph
for prior findings and does not call an external literature source. General literature research is
the host agent's responsibility, not a Breadcrumbs tool. This separation is asserted in a test.

**Abandoned work is a first-class result.** An abandoned finding is not a failure to be filtered
out; it carries a `reason` and it is the most valuable row type in the store. Surfacing it is the
one thing the published-record tools structurally cannot do, because failures never reach the
published record. Never rank an abandoned finding below confirmed work merely for being abandoned.

**The graph is authoritative; the wiki is a view.** The SQLite store is the single source of truth.
The wiki is generated, read-only, one-directional — it is never edited back into the store.

**Writes pass a human gate.** Findings do not land in the graph because a model said so. They pass
validation — abandoned requires a reason, the category must be registered, the source session must
exist. If you add a second write path, it uses the same gate, or the gate is worthless.

**Never fabricate a number.** This is a research-integrity product. A placeholder effect size shown
on stage as though it were real would be disqualifying. If the analysis has not run, say so.

## Working rules

**Done means evidenced.** A feature is complete only when its `evidence.json` has `complete: true`,
every item is `satisfied: true`, and its `review-queue.md` has no open `error` row. The validator
enforces all three.

**Evidence is pointer-based.** Don't paste logs into specs. Store them under
`.spec/evidence/<feature-id>/<run-id>/` and record repo-relative paths and short summaries in
`evidence.json`. (`.gitignore` has an explicit exception so these logs are committed despite `*.log`.)

**Data boundary.** `data_classification` is `public`, as confirmed by the project owner on
2026-07-12. Public research content may be routed to the pinned retrieval and elicitation models;
model/run provenance must still be recorded. Authentication material remains secret: never expose
or commit `ingestion/.secrets/`, cookies, tokens, or live session credentials.

**Never commit `ingestion/.secrets/`.** It holds a live authenticated K Pro session — a real
credential. `ingestion/breadcrumbs.db` and `ingestion/transcripts/` *are* tracked on purpose, so the team
shares one graph store without each re-scraping.

**GitKB (`.kb/`) is this repo's own project memory — not the Breadcrumbs product graph.** It is a
separate tool (`git-kb`) used to track tasks, context, and notes about *building this repo*, distinct
from `ingestion/breadcrumbs.db` (the product's research-findings graph described above). `.kb/config.toml`,
`.kb/AGENTS.md`, and `.kb/store/` are tracked and pushed on `main` on purpose, so a teammate gets a
working KB on `git pull` — **do not run `git-kb init` again**; that would overwrite the shared config.
Only `.kb/.cache/` (a regeneratable local index) and `.kb/workspace(s)/` (your own in-progress
checkout) are gitignored and local-only.

There is no native Windows build of `git-kb` — it lives in WSL Ubuntu. Run every command through the
bridge from a Windows shell:
```powershell
wsl -d Ubuntu -- bash -lc "cd '/mnt/c/Users/<you>/Documents/Github/Biohack' && git-kb <command>"
```
Useful commands: `git-kb status` (workspace state), `git-kb list` (documents), `git-kb board`
(kanban view of tasks), `git-kb context` (bootstrap-context bundle for an agent session), `git-kb
create --type <type> --slug <slug> --title <title>` (new doc), `git-kb commit` (workspace → KB
commit). `git-kb doctor` / `git-kb info` are CLI-only (no MCP tool equivalent). Cloud auth
(`git-kb auth login`, `whoami`, remote `push`/`pull`) is not wired up in this repo yet — it hit a
DBus/secret-service error under headless WSL; local use (create/list/commit/status) does not need it.

**Never edit a SQLite CHECK constraint in place.** `connect()` runs `executescript(graph_schema.sql)`
with `CREATE TABLE IF NOT EXISTS` on every connect, so editing the schema file does nothing to the
committed `breadcrumbs.db`. Changing a CHECK requires a table rebuild — and `finding_edges` cascades on
delete from `findings`, so a rebuild with foreign keys enabled will silently destroy every edge.
See `specs/features/graph-store/spec.tech.md`, BC-GRAPH-006.

**Repo-local source of truth.** Use this repo's own `.spec/`, `specs/`, and `AGENTS.md`. Do not
depend on a path back to AgenticFlow except when deliberately upgrading the setupref version.
