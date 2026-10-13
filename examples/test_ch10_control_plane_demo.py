from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
from pathlib import Path
import tempfile
import unittest

from ch10_control_plane_demo import (Conflict, Crash, Provider, accept, canonical,
                                    cas, connect, demo, initialize, publish,
                                    read, reconcile, unfinished)


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ax-ch10-test-")
        self.addCleanup(self.directory.cleanup)
        self.local_path = Path(self.directory.name)/"local.db"
        self.service_path = Path(self.directory.name)/"service.db"
        self.local, self.service = connect(self.local_path), connect(self.service_path)
        self.addCleanup(self.local.close)
        self.addCleanup(self.service.close)
        initialize(self.local, self.service)
        self.payload = {"job":"job-42", "revision":"demo-fixture-v1", "test":"test_empty_list"}
        self.provider = Provider(self.service)

    def accept(self, key="key", tenant="tenant", payload=None):
        return accept(self.local, tenant, key, self.payload if payload is None else payload)

    def count(self, db, table):
        return db.execute("SELECT count(*) FROM "+table).fetchone()[0]

    def test_accept_persists_operation(self):
        self.assertEqual(read(self.local,self.accept())["state"],"accepted")

    def test_accept_and_outbox_committed_together(self):
        op=self.accept()
        self.assertEqual(self.local.execute("SELECT operation_id FROM outbox").fetchone()[0],op)

    def test_crash_before_outbox_rolls_back_both(self):
        with self.assertRaises(Crash):
            accept(self.local,"tenant","key",self.payload,crash_before_outbox=True)
        self.assertEqual(self.count(self.local,"operations"),0)
        self.assertEqual(self.count(self.local,"outbox"),0)

    def test_same_key_same_payload_same_operation(self):
        self.assertEqual(self.accept(),self.accept())
        self.assertEqual(self.count(self.local,"operations"),1)
        self.assertEqual(self.count(self.local,"outbox"),1)

    def test_same_key_other_payload_conflicts(self):
        self.accept()
        with self.assertRaises(Conflict):
            self.accept(payload={**self.payload,"revision":"other"})
        self.assertEqual(self.count(self.local,"outbox"),1)

    def test_payload_key_order_irrelevant(self):
        self.assertEqual(self.accept(),self.accept(payload=dict(reversed(list(self.payload.items())))))

    def test_new_key_means_new_effect(self):
        a,b=self.accept(),self.accept(key="other")
        self.assertNotEqual(a,b)
        reconcile(self.local,self.provider,a);reconcile(self.local,self.provider,b)
        self.assertEqual(self.count(self.service,"jobs"),2)

    def test_tenant_scope_separates_keys(self):
        self.assertNotEqual(self.accept(),self.accept(tenant="other"))

    def test_invalid_payload_fields(self):
        for p in [{}, {**self.payload,"unexpected":"x"}]:
            with self.assertRaises(ValueError): self.accept(payload=p)

    def test_invalid_payload_values(self):
        for value in [None,True,1,""," "]:
            with self.assertRaises(ValueError): self.accept(payload={**self.payload,"revision":value})

    def test_invalid_identity(self):
        for value in [None,True,""," "]:
            with self.assertRaises(ValueError): self.accept(key=value)

    def test_accept_does_not_mutate_request(self):
        before=self.payload.copy();self.accept();self.assertEqual(before,self.payload)

    def test_read_missing_operation(self):
        with self.assertRaises(KeyError):read(self.local,"missing")

    def test_outbox_duplicate_after_send_crash(self):
        op=self.accept();broker=[]
        with self.assertRaises(Crash):publish(self.local,broker,crash_after_send=True)
        publish(self.local,broker)
        self.assertEqual(broker,[op,op])

    def test_marked_outbox_not_republished(self):
        self.accept();broker=[];publish(self.local,broker);publish(self.local,broker)
        self.assertEqual(len(broker),1)

    def test_rescan_recovers_without_broker(self):
        op=self.accept();broker=[];publish(self.local,broker);broker.clear()
        self.assertEqual(unfinished(self.local),[op])
        reconcile(self.local,self.provider,unfinished(self.local)[0])
        self.assertEqual(unfinished(self.local),[])

    def test_normal_reconcile_confirms_provider_object(self):
        op=self.accept();self.assertEqual(reconcile(self.local,self.provider,op),"confirmed")
        self.assertEqual(read(self.local,op)["external_id"],"1")

    def test_lost_response_is_unknown_not_failed(self):
        op=self.accept();self.provider.lose_response=True
        self.assertEqual(reconcile(self.local,self.provider,op),"unknown")
        self.assertEqual(read(self.local,op)["state"],"unknown")
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_reopen_recovers_existing_object(self):
        op=self.accept();self.provider.lose_response=True
        reconcile(self.local,self.provider,op)
        with closing(connect(self.local_path)) as local, closing(connect(self.service_path)) as service:
            self.assertEqual(reconcile(local,Provider(service),op),"confirmed")
            self.assertEqual(self.count(service,"jobs"),1)

    def test_crash_after_effect_before_journal(self):
        op=self.accept()
        with self.assertRaises(Crash):reconcile(self.local,self.provider,op,crash_after_effect=True)
        self.assertEqual(read(self.local,op)["state"],"dispatching")
        self.assertEqual(reconcile(self.local,self.provider,op),"confirmed")
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_repeated_delivery_has_one_effect(self):
        op=self.accept()
        for _ in range(5):reconcile(self.local,self.provider,op)
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_provider_idempotency_independent_of_journal(self):
        op=self.accept();encoded=canonical(self.payload)
        self.assertEqual(self.provider.ensure(op,encoded)["id"],self.provider.ensure(op,encoded)["id"])
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_provider_rejects_payload_conflict(self):
        op=self.accept();self.provider.ensure(op,canonical(self.payload))
        with self.assertRaises(Conflict):self.provider.ensure(op,canonical({**self.payload,"test":"other"}))

    def test_recovery_rejects_mismatched_evidence(self):
        op=self.accept();self.provider.ensure(op,canonical({**self.payload,"test":"other"}))
        with self.assertRaises(Conflict):reconcile(self.local,self.provider,op)
        self.assertNotEqual(read(self.local,op)["state"],"confirmed")

    def test_unavailable_lookup_does_not_create(self):
        op=self.accept();self.provider.lookup_available=False
        self.assertEqual(reconcile(self.local,self.provider,op),"unknown")
        self.assertEqual(self.count(self.service,"jobs"),0)

    def test_unavailable_lookup_after_effect_does_not_duplicate(self):
        op=self.accept();self.provider.lose_response=True
        reconcile(self.local,self.provider,op);self.provider.lookup_available=False
        self.assertEqual(reconcile(self.local,self.provider,op),"unknown")
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_unknown_absence_then_safe_same_key_create(self):
        op=self.accept();self.provider.lookup_available=False;reconcile(self.local,self.provider,op)
        self.provider.lookup_available=True
        self.assertEqual(reconcile(self.local,self.provider,op),"confirmed")

    def test_stale_version_cannot_overwrite(self):
        op=self.accept();old=read(self.local,op)
        self.assertTrue(cas(self.local,old,"dispatching"))
        self.assertFalse(cas(self.local,old,"dispatching"))
        self.assertEqual(read(self.local,op)["version"],1)

    def test_confirmation_needs_evidence(self):
        op=self.accept();cas(self.local,read(self.local,op),"dispatching")
        with self.assertRaises(ValueError):cas(self.local,read(self.local,op),"confirmed")

    def test_terminal_state_cannot_regress(self):
        op=self.accept();reconcile(self.local,self.provider,op)
        with self.assertRaises(ValueError):cas(self.local,read(self.local,op),"unknown")

    def test_two_connections_admit_same_key_once(self):
        def admit():
            with closing(connect(self.local_path)) as db:
                return accept(db,"tenant","key",self.payload)
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:admit(),range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.count(self.local,"operations"),1)

    def test_two_provider_connections_create_once(self):
        op=self.accept();encoded=canonical(self.payload)
        def ensure():
            with closing(connect(self.service_path)) as db:
                return Provider(db).ensure(op,encoded)["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:ids=list(pool.map(lambda _:ensure(),range(2)))
        self.assertEqual(ids[0],ids[1]);self.assertEqual(self.count(self.service,"jobs"),1)

    def test_two_accepts_with_conflicting_payload(self):
        def admit(revision):
            with closing(connect(self.local_path)) as db:
                try:return accept(db,"tenant","key",{**self.payload,"revision":revision})
                except Conflict:return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(admit,["first","second"]))
        self.assertEqual(results.count("conflict"),1)
        self.assertEqual(self.count(self.local,"operations"),1)

    def test_accept_retry_does_not_regress_confirmed(self):
        op=self.accept();reconcile(self.local,self.provider,op);self.accept()
        self.assertEqual(read(self.local,op)["state"],"confirmed")

    def test_conflict_during_external_call_requires_recheck(self):
        op=self.accept();original=self.provider.lookup
        def interleaved_lookup(identity):
            cas(self.local,read(self.local,identity),"unknown")
            return original(identity)
        self.provider.lookup=interleaved_lookup
        self.assertEqual(reconcile(self.local,self.provider,op),"recheck")
        self.provider.lookup=original
        self.assertEqual(reconcile(self.local,self.provider,op),"confirmed")
        self.assertEqual(self.count(self.service,"jobs"),1)

    def test_demo_is_deterministic_and_not_test_execution(self):
        result=demo();self.assertEqual(result,demo())
        self.assertEqual(result["provider_jobs"],1)
        self.assertEqual(result["deliveries_for_one_outbox_row"],2)
        self.assertEqual(result["after_lost_response"],"unknown")
        self.assertEqual(result["after_reopen"],"confirmed")
        self.assertEqual(result["test_result"],"not_executed")
        self.assertEqual(result["review_completed"],"not_established")


if __name__ == "__main__":
    unittest.main()
