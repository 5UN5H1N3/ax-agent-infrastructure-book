from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import json
import sqlite3
from tempfile import TemporaryDirectory
from threading import Barrier
import unittest

import ch37_operation_ledger as m


class OperationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="test-ch37-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.ledger = m.Ledger(self.folder/"ledger.db")
        self.target = m.Target(self.folder/"target.db")
        self.action = m.Action()
        self.gate = m.gate_for()
        self.executor = m.Executor(self.ledger,self.target,self.gate)
        self.ledger.prepare()

    def unknown(self):
        self.assertEqual(self.executor.dispatch(fault="drop_reply"),"OUTCOME_UNKNOWN")

    def test_demo(self):
        result = m.demo()
        self.assertEqual(result,{"key":m.KEY,"after_lost_reply":"OUTCOME_UNKNOWN",
            "after_recovery":"APPLIED","dispatch_attempts":1,"effects_before_lookup":1,
            "effects_after_lookup":1,"current_config":{"revision":"config-18","local_pref":150},
            "goal_verification":"NOT_EVALUATED","network_calls":0})

    def test_success_stops_at_applied(self):
        self.assertEqual(self.executor.dispatch(),"APPLIED")
        self.assertEqual(self.target.effect_count(),1)
        self.assertEqual(json.loads(self.ledger.read()["receipt"])["applied_revision"],"config-18")

    def test_lost_reply_records_unknown_despite_effect(self):
        self.unknown()
        self.assertEqual(self.target.read()["local_pref"],150)
        self.assertIsNone(self.ledger.read()["receipt"])

    def test_recovery_reopens_both_files(self):
        self.unknown()
        resumed = m.Executor(m.Ledger(self.ledger.path),m.Target(self.target.path),self.gate)
        self.assertEqual(resumed.recover(),"APPLIED")
        self.assertEqual(resumed.target.effect_count(),1)
        self.assertEqual(resumed.ledger.read()["attempts"],1)

    def test_stop_after_commit_leaves_dispatching(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="stop_after_commit")
        self.assertEqual(self.ledger.read()["state"],"DISPATCHING")
        self.assertEqual(self.target.effect_count(),1)
        self.assertEqual(self.executor.recover(),"APPLIED")

    def test_stop_before_send_also_leaves_dispatching(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="before_send")
        self.assertEqual(self.ledger.read()["state"],"DISPATCHING")
        self.assertEqual(self.target.effect_count(),0)
        self.assertEqual(self.executor.recover(),"OUTCOME_UNKNOWN")

    def test_absent_receipt_never_automatically_resends(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="before_send")
        for _ in range(3):
            self.assertEqual(self.executor.recover(),"OUTCOME_UNKNOWN")
        self.assertEqual(self.target.effect_count(),0)
        self.assertEqual(self.ledger.read()["attempts"],1)

    def test_explicit_same_key_retry_after_absence(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="before_send")
        self.executor.recover()
        self.assertEqual(self.executor.dispatch(retry=True),"APPLIED")
        self.assertEqual(self.target.effect_count(),1)
        self.assertEqual(self.ledger.read()["attempts"],2)

    def test_explicit_retry_replays_receipt_without_second_effect(self):
        self.unknown()
        self.assertEqual(self.executor.dispatch(retry=True),"APPLIED")
        self.assertEqual(self.target.effect_count(),1)
        self.assertEqual(self.ledger.read()["attempts"],2)

    def test_retry_returns_historical_receipt_before_cas(self):
        receipt = self.target.apply(m.KEY,self.action)
        with m.connection(self.target.path,write=True) as db:
            db.execute("UPDATE config SET revision=19,local_pref=120 WHERE id=1")
        self.assertEqual(self.target.apply(m.KEY,self.action),receipt)
        self.assertEqual(self.target.read(),{"revision":"config-19","local_pref":120})
        self.assertEqual(self.target.effect_count(),1)

    def test_stale_revision_rejected_without_effect(self):
        with m.connection(self.target.path,write=True) as db:
            db.execute("UPDATE config SET revision=18,local_pref=110 WHERE id=1")
        self.assertEqual(self.executor.dispatch(),"CONFLICT")
        self.assertEqual(self.target.effect_count(),0)
        self.assertEqual(self.target.read()["local_pref"],110)

    def test_same_value_without_receipt_is_still_unknown(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="before_send")
        with m.connection(self.target.path,write=True) as db:
            db.execute("UPDATE config SET revision=18,local_pref=150 WHERE id=1")
        self.assertEqual(self.executor.recover(),"OUTCOME_UNKNOWN")

    def test_target_rejects_same_key_with_changed_parameters(self):
        self.target.apply(m.KEY,self.action)
        with self.assertRaisesRegex(m.Conflict,"key_parameter_mismatch"):
            self.target.apply(m.KEY,replace(self.action,local_pref=170))
        self.assertEqual(self.target.effect_count(),1)

    def test_target_lookup_rejects_changed_digest(self):
        self.target.apply(m.KEY,self.action)
        with self.assertRaises(m.Conflict):
            self.target.lookup(m.KEY,replace(self.action,local_pref=170))

    def test_two_target_connections_same_key_one_effect(self):
        barrier = Barrier(2)
        def send(_):
            target = m.Target(self.target.path)
            barrier.wait(timeout=5)
            return target.apply(m.KEY,self.action)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(send,range(2)))
        self.assertEqual(results[0],results[1])
        self.assertEqual(self.target.effect_count(),1)

    def test_two_ledger_connections_one_claim(self):
        barrier = Barrier(2)
        def claim(_):
            ledger = m.Ledger(self.ledger.path)
            barrier.wait(timeout=5)
            try:
                ledger.claim(m.KEY,self.gate,m.ACTOR,10)
                return "claimed"
            except m.Refused:
                return "refused"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(claim,range(2)))
        self.assertCountEqual(results,["claimed","refused"])
        self.assertEqual(self.ledger.read()["attempts"],1)

    def test_prepare_identical_intent_is_repeatable(self):
        m.Ledger(self.ledger.path).prepare()
        self.assertEqual(self.ledger.read()["state"],"PREPARED")

    def test_prepare_rejects_changed_action(self):
        with self.assertRaisesRegex(m.Refused,"intent_changed"):
            self.ledger.prepare(replace(self.action,local_pref=170))

    def test_prepare_rejects_changed_budget(self):
        with self.assertRaisesRegex(m.Refused,"intent_changed"):
            self.ledger.prepare(budget=3)

    def test_same_operation_cannot_get_new_key(self):
        with self.assertRaises(m.Refused):
            self.ledger.prepare(key="fresh-key")

    def test_conflicting_operation_cannot_bypass_unknown(self):
        self.unknown()
        with self.assertRaises(m.Refused):
            self.ledger.prepare(key="new-key",operation="op-18")

    def test_fixture_keeps_reservation_after_applied(self):
        self.executor.dispatch()
        with self.assertRaises(m.Refused):
            self.ledger.prepare(key="new-key",operation="op-18")

    def test_expired_approval_still_allows_authorized_lookup(self):
        self.unknown()
        self.assertEqual(self.executor.recover(now=101),"APPLIED")

    def test_expired_approval_blocks_new_dispatch(self):
        with self.assertRaises(m.Refused):
            self.executor.dispatch(now=100)
        self.assertEqual(self.ledger.read()["attempts"],0)

    def test_revoked_approval_blocks_retry(self):
        self.unknown()
        self.gate.approval = replace(self.gate.approval,revoked=True)
        with self.assertRaises(m.Refused):
            self.executor.dispatch(retry=True)
        self.assertEqual(self.ledger.read()["attempts"],1)
        self.assertEqual(self.target.effect_count(),1)

    def test_policy_denies_write_independently(self):
        self.gate.allow_write = False
        with self.assertRaises(m.Refused):
            self.executor.dispatch()
        self.assertEqual(self.target.effect_count(),0)

    def test_lookup_has_separate_authorization(self):
        self.unknown();self.gate.allow_lookup = False
        with self.assertRaisesRegex(m.Refused,"lookup_denied"):
            self.executor.recover()
        self.assertEqual(self.ledger.read()["state"],"OUTCOME_UNKNOWN")

    def test_all_approval_bindings_are_checked(self):
        for field in ["actor","audience","run","operation","key","digest"]:
            with self.subTest(field=field):
                self.gate.approval = replace(m.gate_for().approval,**{field:"changed"})
                with self.assertRaises(m.Refused):
                    self.executor.dispatch()
        self.assertEqual(self.ledger.read()["attempts"],0)

    def test_wrong_actor_refused(self):
        with self.assertRaises(m.Refused):
            self.ledger.claim(m.KEY,self.gate,"different-actor",10)

    def test_scope_is_checked_separately_from_valid_digest(self):
        action = replace(self.action,tenant="other")
        with self.assertRaisesRegex(m.Refused,"scope_denied"):
            m.gate_for(action).check(m.KEY,action,m.ACTOR,10,write=True)
        with self.assertRaises(m.Conflict):
            self.target.apply(m.KEY,action)

    def test_budget_survives_reopen_and_conservative_stop(self):
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(fault="before_send")
        self.executor.recover()
        with self.assertRaises(m.WorkerStopped):
            self.executor.dispatch(retry=True,fault="before_send")
        self.executor.recover()
        resumed = m.Executor(m.Ledger(self.ledger.path),m.Target(self.target.path),self.gate)
        with self.assertRaisesRegex(m.Refused,"attempt_budget_exhausted"):
            resumed.dispatch(retry=True)
        self.assertEqual(self.target.effect_count(),0)

    def test_ordinary_dispatch_cannot_repeat_unknown(self):
        self.unknown()
        with self.assertRaises(m.Refused):
            self.executor.dispatch()

    def test_retry_cannot_claim_still_dispatching(self):
        self.ledger.claim(m.KEY,self.gate,m.ACTOR,10)
        with self.assertRaises(m.Refused):
            self.executor.dispatch(retry=True)

    def test_accepted_receipt_prevents_second_executor_send(self):
        self.executor.dispatch()
        for retry in [True,False]:
            with self.subTest(retry=retry),self.assertRaises(m.Refused):
                self.executor.dispatch(retry=retry)

    def test_receipt_binding_checked_before_applied(self):
        self.unknown()
        receipt = self.target.lookup(m.KEY,self.action)
        for field,value in [("key","wrong"),("digest","0"*64),
                            ("applied_revision","config-19"),("local_pref",True),
                            ("local_pref",150.0),("extra","unrecognized")]:
            with self.subTest(field=field,value=value):
                with self.assertRaises(m.Refused):
                    self.ledger.accept_receipt(m.KEY,{**receipt,field:value})
        self.assertEqual(self.ledger.read()["state"],"OUTCOME_UNKNOWN")

    def test_receipt_cannot_skip_dispatch(self):
        receipt = self.target.apply(m.KEY,self.action)
        with self.assertRaises(m.Refused):
            self.ledger.accept_receipt(m.KEY,receipt)

    def test_duplicate_receipt_is_repeatable(self):
        self.executor.dispatch()
        receipt = self.target.lookup(m.KEY,self.action)
        self.ledger.accept_receipt(m.KEY,receipt)
        self.assertEqual(self.ledger.read()["attempts"],1)

    def test_receipt_insert_failure_rolls_back_configuration(self):
        with m.connection(self.target.path,write=True) as db:
            db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON receipts "
                       "BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.executor.dispatch()
        self.assertEqual(self.target.read(),{"revision":"config-17","local_pref":100})
        self.assertEqual(self.target.effect_count(),0)
        self.assertEqual(self.ledger.read()["state"],"DISPATCHING")

    def test_unsigned_integer_validation(self):
        for value in [True,False,-1,4294967296,150.0,"150",None]:
            with self.subTest(value=value),self.assertRaises(ValueError):
                replace(self.action,local_pref=value)
        self.assertEqual(replace(self.action,local_pref=4294967295).local_pref,4294967295)

    def test_cidr_validation(self):
        for prefix in ["192.0.2.1/24","garbage",1,None]:
            with self.subTest(prefix=prefix),self.assertRaises(ValueError):
                replace(self.action,prefix=prefix)

    def test_contract_and_revision_validation(self):
        for values in [{"contract":"shell"},{"expected_revision":"config-0"},
                       {"expected_revision":17},{"expected_revision":"config-17\n"},
                       {"device_uid":""},{"tenant":"other tenant"}]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                replace(self.action,**values)

    def test_bounded_keys_and_strict_budgets(self):
        for values in [{"key":"x"*81},{"key":None},{"budget":True},{"budget":0},{"budget":6}]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                self.ledger.prepare(**values)

    def test_clock_and_retry_types(self):
        for values in [{"now":True},{"now":-1},{"now":10.0},{"retry":"yes"}]:
            with self.subTest(values=values),self.assertRaises(ValueError):
                self.executor.dispatch(**values)

    def test_invalid_fault_does_not_consume_attempt(self):
        with self.assertRaises(ValueError):
            self.executor.dispatch(fault="typo")
        self.assertEqual(self.ledger.read()["attempts"],0)

    def test_recovery_does_not_change_terminal_state(self):
        self.executor.dispatch()
        with self.assertRaises(m.Refused):
            self.executor.recover()
        self.assertEqual(self.ledger.read()["state"],"APPLIED")

    def test_digest_has_stable_serialization(self):
        self.assertEqual(self.action.digest(),m.Action(**json.loads(m.encode(m.asdict(self.action)))).digest())
        self.assertNotEqual(self.action.digest(),replace(self.action,local_pref=151).digest())


if __name__ == "__main__":
    unittest.main()
