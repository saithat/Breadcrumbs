import hashlib

import pytest

from breadcrumbs.contracts import KnowledgeRecord, MemoryDiffRequest, RecallRequest
from tests.support import PROPOSITION, QUOTE, SOURCE, TURNS, concurrent_retries, record

SAMPLES = {"prior_samples": ["uncertain"] * 3, "posterior_samples": ["believe"] * 3}


def request(**changes):
    return MemoryDiffRequest.model_validate(
        {
            "proposition": PROPOSITION,
            "rationale": "Instance counts are not independent environment validation.",
            "scope": {"system": "payments", "environment": "production"},
            "evidence_quote": QUOTE,
            "source_turns": [{"role": role, "content": text} for role, text in TURNS],
            "current_actor": "Alex Rivera",
            **changes,
        }
    )


def test_live_capture_is_idempotent_and_only_approval_creates_knowledge(store, db):
    prepared, retried = concurrent_retries(lambda: store.prepare_memory_diff(request()))
    assert prepared == retried == store.prepare_memory_diff(request())
    assert db.execute("SELECT count(*) AS n FROM chat_messages").fetchone()["n"] == len(TURNS)

    source = prepared.source
    assert source.source_message_hash == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert SOURCE[source.quote_start : source.quote_end] == QUOTE
    prior, posterior = prepared.elicitation.prior, prepared.elicitation.posterior
    assert "statistically precise" in prior.context
    assert QUOTE not in prior.context
    assert QUOTE in posterior.context
    assert "Agreed" in posterior.context
    assert prepared.missing_record_fields == ["prior_samples", "posterior_samples"]
    assert store.recall_knowledge(RecallRequest(queries=["canary"])) == []

    reviewed = KnowledgeRecord.model_validate(
        prepared.record_template.model_dump(exclude_none=True) | SAMPLES
    )
    written = store.write_knowledge(reviewed, "Morgan Lee")
    assert written.source_session_id == source.source_session_id
    assert store.recall_knowledge(RecallRequest(queries=["canary"]))[0].id == written.id


def test_prior_is_informed_by_related_approved_knowledge(store, add_session):
    add_session("source", TURNS)
    approved = store.write_knowledge(record(), "Morgan Lee")
    prepared = store.prepare_memory_diff(
        request(
            proposition="Broad canary rollout needs at least four validated environments.",
        )
    )
    assert [item.id for item in prepared.prior_knowledge] == [approved.id]
    belief = f"{approved.proposition} (scope: "
    assert belief in prepared.elicitation.prior.context
    assert "belief: 1.00" in prepared.elicitation.prior.context
    assert belief in prepared.elicitation.posterior.context


def test_evidence_must_be_verbatim_in_the_named_source(store, add_session, db):
    add_session("source", TURNS)
    with pytest.raises(ValueError, match="verbatim"):
        store.prepare_memory_diff(request(evidence_quote="A paraphrase of the canary advice."))
    with pytest.raises(ValueError, match="verbatim"):
        store.prepare_memory_diff(
            request(
                source_turns=None,
                source_message_id="source:0",
            )
        )
    stored = store.prepare_memory_diff(request(source_turns=None, source_message_id="source:1"))
    assert stored.source.source_session_id == "source"
    assert db.execute("SELECT count(*) AS n FROM knowledge_items").fetchone()["n"] == 0
