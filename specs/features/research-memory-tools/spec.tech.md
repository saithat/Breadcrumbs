---
id: research-memory-tools-tech
title: "Research Memory Tools — technical reference"
type: spec
status: draft
domain: breadcrumbs
audience: engineers, Breadcrumbs team
parity_of: ./components.md
registry: ./components.md
source: ui/lib/data.ts and ui/lib/duplication.ts (demo contract); References/Breadcrumbs-v2.pdf (background)
---

# Breadcrumbs / Research Memory Tools — Technical Reference

**The deliverable *is* an MCP server.** This feature is the moat. Everything else in the repo exists
to feed it or to show it off.

Two rules from Breadcrumbs-v2 govern every component below, and they are the whole product:

1. **Internal research memory only.** A duplication check queries the org's own graph — including
   *abandoned* attempts — and does not call an external literature source. The host agent handles
   general literature research; Breadcrumbs supplies the organizational evidence it lacks.
2. **Calibrated language, always.** The system says *"no prior work found in [sources]"*. It never
   says *"this is novel"*. It cannot know that, and claiming it is the exact failure mode that makes
   researchers stop trusting the tool.

Findings are extracted **in the host** (K Pro / Claude Desktop) before these tools are called. For
interaction knowledge, the host may additionally pass only the relevant recent turns to
`prepare_memory_diff`; Breadcrumbs stores that exact source snapshot before constructing the diff.
Authoritative finding and knowledge writes still pass a human confirm gate.

Component IDs must stay in the same order as `components.md` and `feature.json`.

## MCP — the server and its tools

### BC-MCP-001 — Server bootstrap and tool registration
- **Behavior:** A Python MCP server exposing the ten tools below, connected to Claude Desktop as the
  host (the host stands in for K Pro in the demo). Opens the graph store read-write and surfaces
  errors rather than failing silently mid-demo.
