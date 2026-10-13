import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from ch11_actor_session_demo import (
    Busy, Conflict, Mailbox, SessionStore, demo, initial_state, message, transition,
)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="ax-ch11-test-")
        self.path = Path(self.directory.name) / "sessions.db"
        self.store = SessionStore(self.path)
        self.key = ("tenant-a", "review-42", "uid-42")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def send(self, mid, kind, rev="r1", **fields):
        return self.store.dispatch(self.key, message(mid, kind, rev, **fields))["code"]

    def propose(self, rev="r1"):
        return self.send("plan-" + rev, "ProposePlan", rev)

    def approve(self, rev="r1", call="c1", op="op-1"):
        return self.send("approval-" + rev, "Approve", rev, call_id=call, operation_id=op)

    def result(self, mid="result-1", rev="r1", call="c1", outcome="passed", ref="report/r1"):
        return self.send(mid, "ToolResult", rev, call_id=call, outcome=outcome, report_ref=ref)

    def start(self):
        self.propose(); self.approve()

    def test_initial_state(self):
        self.assertEqual(self.store.state(self.key), initial_state())

    def test_plan_requires_approval(self):
        self.propose()
        self.assertEqual(self.store.state(self.key)["phase"], "waiting_approval")
        self.assertEqual(self.store.state(self.key)["calls"], {})

    def test_approval_records_intent_not_result(self):
        self.start()
        state = self.store.state(self.key)
        self.assertEqual(state["phase"], "waiting_tool")
        self.assertIsNone(state["calls"]["c1"]["result"])

    def test_repeat_command_returns_receipt_without_new_call(self):
        self.start()
        self.assertEqual(self.approve(), "tool_requested")
        self.assertEqual(len(self.store.state(self.key)["calls"]), 1)

    def test_id_reuse_with_new_payload_is_conflict(self):
        self.propose()
        with self.assertRaises(Conflict):
            self.send("plan-r1", "ProposePlan", "r2")
        self.assertEqual(self.store.state(self.key)["revision"], "r1")

    def test_wrong_incarnation_is_rejected(self):
        cmd = message("m1", "ProposePlan", "r1", actor_uid="other-uid")
        with self.assertRaises(Conflict):
            self.store.dispatch(self.key, cmd)
        self.assertEqual(self.store.state(self.key), initial_state())

    def test_same_name_new_incarnation_has_separate_state(self):
        self.propose()
        key = ("tenant-a", "review-42", "new-uid")
        self.assertEqual(self.store.state(key), initial_state())

    def test_tenant_is_part_of_state_and_receipt_scope(self):
        self.propose()
        other = ("tenant-b", "review-42", "uid-42")
        self.store.dispatch(other, message("plan-r1", "ProposePlan", "other-plan"))
        self.assertEqual(self.store.state(other)["revision"], "other-plan")
        self.assertEqual(self.store.state(self.key)["revision"], "r1")

    def test_session_is_part_of_scope(self):
        self.propose()
        self.assertEqual(self.store.state(("tenant-a", "other-session", "uid-42")), initial_state())

    def test_superseded_approval_cannot_start_new_call(self):
        self.propose(); self.propose("r2")
        self.assertEqual(self.approve(), "stale_approval")
        self.assertEqual(self.store.state(self.key)["calls"], {})

    def test_late_result_is_saved_without_promoting_new_plan(self):
        self.start(); self.propose("r2")
        self.assertEqual(self.result(), "historical_result_saved")
        state = self.store.state(self.key)
        self.assertEqual(state["phase"], "waiting_approval")
        self.assertIsNotNone(state["calls"]["c1"]["result"])
        self.assertEqual(state["revision"], "r2")

    def test_late_result_does_not_replace_new_active_call(self):
        self.start(); self.propose("r2"); self.approve("r2", "c2", "op-2")
        self.result()
        state = self.store.state(self.key)
        self.assertEqual((state["phase"], state["active_call"]), ("waiting_tool", "c2"))

    def test_unknown_call_cannot_create_evidence(self):
        self.propose()
        self.assertEqual(self.result(call="unknown"), "unknown_call")
        self.assertEqual(self.store.state(self.key)["calls"], {})

    def test_known_call_wrong_revision_is_rejected(self):
        self.start()
        self.assertEqual(self.result(rev="wrong"), "unknown_call")
        self.assertIsNone(self.store.state(self.key)["calls"]["c1"]["result"])

    def test_passed_report_is_not_review_completion(self):
        self.start(); self.result()
        self.assertEqual(self.store.state(self.key)["phase"], "waiting_acceptance")

    def test_failed_report_blocks_acceptance(self):
        self.start(); self.result(outcome="failed")
        self.assertEqual(self.send("a", "AcceptReview"), "wrong_phase")
        self.assertEqual(self.store.state(self.key)["phase"], "test_failed")

    def test_acceptance_requires_report(self):
        self.start()
        self.assertEqual(self.send("a", "AcceptReview"), "wrong_phase")

    def test_acceptance_matches_current_plan(self):
        self.start(); self.result()
        self.assertEqual(self.send("a", "AcceptReview", "wrong"), "stale_acceptance")

    def test_explicit_acceptance_completes_current_plan(self):
        self.start(); self.result()
        self.assertEqual(self.send("a", "AcceptReview"), "review_accepted")
        self.assertEqual(self.store.state(self.key)["phase"], "completed")

    def test_duplicate_result_under_new_message_id_is_noop(self):
        self.start(); self.result()
        before = self.store.state(self.key)
        self.assertEqual(self.result(mid="result-2"), "result_already_saved")
        self.assertEqual(self.store.state(self.key), before)

    def test_conflicting_result_does_not_overwrite_evidence(self):
        self.start(); self.result()
        with self.assertRaises(Conflict):
            self.result(mid="result-2", outcome="failed")
        self.assertEqual(self.store.state(self.key)["calls"]["c1"]["result"]["outcome"], "passed")

    def test_old_receipt_does_not_describe_current_state(self):
        self.start(); self.propose("r2")
        self.assertEqual(self.approve(), "tool_requested")
        self.assertEqual(self.store.state(self.key)["phase"], "waiting_approval")

    def test_state_and_receipt_rollback_together(self):
        cmd = message("m1", "ProposePlan", "r1")
        with self.assertRaises(RuntimeError):
            self.store.dispatch(self.key, cmd, crash_before_commit=True)
        self.assertEqual(self.store.state(self.key), initial_state())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)
        self.assertEqual(self.store.dispatch(self.key, cmd)["code"], "plan_proposed")

    def test_new_connection_recovers_state_and_receipts(self):
        self.start()
        self.store.close(); self.store = SessionStore(self.path)
        self.assertEqual(self.store.state(self.key)["phase"], "waiting_tool")
        self.assertEqual(self.approve(), "tool_requested")

    def test_returned_state_is_not_mutable_shared_memory(self):
        self.start()
        state = self.store.state(self.key); state["calls"].clear()
        self.assertEqual(len(self.store.state(self.key)["calls"]), 1)

    def test_behavior_does_not_mutate_its_input(self):
        old = initial_state()
        transition(old, message("m1", "ProposePlan", "r1"))
        self.assertEqual(old, initial_state())

    def test_superseded_revision_cannot_be_reused(self):
        self.propose(); self.propose("r2")
        with self.assertRaises(Conflict):
            self.send("new", "ProposePlan", "r1")

    def test_call_id_cannot_be_reused(self):
        self.start(); self.propose("r2")
        with self.assertRaises(Conflict):
            self.approve("r2", "c1", "op-2")

    def test_operation_id_cannot_be_reused_for_new_intent(self):
        self.start(); self.propose("r2")
        with self.assertRaises(Conflict):
            self.approve("r2", "c2", "op-1")

    def test_invalid_commands_leave_no_state_or_receipt(self):
        for cmd in [message("", "ProposePlan", "r1"),
                    message("m1", "ProposePlan", ""),
                    message("m2", "Unexpected", "r1")]:
            with self.assertRaises(Conflict):
                self.store.dispatch(self.key, cmd)
        self.assertEqual(self.store.state(self.key), initial_state())
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM receipts").fetchone()[0], 0)

    def test_mailbox_admission_is_not_state_commit(self):
        box = Mailbox(self.store)
        self.assertEqual(box.submit(self.key, message("m1", "ProposePlan", "r1")), "queued_in_memory")
        self.assertEqual(self.store.state(self.key), initial_state())
        self.assertEqual(box.turn()["code"], "plan_proposed")

    def test_mailbox_is_bounded(self):
        box = Mailbox(self.store, capacity=1)
        box.submit(self.key, message("m1", "ProposePlan", "r1"))
        with self.assertRaises(Busy):
            box.submit(self.key, message("m2", "ProposePlan", "r2"))
        self.assertEqual(len(box.queue), 1)
        self.assertEqual(self.store.state(self.key), initial_state())

    def test_mailbox_copies_message_at_admission(self):
        box = Mailbox(self.store)
        cmd = message("m1", "ProposePlan", "r1")
        box.submit(self.key, cmd); cmd["revision"] = "changed"
        box.turn()
        self.assertEqual(self.store.state(self.key)["revision"], "r1")

    def test_pending_mailbox_does_not_survive_new_instance(self):
        box = Mailbox(self.store)
        box.submit(self.key, message("m1", "ProposePlan", "r1"))
        replacement = Mailbox(self.store)
        self.assertEqual(len(replacement.queue), 0)
        self.assertEqual(self.store.state(self.key), initial_state())

    def test_mailbox_turns_are_fifo_in_this_local_model(self):
        box = Mailbox(self.store)
        box.submit(self.key, message("m1", "ProposePlan", "r1"))
        box.submit(self.key, message("m2", "ProposePlan", "r2"))
        box.turn(); self.assertEqual(self.store.state(self.key)["revision"], "r1")
        box.turn(); self.assertEqual(self.store.state(self.key)["revision"], "r2")

    def test_zero_mailbox_capacity_is_invalid(self):
        with self.assertRaises(ValueError):
            Mailbox(self.store, capacity=0)

    def test_demo_reports_its_limits(self):
        result = demo()
        self.assertEqual(result["queued_before_restart"], 1)
        self.assertEqual(result["queued_after_restart"], 0)
        self.assertEqual(result["late_result"], "historical_result_saved")
        self.assertEqual(result["phase_after_late"], "waiting_approval")
        self.assertEqual(result["saved_results"], 2)
        self.assertEqual(result["final_phase"], "completed")
        self.assertFalse(result["cluster_contacted"])
        self.assertFalse(result["real_test_executed"])
        self.assertFalse(result["real_human_approval_verified"])


if __name__ == "__main__":
    unittest.main()
