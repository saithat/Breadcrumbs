# Breadcrumbs / Research Memory Tools — Component Registry

> The ordered list of components. The same IDs, in the same order, must appear in `spec.tech.md`
> and `feature.json.component_ids`.
> Status vocabulary: built-at-parity | gap | not-built | descoped | unverified | planned | in_progress

## MCP — the server and its tools

| ID | Component | Status |
|----|-----------|--------|
| BC-MCP-001 | Server bootstrap and tool registration (Claude Desktop as host) | built-at-parity |
| BC-MCP-002 | Tool contracts — typed I/O, pinned status/edge/verdict vocabularies | built-at-parity |
| BC-MCP-003 | `check_duplication` stage 1 — internal findings retrieval | built-at-parity |
| BC-MCP-004 | `check_duplication` stage 2 — local concept match → match / open | built-at-parity |
| BC-MCP-005 | Abandoned-result surfacing as a first-class result type — **the differentiator** | built-at-parity |
| BC-MCP-006 | Calibrated-language layer — never "novel" | built-at-parity |
| BC-MCP-007 | `write_finding` — append-only, provenance-tagged, human-gated | built-at-parity |
| BC-MCP-008 | `recall_findings` — by topic, entity, or context | built-at-parity |
| BC-LIT-001 | Host-managed external literature research | descoped |
| BC-MCP-009 | `render_wiki` — generated, read-only Markdown view | built-at-parity |
| BC-MCP-010 | `score_surprise` — reproducible Bayesian belief shift in bits | built-at-parity |
| BC-MCP-011 | `write_knowledge` — source-verified human approval gate | built-at-parity |
| BC-MCP-012 | `recall_knowledge` — BM25+dense rank fusion with constraint-aware active-patch recall | built-at-parity |
| BC-MCP-013 | `find_experts` — demonstrated expertise plus separately calibrated investigation activity | built-at-parity |
| BC-MCP-014 | `prepare_memory_diff` — idempotent live-turn capture, source search, and elicitation context | built-at-parity |