- **Data:** reads/writes `ingestion/breadcrumbs.db` through the existing store module.
- **Source:** `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-001:** The server starts, registers all ten tools, and Claude Desktop lists them.

### BC-MCP-002 — Tool contracts
- **Behavior:** Typed input/output models for every tool, exported to a checked-in JSON Schema. This
  is the boundary the host codes against, and it is where the status and edge vocabularies are
  pinned so they cannot drift from the SQL CHECK constraints.
- **Data:** the shared vocabulary — status `confirmed | in-progress | abandoned | open`, edge
  `duplicate_of | extends | related | contradicts`, duplication verdict `match | open`.
- **Source:** `src/breadcrumbs/contracts.py`; `schema/mcp_contracts.schema.json`; `ui/lib/data.ts`.
- **Status:** built-at-parity.
- **REQ-002:** The vocabularies in the tool contracts equal those in the live database DDL.

### BC-MCP-003 — `check_duplication`, stage 1: internal retrieval
- **Behavior:** Fast local retrieval over the findings graph to produce candidate matches for a
  new hypothesis. The tool has no external literature dependency or fallback.
- **Data:** `findings`, `finding_edges`.
- **Source:** `src/breadcrumbs/store.py:check_duplication`.
- **Status:** built-at-parity.
- **REQ-003:** Duplication output is derived only from the internal graph and carries no external
  literature result field.

### BC-MCP-004 — `check_duplication`, stage 2: semantic match and verdict
- **Behavior:** Normalized disease/gene aliases and local concept overlap route a question to the
  strongest internal marker, then append its graph neighbors. The UI boundary returns `match` or
  `open` exactly as `ui/lib/data.ts` defines.
- **Data:** reads `findings` and `finding_edges`.
- **Source:** `src/breadcrumbs/store.py`; `ui/lib/duplication.ts`.
- **Status:** built-at-parity.
- **REQ-004:** Two phrasings of the same hypothesis (LUAD cytotoxic infiltration vs. lung-adeno
  CD8 T-cell infiltration) return `match`; an unrelated hypothesis returns `open`.

### BC-MCP-005 — Abandoned-result surfacing
- **Behavior:** Abandoned prior work is a **first-class result type**, not a filtered-out failure.
  When recall or duplication surfaces an abandoned finding, it is returned with its `reason`
  attached and is never ranked below confirmed work merely for being abandoned. This single
  component is the difference between Breadcrumbs and every published-record tool on the market.
- **Data:** `findings` where `status = 'abandoned'`.
- **Source:** `src/breadcrumbs/store.py:_ui_matches`.
- **Status:** built-at-parity.
- **REQ-005:** A query related to F-093 returns it, flagged abandoned, with its reason text.

### BC-MCP-006 — Calibrated-language layer
- **Behavior:** Every user-facing string the server emits is calibrated. It reports *"no prior work
  found in [sources]"* and names the sources actually searched. The word "novel" is **hard-blocked**
  in tool output.
- **Data:** none.
- **Source:** `src/breadcrumbs/store.py:_duplication_markdown`; `tests/test_mcp_server.py`.
- **Status:** built-at-parity.
- **REQ-006:** No tool output contains the word "novel". Enforced by an executable check, because a
  guideline nobody tests is a guideline nobody keeps.

### BC-MCP-007 — `write_finding`
- **Behavior:** Create or update a finding plus its edges, append-only and provenance-tagged (who,
  when, what data, what effect). Passes the same validation gate the reviewed-write path already
  enforces — abandoned requires a reason, category must be registered, source session must exist.
- **Data:** `findings`, `finding_edges`.
- **Source:** `src/breadcrumbs/server.py:write_finding`; `src/breadcrumbs/store.py:write`, which
  reuses `ingestion/write_findings.py:write_payload`.
- **Status:** built-at-parity.
- **REQ-007:** A finding written through the tool is subject to the identical validation as one
  written through the reviewed-write path.

### BC-MCP-008 — `recall_findings`
- **Behavior:** Given a new question, return semantically-related prior findings and their
  connections, retrievable by topic, entity, or context. This is the read path that makes Session 2
  of the demo work.
- **Data:** `findings`, `finding_edges`.
- **Source:** `src/breadcrumbs/store.py:recall_findings`.
- **Status:** built-at-parity.
- **REQ-008:** A LUSC question about a signature previously tested in LUAD recalls the LUAD finding.

### BC-LIT-001 — Host-managed literature research
- **Behavior:** General literature research remains with the host agent. The Breadcrumbs MCP does
  not call, cache, or claim results from an external literature service.
- **Data:** none.
- **Source:** deliberately absent from `src/breadcrumbs`.
- **Status:** descoped.
- **REQ-009:** The MCP has no external literature client, cache table, or literature result field.

### BC-MCP-009 — `render_wiki`
- **Behavior:** Generate a Markdown wiki page from the graph. The wiki is a **generated,
  one-directional, read-only view** — it is never edited back into the store, and it carries a
  banner saying so. Anything authoritative lives in the graph.
- **Data:** reads `findings`, `finding_edges`.
- **Source:** `src/breadcrumbs/store.py:render_wiki`.
- **Status:** built-at-parity.
- **REQ-010:** The rendered page cites the findings it was generated from and is reproducible from
  the graph alone.

### BC-MCP-010 — `score_surprise`
- **Behavior:** Quantifies how much a conversation changed a candidate belief without asking a
  model for an opaque importance score. The host supplies repeated categorical belief judgments
  from before and after the cited interaction. The tool maps the fixed five-label scale to
  `[0, .25, .5, .75, 1]`, fits Beta distributions with documented pseudo-counts, and returns prior
  and posterior means, signed/absolute belief shift, `KL(posterior || prior)` in bits, entropy
  change, and optional Jensen-Shannon divergence for sampled actions. Given the same samples the
  output is identical. Prior/posterior sample counts must match so Monte Carlo replicate count
  cannot masquerade as evidence or certainty.
- **Data:** no persistence; only structured samples enter the deterministic calculator.
- **Source:** `src/breadcrumbs/surprise.py`; `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-012:** Fixed fixtures produce stable belief-shift, Bayesian-surprise, entropy, and action
  divergence values without third-party numerical dependencies.

