from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from ch31_retry_boundaries import (ACTION, SCOPE, Action, Scope, Target, Journal,
    ReplyLost, KnownNoEffect, Rejected, CoordinatorStopped, ContractConflict,
    checked_contract, execute, nested_retries, demo)


class FixtureTests(unittest.TestCase):
    def test_digest_is_deterministic(self):
        self.assertEqual(ACTION.digest(), replace(ACTION).digest())
        self.assertEqual(len(ACTION.digest()), 64)

    def test_every_action_field_is_bound(self):
        for field, value in (("tenant", "team-b"), ("repository", "other"),
                             ("input_commit", "c" * 40), ("report_sha256", "d" * 64)):
            with self.subTest(field=field):
                self.assertNotEqual(ACTION.digest(), replace(ACTION, **{field: value}).digest())

    def test_bad_action_fields(self):
        for field, value in (("tenant", ""), ("repository", " x"),
                             ("input_commit", "a" * 7), ("report_sha256", "B" * 64),
                             ("input_commit", None), ("report_sha256", True)):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                replace(ACTION, **{field: value})

    def test_bad_scope(self):
        for tenant, repo in (("", "x"), ("x", " "), (True, "x")):
            with self.subTest(tenant=tenant), self.assertRaises(ValueError): Scope(tenant, repo)

    def test_scope_match(self):
        self.assertEqual(checked_contract(ACTION, SCOPE, ACTION.digest()), ACTION.digest())

    def test_wrong_tenant(self):
        with self.assertRaises(PermissionError): checked_contract(ACTION, Scope("other", "billing"), ACTION.digest())

    def test_wrong_repository(self):
        with self.assertRaises(PermissionError): checked_contract(ACTION, Scope("team-a", "other"), ACTION.digest())

    def test_stale_approval(self):
        changed = replace(ACTION, report_sha256="c" * 64)
        with self.assertRaises(PermissionError): checked_contract(changed, SCOPE, ACTION.digest())

    def test_truthy_approval_is_not_accepted(self):
        for approval in (True, 1, "true", None):
            with self.subTest(approval=approval), self.assertRaises(PermissionError): checked_contract(ACTION, SCOPE, approval)

    def test_request_object_types(self):
        with self.assertRaises(ValueError): checked_contract({}, SCOPE, ACTION.digest())
        with self.assertRaises(ValueError): checked_contract(ACTION, {}, ACTION.digest())


class LocalStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(prefix="ax-ch31-test-")
        self.root = Path(self.tmp.name)
        self.jp, self.tp = self.root / "journal.sqlite", self.root / "target.sqlite"
        self.journal, self.target = Journal(self.jp), Target(self.tp)

    def tearDown(self):
        self.tmp.cleanup()

    def run_action(self, **kwargs):
        return execute(self.journal, self.target, "op-1", ACTION, SCOPE, ACTION.digest(), **kwargs)

    def test_success(self):
        row = self.run_action()
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(row["receipt"], "receipt-1")
        self.assertEqual(self.target.count(), 1)

    def test_repeat_does_not_send(self):
        self.run_action(); self.run_action()
        self.assertEqual(self.target.sends, 1)
        self.assertEqual(self.target.count(), 1)

    def test_lost_reply_is_reconciled(self):
        row = self.run_action(mode="drop_reply")
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(self.target.sends, 1)
        self.assertEqual(self.target.count(), 1)

    def test_crash_leaves_durable_uncertainty(self):
        with self.assertRaises(CoordinatorStopped): self.run_action(mode="crash_after_commit")
        self.assertEqual(self.journal.read("op-1")["state"], "uncertain")
        self.assertEqual(self.target.count(), 1)

    def test_reopen_recovers_without_second_send(self):
        with self.assertRaises(CoordinatorStopped): self.run_action(mode="crash_after_commit")
        reopened = Target(self.tp)
        row = execute(Journal(self.jp), reopened, "op-1", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["attempts"], 1)
        self.assertEqual(reopened.sends, 0)
        self.assertEqual(reopened.count(), 1)

    def test_reopen_done_uses_journal(self):
        self.run_action()
        reopened = Target(self.tp)
        row = execute(Journal(self.jp), reopened, "op-1", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(row["receipt"], "receipt-1")
        self.assertEqual(reopened.sends, 0)

    def test_new_payload_with_old_key_rejected(self):
        self.run_action()
        changed = replace(ACTION, report_sha256="c" * 64)
        with self.assertRaises(ContractConflict):
            execute(self.journal, self.target, "op-1", changed, SCOPE, changed.digest())
        self.assertEqual(self.target.sends, 1)

    def test_budget_cannot_be_reset_under_same_key(self):
        self.run_action(mode="unavailable")
        with self.assertRaises(ContractConflict): self.run_action(budget=3)
        self.assertEqual(self.target.sends, 2)

    def test_budget_exhausted(self):
        row = self.run_action(mode="unavailable")
        self.assertEqual(row["state"], "budget_exhausted")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(self.target.count(), 0)

    def test_budget_survives_reopen(self):
        self.run_action(mode="unavailable")
        reopened = Target(self.tp)
        row = execute(Journal(self.jp), reopened, "op-1", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(row["state"], "budget_exhausted")
        self.assertEqual(reopened.sends, 0)

    def test_known_no_effect_can_be_retried(self):
        original = self.target.send
        def first_unavailable(key, fingerprint, mode):
            return original(key, fingerprint, "unavailable" if self.target.sends == 0 else "ok")
        self.target.send = first_unavailable
        row = self.run_action()
        self.assertEqual(row["state"], "done")
        self.assertEqual(row["attempts"], 2)
        self.assertEqual(self.target.count(), 1)

    def test_denial_not_retried(self):
        row = self.run_action(mode="reject")
        self.assertEqual(row["state"], "rejected")
        self.assertEqual(self.target.sends, 1)
        self.assertEqual(self.target.count(), 0)
        self.run_action()
        self.assertEqual(self.target.sends, 1)

    def test_scope_rejection_precedes_journal(self):
        with self.assertRaises(PermissionError):
            execute(self.journal, self.target, "op-1", ACTION, Scope("team-b", "billing"), ACTION.digest())
        with self.assertRaises(KeyError): self.journal.read("op-1")
        self.assertEqual(self.target.sends, 0)

    def test_stale_approval_precedes_target(self):
        with self.assertRaises(PermissionError): execute(self.journal, self.target, "op-1", ACTION, SCOPE, "0" * 64)
        self.assertEqual(self.target.count(), 0)

    def test_target_itself_deduplicates(self):
        a = self.target.send("op-1", ACTION.digest())
        b = self.target.send("op-1", ACTION.digest())
        self.assertEqual(a, b)
        self.assertEqual(self.target.count(), 1)

    def test_target_rejects_key_payload_mismatch(self):
        self.target.send("op-1", ACTION.digest())
        with self.assertRaises(ContractConflict): self.target.send("op-1", "c" * 64)
        self.assertEqual(self.target.count(), 1)

    def test_lookup_rejects_mismatch(self):
        self.target.send("op-1", ACTION.digest())
        with self.assertRaises(ContractConflict): self.target.lookup("op-1", "c" * 64)

    def test_lookup_missing(self):
        self.assertIsNone(self.target.lookup("op-1", ACTION.digest()))

    def test_legacy_lost_reply_stops(self):
        target = Target(self.root / "legacy.sqlite", False)
        row = execute(self.journal, target, "op-1", ACTION, SCOPE, ACTION.digest(), mode="drop_reply")
        self.assertEqual(row["state"], "manual_review")
        self.assertEqual(target.sends, 1)
        self.assertEqual(target.count(), 1)
        execute(self.journal, target, "op-1", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(target.sends, 1)

    def test_legacy_crash_after_commit_stops_on_reopen(self):
        path = self.root / "legacy.sqlite"
        with self.assertRaises(CoordinatorStopped):
            execute(self.journal, Target(path, False), "op-1", ACTION, SCOPE, ACTION.digest(), mode="crash_after_commit")
        target = Target(path, False)
        row = execute(Journal(self.jp), target, "op-1", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(row["state"], "manual_review")
        self.assertEqual(target.sends, 0)
        self.assertEqual(target.count(), 1)

    def test_legacy_no_effect_can_retry(self):
        row = execute(self.journal, Target(self.root / "legacy.sqlite", False), "op-1", ACTION, SCOPE, ACTION.digest(), mode="unavailable")
        self.assertEqual(row["state"], "budget_exhausted")
        self.assertEqual(row["attempts"], 2)

    def test_legacy_has_no_operation_lookup(self):
        with self.assertRaises(ValueError): Target(self.root / "legacy.sqlite", False).lookup("op-1", ACTION.digest())

    def test_new_key_means_new_operation(self):
        self.run_action()
        execute(self.journal, self.target, "op-2", ACTION, SCOPE, ACTION.digest())
        self.assertEqual(self.target.count(), 2)

    def test_guard_also_precedes_completed_return(self):
        self.run_action()
        with self.assertRaises(PermissionError):
            execute(self.journal, self.target, "op-1", ACTION, Scope("team-b", "billing"), ACTION.digest())

    def test_invalid_budget(self):
        for budget in (True, 0, -1, 11, "2"):
            with self.subTest(budget=budget), self.assertRaises(ValueError): self.run_action(budget=budget)
        self.assertEqual(self.target.sends, 0)

    def test_invalid_key(self):
        for key in ("", " ", "x" * 161):
            with self.subTest(key=key), self.assertRaises(ValueError):
                execute(self.journal, self.target, key, ACTION, SCOPE, ACTION.digest())

    def test_bad_target_mode(self):
        for mode in ("unknown", [], True):
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.run_action(mode=mode)
        self.assertEqual(self.target.sends, 0)

    def test_bad_target_requests(self):
        for key, digest in (("", ACTION.digest()), ("op-1", "bad"), ("op-1", True)):
            with self.subTest(key=key), self.assertRaises(ValueError): self.target.send(key, digest)

    def test_invalid_modeled_backend_types(self):
        with self.assertRaises(ValueError): Target(self.root / "invalid.sqlite", 1)
        with self.assertRaises(ValueError): execute({}, self.target, "op-1", ACTION, SCOPE, ACTION.digest())


class RetryAndDemoTests(unittest.TestCase):
    def test_three_two_attempt_layers_multiply(self):
        calls = []
        def fail():
            calls.append(1); raise ReplyLost()
        with self.assertRaises(ReplyLost): nested_retries(fail)
        self.assertEqual(len(calls), 8)

    def test_early_success_stops_layers(self):
        calls = []
        def succeed():
            calls.append(1); return "receipt"
        self.assertEqual(nested_retries(succeed), "receipt")
        self.assertEqual(len(calls), 1)

    def test_denial_is_not_a_timeout(self):
        calls = []
        def deny():
            calls.append(1); raise Rejected()
        with self.assertRaises(Rejected): nested_retries(deny)
        self.assertEqual(len(calls), 1)

    def test_retry_limits_are_total_attempts(self):
        calls = []
        def fail():
            calls.append(1); raise ReplyLost()
        with self.assertRaises(ReplyLost): nested_retries(fail, (1, 3))
        self.assertEqual(len(calls), 3)

    def test_invalid_retry_layers(self):
        for layers in ((), [2, 2], (True,), (0,), (6,), (2,) * 7):
            with self.subTest(layers=layers), self.assertRaises(ValueError): nested_retries(lambda: None, layers)

    def test_demo_outcomes(self):
        cases = demo()["cases"]
        self.assertEqual(cases["retry_cascade"]["effects"], 8)
        self.assertEqual(cases["bounded_idempotent"]["effects"], 1)
        self.assertEqual(cases["legacy_timeout"]["status"], "manual_review")
        self.assertEqual(cases["restart"]["sends"], 0)
        self.assertEqual(cases["new_key"]["effects"], 2)
        self.assertEqual(cases["outage"]["sends_after_second_run"], 2)

    def test_demo_is_offline(self):
        result = demo()
        self.assertTrue(result["simulated_target"])
        self.assertFalse(result["live_services_executed"])

    def test_demo_is_deterministic(self):
        self.assertEqual(demo(), demo())


if __name__ == "__main__":
    unittest.main()
