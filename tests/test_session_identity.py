from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from breadcrumbs.identity import backfill_session_identity_candidates
from breadcrumbs.store import BreadcrumbsStore
from ingestion.store import connect


class SessionIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "identity.db"
        connection = connect(self.path)
        connection.executemany(
            "INSERT INTO chat_sessions("
            "id,url,title,scraped_at,researcher,raw_json"
            ") VALUES (?,?,?,?,?,?)",
            [
                (
                    "named-source",
                    "https://example.test/named",
                    "Named",
                    "2027-01-01T00:00:00Z",
                    "Dr. Alice",
                    "{}",
                ),
                (
                    "duplicate-target",
                    "https://example.test/duplicate",
                    "Duplicate",
                    "2027-01-02T00:00:00Z",
                    None,
                    "{}",
                ),
                (
                    "artifact-session",
                    "https://example.test/artifact",
                    "Artifact",
                    "2027-01-03T00:00:00Z",
                    None,
                    "{}",
                ),
                (
                    "named-without-messages",
                    "https://example.test/owner-only",
                    "Owner only",
                    "2027-01-04T00:00:00Z",
                    "Dr. Dora",
                    "{}",
                ),
            ],
        )
        connection.executemany(
            "INSERT INTO chat_messages(id,session_id,seq,role,content) VALUES (?,?,?,?,?)",
            [
                (
                    "named-source:q",
                    "named-source",
                    0,
                    "user",
                    "Should we investigate EGFR resistance?",
                ),
                (
                    "duplicate-target:q",
                    "duplicate-target",
                    0,
                    "user",
                    "  Should we investigate EGFR resistance?  ",
                ),
                (
                    "artifact-session:q",
                    "artifact-session",
                    0,
                    "user",
                    "How should TP53 evidence be validated?",
                ),
            ],
        )
        connection.commit()
        connection.close()

        self.store = BreadcrumbsStore(self.path)
        for finding_id, author in (
            ("F-IDENTITY-BOB", "Dr. Bob"),
            ("F-IDENTITY-CAROL", "Dr. Carol"),
            ("F-IDENTITY-AGENT", "AI-agent"),
        ):
            self.store.write(
                {
                    "id": finding_id,
                    "category": "LUAD-immune",
                    "disease": "BLCA",
                    "hypothesis_text": "Validate TP53 evidence at patient level.",
                    "entities": ["TP53"],
                    "effect": "No effect size was calculated in this identity fixture.",
                    "status": "in-progress",
                    "author": author,
                    "source_session_id": "artifact-session",
                    "source_type": "internal",
                }
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def candidates(self):
        connection = connect(self.path)
        try:
            return connection.execute(
                "SELECT c.*, p.normalized_name FROM session_identity_candidates c "
                "JOIN people p ON p.id = c.person_id "
                "ORDER BY c.session_id, c.evidence_type, p.normalized_name"
            ).fetchall()
        finally:
            connection.close()

    def test_identity_evidence_is_graded_hashed_and_idempotent(self) -> None:
        first_counts = backfill_session_identity_candidates(self.path)
        first = self.candidates()
        second_counts = backfill_session_identity_candidates(self.path)
        second = self.candidates()

        self.assertEqual(first_counts, second_counts)
        self.assertEqual(len(first), 5)
        self.assertEqual(len(second), 5)
        self.assertEqual(
            [row["updated_at"] for row in first],
            [row["updated_at"] for row in second],
        )
        by_key = {
            (row["session_id"], row["normalized_name"], row["evidence_type"]): row
            for row in second
        }
        direct = by_key[("named-source", "dr. alice", "session_researcher")]
        self.assertEqual((direct["evidence_strength"], direct["status"]), ("confirmed", "accepted"))
        owner_only = by_key[
            ("named-without-messages", "dr. dora", "session_researcher")
        ]
        self.assertEqual(owner_only["status"], "accepted")

        propagated = by_key[
            ("duplicate-target", "dr. alice", "exact_question_match")
        ]
        self.assertEqual(
            (propagated["evidence_strength"], propagated["status"]),
            ("weak", "proposed"),
        )
        for name in ("dr. bob", "dr. carol"):
            candidate = by_key[("artifact-session", name, "finding_author")]
            self.assertEqual(
                (candidate["evidence_strength"], candidate["status"]),
                ("supporting", "proposed"),
            )
        self.assertNotIn(
            ("artifact-session", "ai-agent", "finding_author"), by_key
        )

        for row in second:
            canonical = json.dumps(
                json.loads(row["evidence"]),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            self.assertEqual(row["evidence"], canonical)
            self.assertEqual(
                row["evidence_hash"],
                hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )

    def test_proposed_candidates_do_not_become_session_owners_or_investigators(self) -> None:
        backfill_session_identity_candidates(self.path)
        connection = connect(self.path)
        try:
            researchers = {
                row["id"]: row["researcher"]
                for row in connection.execute(
                    "SELECT id, researcher FROM chat_sessions ORDER BY id"
                )
            }
            investigation_sessions = {
                row["session_id"]
                for row in connection.execute(
                    "SELECT session_id FROM person_investigations"
                )
            }
        finally:
            connection.close()

        self.assertIsNone(researchers["duplicate-target"])
        self.assertIsNone(researchers["artifact-session"])
        self.assertEqual(investigation_sessions, {"named-source"})

    def test_human_candidate_status_survives_deterministic_refresh(self) -> None:
        backfill_session_identity_candidates(self.path)
        connection = connect(self.path)
        try:
            with connection:
                connection.execute(
                    "UPDATE session_identity_candidates SET status = 'rejected' "
                    "WHERE session_id = 'artifact-session' AND candidate_name = 'Dr. Bob'"
                )
        finally:
            connection.close()

        backfill_session_identity_candidates(self.path)
        rows = self.candidates()
        bob = next(row for row in rows if row["candidate_name"] == "Dr. Bob")
        self.assertEqual(bob["status"], "rejected")


if __name__ == "__main__":
    unittest.main()
