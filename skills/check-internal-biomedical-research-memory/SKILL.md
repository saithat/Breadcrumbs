---
name: check-internal-biomedical-research-memory
description: Automatically use Breadcrumbs internal research memory before answering any biomedical research planning, hypothesis, experiment, analysis, interpretation, expertise, or follow-up request. Trigger even when the user does not mention Breadcrumbs, prior work, duplication, or novelty, including plain intent statements such as "I am considering...", "I want to test...", and "Should we analyze...?".
---

# Check Internal Biomedical Research Memory

Use the enabled `breadcrumbs` MCP connector as the organization's internal research memory. Prefer internal evidence before external context. Do not treat the database as a literature database or infer novelty from missing rows.

## Run the write-back check before answering

Before answering every substantive researcher turn, inspect the newest user message together with
the immediately relevant exchange. If it contains an explicit correction, decision, constraint,
exception, abandoned approach, or belief revision that would change what a future researcher should
believe or do, call `breadcrumbs:prepare_memory_diff` immediately and present the resulting Memory
Diff for approval before continuing the scientific analysis or starting a web lookup.

Do not merely agree with the change, research it further, wait until the end of the session, or ask
whether the researcher wants it stored. Preparing the diff captures source provenance but does not
approve or persist the candidate. `breadcrumbs:write_knowledge` still requires explicit approval of
that exact diff.

A settled correction is independently reviewable even when the replacement choice remains open.
For example, “do not transfer this disease-specific endpoint policy to another disease; choose from
disease-specific evidence” is a candidate constraint even though the endpoint has not yet been
selected. Generic questions, tentative brainstorming, requests for information, and facts that do
not change future belief or action are not candidates.

## Decide when to check

Check Breadcrumbs before answering a request that:

- proposes, prioritizes, or evaluates a biomedical hypothesis;
- plans an analysis or experiment;
- asks whether work is new, duplicated, previously attempted, or worth repeating;
- interprets an internal result or recommends follow-up work.

Trigger from research intent, not only explicit memory language. For example, automatically check
when a researcher says "I am considering a MOSAIC BLCA analysis..." even if they do not ask about
Breadcrumbs, prior attempts, duplication, expertise, or novelty. Load this skill and query the
internal tools before giving scientific guidance.

Skip the check for simple educational facts that do not propose or evaluate research.

If the connector tools are not loaded, search for the `breadcrumbs` tools, then call them. Do not stop after listing or discovering tools. If the connector is unavailable, state that the internal-memory check could not be performed.

## Read before reasoning

For a natural-language research question, call `breadcrumbs:check_duplication` and
`breadcrumbs:recall_knowledge` before answering. Use `breadcrumbs:recall_findings` when the user
asks for related prior work or graph context. Use the exact `breadcrumbs:read` tool only when the
request supplies a credible stored field and value.

The tool performs exact equality matching, not semantic or fuzzy search. Treat natural-language
terms in the request as search clues, not necessarily as the literal values stored in Breadcrumbs.

1. Extract disease, category, gene, biomarker, phenotype, outcome, and status clues from the request.
2. Normalize obvious names to likely stored values before reading. Prefer canonical disease codes
   and gene symbols over prose names.
3. Call `breadcrumbs:read` with one exact `column` and scalar `value` for each useful canonical
   value. Start with `disease`; use `category`, `status`, `author`, `source_session_id`, or another
   supported field when the request supplies a credible exact value.
4. If a term has multiple scientifically plausible canonical forms, make a bounded set of
   additional reads rather than assuming the first form is correct. Usually two to four reads are
   enough; do not generate an unbounded synonym list.
5. Combine and de-duplicate results by finding `id`, then judge relevance from the complete finding
   text returned by the tool. A broad disease query may return the relevant hypothesis even when
   its wording differs from the request.
6. Do not silently skip the read because the scientific answer seems familiar.

### Find canonical terms with web search

When the request uses prose names, abbreviations, or possible aliases, use web search to identify
the canonical disease codes, gene symbols, and established synonyms before calling
`breadcrumbs:read`. Prefer authoritative public sources such as NCI, HGNC, NCBI, or disease
ontology documentation. Search again when an empty exact query may be explained by a naming
mismatch.

Keep web searches limited to public terminology. Never send internal finding text, unpublished
hypotheses, patient-level information, source-session content, researcher identities, or other
confidential organizational context to web search. Reduce the query to the isolated public term,
for example `lung adenocarcinoma canonical disease abbreviation` or `LKB1 official gene symbol`.
If a term cannot be searched without disclosing confidential context, do not search it; state the
limitation and use safe exact values already supplied by the user.

Use the retrieved aliases to make a bounded set of exact internal reads, usually two to four.
Never broaden silently: related disease subtypes are not interchangeable, and a finding from one
must not be presented as though it came from another. Gene aliases help interpret findings returned
by a disease or category read; the current exact tool does not search inside the JSON `entities`
array by individual member.

Example call:

```json
{
  "column": "disease",
  "value": "LUAD"
}
```

An empty response means only that the exact filter matched no row in the current database. Say which filter was checked. Never call that result proof of novelty.

Before presenting an empty-result conclusion, verify that obvious canonical disease codes and
aliases were tried. Report every exact `column = value` filter used, including filters that returned
zero rows. Use wording such as: `No prior work was found for the exact internal filters disease =
LUAD and disease = LUSC.`

## Summarize retrieved memory

Present internal memory before external knowledge:

