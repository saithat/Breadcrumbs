import hashlib

import psycopg
import pytest
from pydantic import ValidationError

from breadcrumbs.contracts import RecallRequest
from tests.support import QUOTE, SOURCE, TURNS, concurrent_retries, record


@pytest.fixture(autouse=True)
def source(add_session):
    add_session("source", TURNS)


def recall(store, query, **options):
    return store.recall_knowledge(RecallRequest(queries=[query], **options))


def count(db):
    return db.execute("SELECT count(*) AS n FROM knowledge_items").fetchone()["n"]


def test_approval_preserves_reviewed_fields_and_derives_provenance_and_scores(store):
    reviewed = record(kind="abandoned", reason="The sample cannot support broad rollout.")
    written = store.write_knowledge(reviewed, "Morgan Lee")
    assert written.model_dump(include=set(reviewed.model_fields)) == reviewed.model_dump()
    assert written.approved_by == "Morgan Lee"
    assert written.source_message_hash == hashlib.sha256(SOURCE.encode()).hexdigest()
    assert written.source_session_id == "source"
    assert (written.belief_shift, written.surprising) == (0.5, True)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"kind": "abandoned"}, "reason is required"),
        ({"bayesian_surprise_bits": 9}, "Extra inputs"),
        ({"approved_by": "Morgan Lee"}, "Extra inputs"),
    ],
)
def test_record_rejects_missing_reason_and_supplied_derived_fields(changes, error):
    with pytest.raises(ValidationError, match=error):
        record(**changes)


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"source_message_id": "missing:1"}, "Unknown source message"),
        ({"evidence_quote": "A paraphrase is not provenance."}, "verbatim"),
        ({"supersedes_id": "K-MISSING"}, "Unknown superseded"),
    ],
)
def test_unverifiable_provenance_never_writes(store, db, changes, error):
    with pytest.raises(ValueError, match=error):
        store.write_knowledge(record(**changes), "Morgan Lee")
    assert count(db) == 0


def test_retry_is_idempotent_but_cannot_hide_a_reviewed_edit(store, db):
    written, retried = concurrent_retries(lambda: store.write_knowledge(record(), "Morgan Lee"))
    assert retried.id == written.id
    with pytest.raises(ValueError, match="different reviewed field"):
        store.write_knowledge(record(rationale="A materially different rationale."), "Morgan Lee")
    with pytest.raises(ValueError, match="approved_by"):
        store.write_knowledge(record(), "Someone Else")
    assert count(db) == 1


def test_reingestion_preserves_approval_and_flags_source_drift(store, add_session):
    written = store.write_knowledge(record(), "Morgan Lee")
    for content, drifted in [(SOURCE, False), ("This answer was edited after approval.", True)]:
        add_session("source", [("user", "Question"), ("assistant", content)], revision="2")
        recalled = recall(store, "canary")[0]
        assert (recalled.id, recalled.evidence_quote) == (written.id, QUOTE)
        assert recalled.source_drifted is drifted
    with pytest.raises(psycopg.IntegrityError):
        add_session("source", [("user", "Approved source omitted.")])


def test_corrections_append_history_and_cannot_branch(store, add_session):
    prior = store.write_knowledge(record(), "Morgan Lee")
    quote = "With 28 independent environments, re-evaluate canary rollout inference."
    add_session("correction", [("user", quote)])
    correction = record(
        kind="belief_revision",
        proposition=quote,
        evidence_quote=quote,
        source_message_id="correction:0",
        supersedes_id=prior.id,
        aliases=[],
    )
    revision, retried = concurrent_retries(lambda: store.write_knowledge(correction, "Morgan Lee"))
    assert retried.id == revision.id
    assert [item.id for item in recall(store, "canary")] == [revision.id]
    history = recall(store, "canary", include_superseded=True)
    assert {item.id: item.is_superseded for item in history} == {prior.id: True, revision.id: False}
    via_history = recall(store, "single region")[0]
    assert (via_history.id, via_history.matched_via_history) == (revision.id, [prior.id])
    branch = correction.model_copy(update={"proposition": "An alternative branch."})
    with pytest.raises(ValueError, match="already has a successor"):
        store.write_knowledge(branch, "Morgan Lee")


def test_recall_reports_scope_and_condition_compatibility(store):
    written = store.write_knowledge(
        record(
            aliases=["AuroraDecisionToken"],
            scope={"system": "payments", "environment": "production"},
            conditions=[
                {
                    "field": "environments",
                    "field_aliases": ["regions"],
                    "operator": "lt",
                    "value": 4,
                }
            ],
        ),
        "Morgan Lee",
    )
    matched = recall(store, "canary", scope={"system": "payments", "regions": 3, "method": "x"})[0]
    assert matched.id == written.id
    assert matched.conditions == written.conditions
    assert set(matched.scope_compatibility.compatible) == {"system", "regions"}
    assert matched.scope_compatibility.unknown == ["method"]
    failed = recall(store, "canary", scope={"environments": 4})[0]
    assert failed.scope_compatibility.incompatible == ["environments"]
    assert recall(store, "canary", scope={"environment": "staging"}, strict_scope=True) == []
    assert recall(store, "canary", scope={"method": "x"}, strict_scope=True) == []
    assert recall(store, "AuroraDecisionToken")[0].id == written.id
    assert recall(store, "warehouse forecasting") == []


def test_full_text_honours_phrases_and_fuses_rephrasings(store, add_session):
    def approve(name, proposition):
        add_session(name, [("user", proposition)])
        return store.write_knowledge(
            record(
                proposition=proposition,
                evidence_quote=proposition,
                source_message_id=f"{name}:0",
                aliases=[],
            ),
            "Morgan Lee",
        )

    common = [
        approve(f"common{i}", f"Production rollout {i} waits for the weekly rollout review.")
        for i in range(3)
    ]
    rare = approve("rare", "Kestrel migrations need a database owner on call.")
    top = recall(store, "who owns Kestrel database migrations")[0]
    assert top.id == rare.id
    assert top.matches[0].rank == 1

    assert recall(store, '"weekly rollout review"')[0].id in {item.id for item in common}
    assert recall(store, '"rollout weekly review"') == []
    assert all(item.id != rare.id for item in recall(store, 'Kestrel "rollout review"'))

    fused = store.recall_knowledge(
        RecallRequest(queries=["on-call database owner", "schema change staffing"])
    )
    assert fused[0].id == rare.id
    assert [match.query for match in fused[0].matches] == ["on-call database owner"]
