"""Offline fixture tests; no Substrate runtime is started."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from ch14_recovery_receipt_demo import (
    ToyService, LookupUnavailable, capture, restore, reconcile,
    recovery_window, demo,
)


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ax-ch14-test-")
        self.path = str(Path(self.temp.name) / "service.sqlite")
        self.service = ToyService(self.path)
        self.data = {"pending": {"operation_id": "op-1", "payload": "patch-a"},
                     "result": None}
        self.memory = {"phase": "WAITING_FOR_RECEIPT"}

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_full_preserves_supplied_heap(self):
        view = restore(capture("FULL", self.data, self.memory))
        self.assertEqual(view["memory"], self.memory)

    def test_data_uses_entrypoint(self):
        snapshot = capture("DATA", self.data, self.memory)
        self.assertIsNone(snapshot.memory)
        self.assertEqual(restore(snapshot)["memory"]["phase"], "ENTRYPOINT")

    def test_capture_copies_nested_data(self):
        snapshot = capture("FULL", self.data, self.memory)
        self.data["pending"]["payload"] = "changed"
        self.assertEqual(snapshot.data["pending"]["payload"], "patch-a")

    def test_capture_copies_memory(self):
        snapshot = capture("FULL", self.data, self.memory)
        self.memory["phase"] = "changed"
        self.assertEqual(snapshot.memory["phase"], "WAITING_FOR_RECEIPT")

    def test_restore_is_independent(self):
        snapshot = capture("FULL", self.data, self.memory)
        view = restore(snapshot);view["data"]["pending"] = None
        self.assertIsNotNone(restore(snapshot)["data"]["pending"])

    def test_invalid_scope(self):
        with self.assertRaises(ValueError):capture("FULLISH", self.data, self.memory)

    def test_service_commits_one_effect(self):
        self.assertEqual(self.service.publish("op-1", "patch-a"), "receipt-1")
        self.assertEqual(self.service.effect_count(), 1)

    def test_same_id_same_payload_is_deduplicated(self):
        first = self.service.publish("op-1", "patch-a")
        self.assertEqual(self.service.publish("op-1", "patch-a"), first)
        self.assertEqual(self.service.effect_count(), 1)

    def test_same_id_different_payload_is_rejected(self):
        self.service.publish("op-1", "patch-a")
        with self.assertRaises(ValueError):self.service.publish("op-1", "patch-b")
        self.assertEqual(self.service.effect_count(), 1)

    def test_new_id_creates_another_effect(self):
        self.service.publish("op-1", "patch-a")
        self.service.publish("op-2", "patch-a")
        self.assertEqual(self.service.effect_count(), 2)

    def test_reopen_retains_receipt(self):
        self.service.publish("op-1", "patch-a")
        self.service.close();self.service = ToyService(self.path)
        self.assertEqual(self.service.lookup("op-1")["receipt"], "receipt-1")

    def test_full_rollback_does_not_rollback_service(self):
        snapshot = capture("FULL", self.data, self.memory)
        self.service.publish("op-1", "patch-a")
        view = restore(snapshot)
        self.assertIsNone(view["data"]["result"])
        self.assertEqual(self.service.effect_count(), 1)

    def test_both_scopes_reconcile_same_receipt(self):
        self.service.publish("op-1", "patch-a")
        for scope in ("FULL", "DATA"):
            view = restore(capture(scope, self.data, self.memory))
            self.assertEqual(reconcile(view, self.service), "CONFIRMED_FROM_SERVICE")
            self.assertEqual(view["data"]["result"], "receipt-1")
            self.assertIsNone(view["data"]["pending"])
        self.assertEqual(self.service.effect_count(), 1)

    def test_unavailable_lookup_preserves_intent(self):
        self.service.lookup_available = False
        view = restore(capture("DATA", self.data, self.memory));before = deepcopy(view)
        self.assertEqual(reconcile(view, self.service), "OUTCOME_UNKNOWN")
        self.assertEqual(view, before)

    def test_unavailable_lookup_does_not_publish(self):
        self.service.publish("op-1", "patch-a");self.service.lookup_available = False
        self.assertEqual(reconcile(restore(capture("FULL", self.data, self.memory)), self.service), "OUTCOME_UNKNOWN")
        self.assertEqual(self.service.effect_count(), 1)

    def test_absent_receipt_does_not_authorize_retry(self):
        view = restore(capture("DATA", self.data, self.memory))
        self.assertEqual(reconcile(view, self.service), "REQUIRES_DECISION")
        self.assertEqual(self.service.effect_count(), 0)
        self.assertIsNotNone(view["data"]["pending"])

    def test_receipt_payload_conflict(self):
        self.service.publish("op-1", "patch-b")
        view = restore(capture("FULL", self.data, self.memory))
        self.assertEqual(reconcile(view, self.service), "PAYLOAD_CONFLICT")
        self.assertIsNotNone(view["data"]["pending"])

    def test_no_pending_intent(self):
        view = {"data": {"pending": None, "result": "receipt-1"}}
        self.assertEqual(reconcile(view, self.service), "NO_PENDING_INTENT")

    def test_reconcile_is_repeatable_after_confirmation(self):
        self.service.publish("op-1", "patch-a")
        view = restore(capture("FULL", self.data, self.memory))
        reconcile(view, self.service)
        self.assertEqual(reconcile(view, self.service), "NO_PENDING_INTENT")
        self.assertEqual(self.service.effect_count(), 1)

    def test_lookup_distinguishes_absence(self):
        self.assertIsNone(self.service.lookup("missing"))
        self.service.lookup_available = False
        with self.assertRaises(LookupUnavailable):self.service.lookup("missing")

    def test_empty_id_or_payload(self):
        for operation_id,payload in (("", "x"), ("x", "")):
            with self.assertRaises(ValueError):self.service.publish(operation_id,payload)

    def test_supplied_window(self):
        self.assertEqual(recovery_window(420, 120), 300)

    def test_no_external_point_is_not_zero_loss(self):
        self.assertIsNone(recovery_window(420, None))

    def test_zero_age(self):
        self.assertEqual(recovery_window(420, 420), 0)

    def test_invalid_timestamps(self):
        for now,point in ((100, 120), (420, -1), (True, 0), (420, "120")):
            with self.subTest(now=now,point=point),self.assertRaises(ValueError):
                recovery_window(now, point)

    def test_demo(self):
        result = demo()
        self.assertFalse(result["substrate_contacted"])
        self.assertEqual([x["service_effects"] for x in result["recovered"]], [1,1])
        self.assertEqual(result["lookup_unavailable"]["outcome"], "OUTCOME_UNKNOWN")
        self.assertEqual(result["lookup_unavailable"]["service_effects"], 1)
        self.assertEqual(result["supplied_node_loss_window_s"], 300)


if __name__ == "__main__":
    unittest.main()
