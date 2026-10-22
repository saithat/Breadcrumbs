from breadcrumbs.contracts import KnowledgeEvidence, PeopleRequest
from tests.support import QUOTE, TURNS, record


def people(store, topic, **options):
    return store.find_people_with_context(PeopleRequest(topic=topic, **options))


def test_people_preserve_relationships_counts_and_provenance(store, add_session):
    add_session("source", TURNS, actor="Alex Rivera")
    written = store.write_knowledge(
        record(kind="abandoned", reason="The sample is too small."), "alex rivera"
    )
    add_session("source", [("user", "Canary rollout?"), ("assistant", "Edited source.")])
    result = people(store, "canary", scope={"system": "payments"})
    person = next(p for p in result.people if p.display_name == "Alex Rivera")
    assert person.relationships == ["knowledge_author", "knowledge_reviewer", "session_actor"]
    assert person.approved_knowledge_count == 1
    assert "not expertise" in result.message
    evidence = next(e for e in person.evidence if isinstance(e, KnowledgeEvidence))
    assert (evidence.knowledge_id, evidence.evidence_quote, evidence.reason) == (
        written.id,
        QUOTE,
        written.reason,
    )
    assert evidence.source_drifted is True


def test_session_activity_and_empty_results_make_calibrated_claims(store, add_session):
    add_session("billing", [("user", "Which billing migration is active?")], actor="Sam Patel")
    result = people(store, "billing migration")
    assert [(p.display_name, p.relationships) for p in result.people] == [
        ("Sam Patel", ["session_actor"]),
    ]
    assert result.people[0].approved_knowledge_count == 0
    empty = people(store, "warehouse inventory forecasting")
    assert empty.people == []
    assert "were found" in empty.message


def test_agents_and_unknown_names_do_not_become_people(store, add_session):
    for name in ("assistant", "unknown"):
        add_session(name, TURNS, actor=name)
        store.write_knowledge(record(source_message_id=f"{name}:1", author=name), "Morgan Lee")
    result = people(store, "canary")
    assert [(p.display_name, p.relationships) for p in result.people] == [
        ("Morgan Lee", ["knowledge_reviewer"]),
    ]
