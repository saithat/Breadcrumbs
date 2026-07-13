---
id: demo-flow-tech
title: "Demo Flow — technical reference"
type: spec
status: draft
domain: breadcrumbs
audience: engineers, biologists, Breadcrumbs team
parity_of: ./components.md
registry: ./components.md
source: References/Breadcrumbs-v2.pdf
---

# Breadcrumbs / Demo Flow — Technical Reference

*"Write everything toward this."* The demo is the deliverable the judges actually experience, and
the two-session flow is the entire argument:

- **Session 1 — a finding is born.** A researcher asks a survival-stratification question on TCGA
  LUAD. The agent runs it, gets a real result, and writes it to the graph with provenance and a
  calibrated novelty note.
- **Session 2 — the save.** A *different* researcher, in a *new session with no shared context*,
  asks a related question. Instead of cold-starting, the agent surfaces what the org already knows —
  including a teammate's **abandoned** attempt and why it was abandoned.

The punchline: internal recall plus a surfaced abandoned attempt is something the published-record
tools structurally cannot do, because failures never reach the published record.

Component IDs must stay in the same order as `components.md` and `feature.json`.

## DEMO — the two sessions and the surfaces that show them

### BC-DEMO-001 — Seeded graph
- **Behavior:** The graph is pre-loaded with the abandoned finding F-093 (and optionally one
  unrelated confirmed finding, so the graph visibly holds more than just the demo path). F-118 is
  **not** pre-seeded — it is written live in Session 1, and pre-seeding it would make the write path
  a lie.
- **Data:** `schema/seed_findings.json` → `findings`, `finding_edges`.
- **Source:** `schema/seed_findings.json`; loaded via `ingestion/write_findings.py`.
- **Status:** gap — the seed file exists, but F-118's effect fields are still `{{ }}` placeholders
  awaiting the real TCGA run, and there is no one-command seed loader.
- **REQ-001:** A single command loads the seed graph from scratch and F-093 is present with its reason.

### BC-DEMO-002 — Session 1 script
- **Behavior:** The literal chat turns for the "a finding is born" beat, driven live against the MCP
  server. The agent's answer reports the real effect size, states the calibrated novelty note, and
  confirms the finding was written to memory with attribution.
- **Data:** `demo/sample_conversations.md`.
- **Source:** `demo/sample_conversations.md`.
- **Status:** gap — the script exists with `{{ }}` slots for the real numbers.
- **REQ-002:** Session 1 runs live against the server and the finding lands in the graph.

### BC-DEMO-003 — Session 2 script
- **Behavior:** The "save" beat. A new session, a different researcher, nothing in context. The
  agent surfaces the Session 1 finding *and* the abandoned attempt with its reason, adds published
  context, and names what is genuinely still open.
- **Data:** `demo/sample_conversations.md`; reads the graph written by Session 1.
- **Source:** `demo/sample_conversations.md`.
- **Status:** gap.
- **REQ-003:** In a session with no shared context, the agent surfaces both the prior finding and the
  abandoned attempt, and never uses the word "novel".

### BC-DEMO-004 — Demo surface: the Next.js chat UI
- **Behavior:** The demo is driven through `ui/` — a Next.js chat UI with session history, the
  retrace chat, and a live trail graph. It calls the MCP server through
  `ui/app/api/check_duplication/route.ts`, which points at the real backend when
  `BREADCRUMBS_MCP_URL` is set and **degrades to a seeded local mock whenever the backend is unset
  or unreachable** — so the demo cannot go dark on stage.
- **Data:** `ui/lib/data.ts` (seeded trail), `ui/lib/duplication.ts` (the mock, deliberately shaped
  to match the real tool's response exactly).
- **Source:** `ui/app/page.tsx`; `ui/app/api/check_duplication/route.ts`; `ui/lib/duplication.ts`.
- **Status:** gap — the UI is built and runs standalone against the mock; it has not yet been
  pointed at a real MCP server, because there isn't one yet.
- **Note on the divergence from the pitch:** Breadcrumbs-v2 called Claude Desktop the *cleanest*
  surface (the MCP host standing in for K Pro) and the Next.js UI the *fallback*. The team has built
  the fallback and not the Claude Desktop wiring, so the fallback is now the plan of record. Wiring
  Claude Desktop is optional and only worth doing if the server lands with hours to spare — the
  "add-on to K Pro, consumed via MCP" story is stronger when the host is a real MCP host, but a
  demo that works beats a demo that tells a better story.
- **REQ-004:** With `BREADCRUMBS_MCP_URL` pointed at the real server, the UI drives the full flow
  against the live graph — not the mock.

### BC-DEMO-005 — Generated wiki page
- **Behavior:** The read-only Markdown wiki rendered from the graph, shown at the end of the demo as
  the durable artifact: the org's research memory, written by nobody, derived entirely from what the
  team actually did. Carries a banner saying it is generated and never edited back.
- **Data:** rendered from `findings` and `finding_edges` when the live MCP is configured; the
  standalone UI mock remains frontend-owned demo data.
- **Source:** not-built.
- **Status:** not-built.
- **REQ-005:** The wiki page renders from the post-demo graph and cites both demo findings.

### BC-DEMO-006 — Cross-indication graph visual
- **Behavior:** A static visual of the graph showing the LUAD finding, the abandoned attempt, and
  the edges between them — the "memory" made legible in one glance. P2: only if hours remain.
- **Data:** graph export from `findings` + `finding_edges`.
- **Source:** not-built.
- **Status:** not-built.
- **REQ-006:** The visual renders from the post-demo graph with no hand-drawn nodes.

### BC-DEMO-007 — Backup demo video
- **Behavior:** A recorded run of the full two-session flow, under five minutes, mp4 or mov. Recorded
  the night before. **Do not trust the demo gods at 16:00** — and the video is a hard submission
  requirement in its own right, not merely a fallback.
- **Data:** none.
- **Source:** not-built.
- **Status:** not-built.
- **REQ-007:** A sub-five-minute mp4/mov of the complete flow exists before demo day.

### BC-DEMO-008 — Memory Diff
- **Behavior:** A compact review card makes the candidate-generation boundary visible: the proposed
  knowledge kind and proposition, exact supporting quote, belief before/after, Bayesian surprise in
  bits, and structured action change are shown before the researcher can approve or skip it. Save
  calls the same backend human gate as MCP; when the backend is absent the UI does not pretend the
  item was persisted. The checked-in example is visibly illustrative and cannot be approved,
  because its fixed samples are UI test data rather than a logged model elicitation; a live
  candidate becomes writable only with an approved model and elicitation run ID.
- **Data:** one ephemeral candidate in the client; approved rows are written to `knowledge_items`.
- **Source:** `ui/app/memory-diff.tsx`; `ui/app/api/knowledge/route.ts`; `ui/app/page.tsx`.
- **Status:** built-at-parity.
- **REQ-008:** The production UI build includes a Memory Diff review with explicit approve/skip
  actions and no mock-success write path.