### BC-MCP-011 — `write_knowledge`
- **Behavior:** Persists at most one already-reviewed candidate per call. `approved_by` is mandatory;
  the evidence quote must occur verbatim in the referenced chat message; the source session is
  derived from that message; and all surprise fields are recomputed from the supplied samples
  rather than trusted from the host. `abandoned` requires a reason. A revision may point to the
  approved item it supersedes, preserving an append-only patch history.
- **Data:** writes `knowledge_items` only after validation.
- **Source:** `src/breadcrumbs/store.py`; `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-013:** Missing approval, an unknown source message, a non-verbatim quote, and an abandoned
  item without a reason are each rejected without changing the database. The write also requires a
  logged elicitation run from a model permitted by `.spec/repo.json`.

### BC-MCP-012 — `recall_knowledge`
- **Behavior:** Retrieves only approved internal knowledge with a local, constraint-aware hybrid
  ranker. SQLite FTS5/BM25, deterministic field-weighted token coverage, and dense embeddings from
  the pinned `BAAI/bge-small-en-v1.5` FastEmbed model generate candidates from
  the proposition, rationale, approved aliases, typed conditions, scope, action, reason, and source
  quote. Structured scope is an applicability feature by default, not a model-invented hard gate:
  exact facet matches and satisfied numeric conditions boost a patch; approved condition-field
  aliases map host wording such as `pH` to a canonical field such as `buffer_pH`; contradictions lower it; and
  unknown keys do not remove it. `strict_scope=true` preserves exact-subset filtering for a caller
  that explicitly needs it. Each result reports the BM25/token components and compatible,
  incompatible, and unknown scope fields. Bayesian surprise remains only a tie-breaker; it is not a
  relevance or importance score. BM25 and exact-cosine dense rankings are fused with reciprocal
  rank fusion; exact cosine is deliberate at this store size so an approximate index cannot trade
  away recall. Dense-only candidates must meet the pinned `0.55` cosine floor. Query and passage
  embeddings are computed locally in the organization's runtime; only public model weights are
  downloaded. Every result reports the model, dense similarity/rank, BM25 rank, field coverage,
  fusion score, and scope compatibility. Superseded rows are excluded by default but remain available for
  audit. A match to historical wording resolves forward to the active patch head and reports
  `matched_via_history`, so corrections do not erase old retrieval aliases.
- **Data:** reads `knowledge_items`, `knowledge_fts`, and `knowledge_embeddings`; returns source identifiers, scoring inputs and metrics,
  action deltas, and patch links.
- **Source:** `src/breadcrumbs/store.py`; `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-014:** A query with an inferred unknown scope key and a numeric value satisfying a stored
  typed condition returns the relevant active approved item; an approved alias is searchable;
  dense-only paraphrases are candidates; the BM25 and dense ranks are fused and exposed;
  `strict_scope=true` retains exact-subset behavior; superseded items remain hidden by default and historical wording resolves to the active patch.

### BC-MCP-013 — `find_experts`
- **Behavior:** Answers expertise questions by retrieving topic-relevant approved knowledge and
  reviewed findings, resolving their author/reviewer contribution edges to canonical provisional
  people, and aggregating demonstrated evidence. It separately retrieves named researchers' exact
  initial session questions as `active_investigators`, including sessions that produced no finding
  or approved knowledge. Authorship and finding ownership count as primary
  evidence; review is supporting evidence and cannot qualify a person by itself. Repeated evidence
  from one source session is capped so verbosity cannot dominate, abandoned attempts are not
  penalized, and every ranked person includes the concrete artifacts that support the result. The
  deterministic `expertise_evidence_v2` score is a ranking score, never a probability. Confidence is
  `low | moderate | high` based on independent source-session and primary-evidence counts.
  Investigation activity alone can never create a demonstrated expert; it contributes only a small,
  capped, exposed bonus to a person who already has primary evidence. Explicit disease scope
  excludes findings from other or unspecified diseases; low field-coverage matches and non-person
  labels such as `Unknown` or `AI-agent` are excluded. Output reports the highest evidence score
  among the sources searched and states that the score establishes neither an organizational role
  nor general expertise. Host guidance requires compact scientific reporting of stored facts and
  prohibits dramatic framing, post-hoc interpretation, and unsolicited recommendations.
