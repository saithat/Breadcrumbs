# Review Queue — Research Memory Tools

> Unresolved risks, divergences, and findings raised during spec or build.
> An item here BLOCKS completion until resolved or explicitly accepted. Append-only; don't delete,
> mark resolved.

| # | Raised by | Finding | Severity | Status |
|---|-----------|---------|----------|--------|
| 1 | spec-author | **Data boundary.** Resolution: the UI-aligned duplication path uses the local matcher in `src/breadcrumbs/store.py`; hypothesis text is not sent to an external model. The project owner separately confirmed on 2026-07-12 that the research data is public; authentication material remains secret. | error | resolved |
| 2 | spec-author | `write_finding` must reuse the validation in `ingestion/write_findings.py` rather than reimplementing it. Resolution: `BreadcrumbsStore.write` calls `write_payload` directly and the shared-gate test exercises both paths. | warn | resolved |
| 3 | spec-author | The MCP endpoint paths recorded in `feature.json.backend` are a mapping onto the layer schema's HTTP shape, while the real MCP surface is `tools/call`. Resolution: tools use FastMCP; `/check_duplication`, `/knowledge/*`, and `/experts/find` are deliberate UI/host REST seams. | info | resolved |
| 4 | spec-author | **The `check_duplication` response shape is already defined in TypeScript.** Resolution: `src/breadcrumbs/contracts.py` mirrors the UI boundary, generates `schema/mcp_contracts.schema.json`, and the contract test pins the UI enums and checked schema. | error | resolved |
| 5 | codex | **Dense retrieval boundary.** The project owner confirmed on 2026-07-12 that the research data is public. Dense retrieval may use the pinned public local model; the model ID, content hash, dimensions, and exact vector bytes remain recorded for reproducibility. Authentication material is never embedded. | error | resolved |
