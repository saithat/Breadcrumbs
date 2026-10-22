"""Small shared examples of a reviewed decision and its source conversation."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from breadcrumbs.contracts import KnowledgeRecord

QUOTE = "Use the canary only as exploratory because there are three independent environments."
SOURCE = f"The single-region signal is statistically precise.\n{QUOTE}\nValidate more environments."
TURNS = [
    ("user", "Can the single-region canary justify a broad rollout?"),
    ("assistant", SOURCE),
    ("user", "Agreed; validate more environments before broad rollout."),
]
PROPOSITION = "Treat the single-region canary as exploratory until more environments are validated."


def concurrent_retries(call):
    """Start two identical host requests together to exercise database idempotence."""
    ready = Barrier(2)

    def run(_):
        ready.wait()
        return call()

    with ThreadPoolExecutor(max_workers=2) as pool:
        return list(pool.map(run, range(2)))


def candidate(**changes):
    return {
        "kind": "constraint",
        "proposition": PROPOSITION,
        "rationale": "Instance counts cannot replace independent environment validation.",
        "scope": {"system": "payments", "environment": "production"},
        "aliases": ["canary rollout constraint"],
        "conditions": [{"field": "environments", "operator": "lt", "value": 4}],
        "evidence_quote": QUOTE,
        "source_message_id": "source:1",
        "prior_samples": ["uncertain"] * 3,
        "posterior_samples": ["strongly_believe"] * 3,
        "elicitation_model": "claude-sonnet-5",
        "elicitation_run_id": "test-run",
        "author": "Alex Rivera",
        **changes,
    }


def record(**changes):
    return KnowledgeRecord.model_validate(candidate(**changes))