1. State how many findings were returned and how directly they bear on the question.
2. Separate `confirmed`, `in-progress`, and `abandoned` findings.
3. Preserve effect sizes, confidence intervals, p-values, sample sizes, and caveats exactly as stored.
4. For abandoned findings, prominently report `reason` and `note` so failed approaches are not repeated.
5. Include `disease`, `category`, `entities`, `author`, `timestamp`, and `source_session_id` for traceability.
6. Say when a field is absent. Do not invent values, strengthen associations into causal claims, or blend separate findings.

Use this compact format for each result:

```text
[status] hypothesis_text
Effect: effect
Entities: entities
Why it matters: direct relevance to the current question
Reason/note: include for abandoned or caveated work
Source: author, timestamp, source_session_id
```

## Answer expertise and investigation questions

Call `breadcrumbs:find_experts` with the scientific topic and only the scope explicitly supplied by
the researcher. Present the result for a scientific audience:

1. Give a compact evidence summary: person, provisional/verified identity status, confidence,
   distinct source sessions, and primary evidence count.
2. List only the returned on-topic evidence with artifact ID, status, stored statistics or effect,
   stored reason, and source session.
3. Report `active_investigators` separately. An empty list means only that no qualifying named
   session activity was retrieved.
4. State that the ranking is based on source-linked work and does not establish an organizational
   role or general expertise.

Use literal, technical language. Do not use dramatic framing or metaphors such as "critical
warning", "hit a wall", "dead end", "shelved", or "worth pausing on". Do not infer that a
hypothesis was disproven, remains open, or would become significant in a larger cohort unless a
stored record explicitly supports that statement. A non-significant result and a small cohort may
be reported as separate stored facts; do not add a post-hoc power interpretation. Do not recommend
contacting a person or propose follow-up work unless the researcher asks for recommendations. Do
not narrate tool discovery or internal reasoning in the final answer.

## Prepare interaction knowledge before approval

Apply the turn-level write-back check before continuing the response. When an exchange changes what
a future researcher should believe or do, identify every distinct,
source-supported candidate implied by an explicit correction, decision, constraint, exception,
abandoned approach, or belief revision. Do not impose a hard per-session candidate cap. Deduplicate
semantically overlapping propositions and exclude generic summaries and unsupported implications.
Queue supported candidates and present them in manageable review batches, using three as the
default presentation batch size rather than as an extraction or persistence limit.

For each distinct candidate, infer one concise proposition, rationale, scope, and optional kind,
then call `breadcrumbs:prepare_memory_diff` with `live_context` containing only the relevant recent
user and assistant turns copied exactly from the active conversation. Do not paraphrase those turns
or include hidden reasoning/tool traces. Do not ask the researcher to sync, transcribe, identify,
or quote the thread, or to provide belief samples, an elicitation model, or a run ID. Pass
`current_actor` only from authenticated host context. Use `source_session_id` without `live_context`
only when deliberately preparing from an interaction already stored in Breadcrumbs. The tool
content-addresses live source turns and supplies exact evidence, bounded alternatives, before/after
packets, fixed labels, approved model, deterministic run ID, author hint, and partial record
template. Multiple candidates grounded in the same exact live context reuse the same source
snapshot while retaining separate draft IDs and records.

Review each returned source selection and warning. If no live or stored span supports a candidate,
discard that candidate rather than inventing provenance or writing knowledge. Never tell the
researcher to perform ingestion. For every supported candidate, execute its returned prior and
posterior elicitation packets independently for the specified replicate count, using exactly that
candidate's proposition and label vocabulary. Do not invent judgments, substitute another model,
or create a run ID. Pass the observed labels to `breadcrumbs:score_surprise`, then show the
scientific Memory Diff and calculated belief shift and Bayesian surprise as measures of belief
movement—not importance or originality.

Do not call `breadcrumbs:write_knowledge` until a person explicitly approves that exact diff.
Supply the approver through the separate `approved_by` argument. Review and approve every candidate
independently; approval of one candidate in a batch never approves another. If the person edits or
declines a candidate, do not persist its unapproved version. The researcher reviews scientific
content; the MCP and host carry the provenance and elicitation mechanics.

## Write reviewed findings only

Call `breadcrumbs:write_finding` only when the conversation contains a reviewed finding supported by an ingested source session. Write one finding per tool call.

Required `record` fields:

```json
{
  "category": "registered category",
  "disease": "disease code",
  "hypothesis_text": "specific testable claim or question",
  "entities": ["NORMALIZED", "ENTITY", "NAMES"],
  "effect": "verbatim result including statistics and caveats",
  "status": "confirmed | in-progress | abandoned",
  "author": "researcher name",
  "source_session_id": "existing ingested session id",
  "source_type": "internal | external"
}
```

Optional fields are `id`, `created_at`, `n`, `provenance`, `reason`, `note`, `markdown`, and `resources`. Omit `id` and `created_at` to let Breadcrumbs create them.

Apply these validation rules:

- Use only a registered `category` and an existing `source_session_id`.
- Use `source_type: internal` for organization-generated work; it must not include resources.
- Use `source_type: external` for published evidence and include non-empty `resources`. Each resource must have `type` set to `paper` or `database` and a `citation`; `url` is optional.
- Use `reason` for `abandoned` findings; make it concrete and actionable.
- Set `reason` to null or omit it for `confirmed` and `in-progress` findings.
- Put methodology and data origin in `provenance`.
- Put caveats and future-research guidance in `note`.
- Never fabricate a required field. Ask the user for missing information instead.

After a successful write, report the stored ID and briefly repeat exactly what was persisted.
