"""Recovery closeout must require an exact journal and recognized state."""

from pathlib import Path
import tempfile
from unittest import TestCase
from unittest.mock import patch

from gateway import ipv6_reconcile as recovery
from tests_gateway.test_ipv6_policy import RAW, snapshot


class ReconcileTests(TestCase):
    def test_complete_target_closes_only_matching_intent(self):
        current = snapshot("secondary")
        journal = {
            "phase": "intent",
            "transaction_id": "a" * 32,
            "before_digest": "b" * 64,
            "desired": "secondary",
        }
        saved = []
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(recovery, "LOCK", Path(directory) / "lock"),
            patch.object(recovery.os, "geteuid", return_value=0),
            patch.object(recovery, "private_json", side_effect=[RAW, journal]),
            patch.object(recovery, "snapshot", return_value=current),
            patch.object(recovery, "atomic_journal", side_effect=saved.append),
        ):
            result = recovery.reconcile("a" * 32, current.digest(), "secondary")
        self.assertTrue(result["reconciled"])
        self.assertEqual(saved[0]["phase"], "complete")

    def test_foreign_digest_or_journal_never_closes(self):
        current = snapshot("primary")
        journal = {
            "phase": "blocked",
            "transaction_id": "a" * 32,
            "before_digest": "b" * 64,
            "desired": "secondary",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(recovery, "LOCK", Path(directory) / "lock"),
            patch.object(recovery.os, "geteuid", return_value=0),
            patch.object(recovery, "private_json", side_effect=[RAW, journal]),
            patch.object(recovery, "snapshot", return_value=current),
            patch.object(recovery, "atomic_journal") as save,
        ):
            with self.assertRaisesRegex(recovery.PolicyError, "gateway_state_does_not_match"):
                recovery.reconcile("a" * 32, current.digest(), "primary")
            save.assert_not_called()
