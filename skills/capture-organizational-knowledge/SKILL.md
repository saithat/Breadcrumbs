---
name: capture-organizational-knowledge
description: Use Breadcrumbs organizational memory while planning, deciding, correcting prior assumptions, abandoning an approach, asking what the organization already knows, or asking who has relevant context. Recall approved knowledge before related work and prepare approval-gated Memory Diffs when a conversation creates durable tacit knowledge.
---

# Capture organizational knowledge

Use the Breadcrumbs MCP tools directly.

1. Before related work, call `recall_knowledge` with a few rephrasings of the need and only scope
   fields stated in the request. Apply compatible constraints, decisions, exceptions, abandoned approaches, and
   belief revisions without treating them as universal truth.
2. For people questions, call `find_people_with_context`. Preserve whether each result authored
   knowledge, reviewed it, or participated in a relevant source conversation. These relationships
   provide context; they do not establish expertise or an organizational role.
3. On each substantive turn, check for an explicit decision, correction, constraint, exception,
   abandoned approach, or belief revision that would change future belief or action.
4. When one appears, call `prepare_memory_diff` with only the relevant exact recent turns and the
   exact quote that supports the candidate. Sample each returned elicitation packet `replicates`
   times, then call `score_surprise` with the prior and posterior judgments.
5. Present the candidate's kind, proposition, scope, rationale, exact source quote, belief change,
   whether it is surprising, and conditions. Call `write_knowledge` only after explicit human approval.
6. Use `supersedes_id` for corrections. If approval is skipped, persist nothing.

Never ask the user for database IDs, exact source quotes, model choices, or run IDs. Never invent
evidence, identity, metrics, scope, or approval.