- **Data:** reads `people`, `person_contributions`, `person_investigations`, `chat_sessions`,
  `chat_messages`, `knowledge_items`, `knowledge_embeddings`, and `findings`; returns
  canonical/provisional identity status, role-labelled evidence, separately ranked investigation
  activity, distinct session counts, score components, confidence, and searched sources.
- **Source:** `src/breadcrumbs/store.py`; `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-015:** Independent relevant authored contributions rank above repeated same-session work;
  review-only and non-person identities are excluded; explicit disease scope removes off-disease
  findings; low-coverage incidental matches are removed; abandoned work remains evidence; names
  differing only in case or whitespace resolve to one provisional person; repeated relevant named
  sessions appear under `active_investigators`; investigator-only people never appear under
  demonstrated experts; and empty results name the stores searched without claiming a definitive
  absence of expertise.

### BC-MCP-014 — `prepare_memory_diff`
- **Behavior:** Converts a natural interaction-level knowledge candidate into a reproducible,
  source-grounded elicitation packet without asking the researcher for storage metadata. Given a
  host-inferred proposition, rationale, scope, optional kind, and relevant recent `live_context`
  turns copied exactly by the agent, the tool writes one content-addressed source snapshot to
  `chat_sessions` / `chat_messages`, then deterministically ranks its exact spans. Repeating the
  same capture is idempotent. If `live_context` is omitted, the tool may instead search an already
  stored interaction, optionally narrowed by `source_session_id`. It returns the selected verbatim
  quote plus bounded alternatives, source identifiers/hash/offsets, prior context that excludes the
  evidence, posterior context that adds it, the fixed belief-label vocabulary, pinned approved
  model, deterministic run ID and replicate count, an authenticated actor hint when available, and
  a partial `write_knowledge` record template. The researcher supplies no sync action, transcript,
  IDs, quote, samples, model name, or run ID. If authenticated actor context is unavailable, author
  remains explicitly missing; Breadcrumbs does not infer it from the transcript. The host executes
  the returned elicitation protocol, presents the scientific Memory Diff, and calls
  `write_knowledge` only after explicit approval. Source capture is not knowledge approval.
  A conversation may yield any number of distinct, source-supported candidates; there is no hard
  extraction or persistence cap. The host deduplicates semantically overlapping propositions,
  queues supported candidates, and presents manageable review batches with a default batch size of
  three. Batch size is a presentation setting only. Every candidate is prepared, elicited,
  reviewed, and approved independently, and candidates grounded in the same exact `live_context`
  reuse its content-addressed source snapshot. Before answering each substantive researcher turn,
  the host checks the newest turn and its immediately relevant context for an explicit correction,
  decision, constraint, exception, abandoned approach, or belief revision. It prepares a supported
  candidate before continuing analysis or web lookup. A settled negative correction remains a
  candidate when the replacement choice is unresolved; preparation is source capture, not approval.
- **Data:** reads and idempotently inserts source snapshots in `chat_sessions`, `chat_messages`, and
  `chat_message_sections`; does not persist samples, candidates, or `knowledge_items`.
- **Source:** `src/breadcrumbs/interaction_context.py`; `src/breadcrumbs/store.py`;
  `src/breadcrumbs/server.py`.
- **Status:** built-at-parity.
- **REQ-016:** Natural corrections and decisions can produce any number of distinct, independently
  reviewable candidates from exact live host turns without researcher-supplied sync, identifiers,
  quotes, belief samples, model, or run ID. Calls over the same exact context reuse one stable source
  snapshot while producing separate deterministic drafts and before/after packets; no authoritative
  knowledge row exists before its own explicit approval. The host deduplicates overlapping
  propositions and uses a default review batch size of three without treating it as a candidate cap.
  Host instructions place an explicit write-back check before research, web lookup, or response and
  treat settled corrections as candidates even when their replacement choice remains unresolved.
