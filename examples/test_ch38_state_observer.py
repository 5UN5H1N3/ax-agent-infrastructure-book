from dataclasses import replace
import unittest

import ch38_state_observer as m


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.samples = m.fixtures()
        self.observer = m.Observer(m.Identity(), now=100)

    def report(self):
        return self.observer.inspect(self.samples)

    def change(self, source, **values):
        self.samples[source] = replace(self.samples[source], **values)

    def payload(self, source, **values):
        self.change(source, values={**self.samples[source].values, **values})

    def test_incident_has_separate_claims(self):
        r = self.report()
        self.assertEqual(r["claims"]["ax"]["record_phase"], "Running")
        self.assertEqual(r["claims"]["runner"]["command_state"], "EXITED")
        self.assertEqual(r["claims"]["ledger"]["goal_verification"], "NOT_EVALUATED")
        self.assertEqual(r["automatic_mutations"], 0)
        self.assertNotIn("healthy", r)

    def test_fresh_task_read_does_not_refresh_readiness(self):
        self.assertEqual(self.report()["claims"]["ax"]["live_workspace_ready"], "UNKNOWN")

    def test_command_exit_does_not_erase_applied(self):
        self.assertEqual(self.report()["claims"]["ledger"]["operation_state"], "APPLIED")

    def test_old_fact_with_fresh_fetch_is_rejected(self):
        self.change("runner", observed_at=50, fetched_at=99)
        self.assertEqual(self.report()["unknown_sources"]["runner"], "stale_observation")

    def test_exact_age_limit_is_accepted(self):
        self.change("runner", observed_at=90)
        self.assertIn("runner", self.report()["claims"])

    def test_one_missing_source_does_not_erase_others(self):
        del self.samples["runner"]
        self.assertEqual(len(self.report()["claims"]), 3)
        self.assertEqual(self.report()["unknown_sources"], {"runner": "missing"})

    def test_empty_report_has_unknowns_not_negative_facts(self):
        r = self.observer.inspect({})
        self.assertEqual(len(r["unknown_sources"]), 4)
        self.assertEqual(r["claims"], {})

    def test_inventory_absence_requires_successful_observation(self):
        self.payload("substrate", actor_exists=False)
        self.assertIs(self.report()["claims"]["substrate"]["actor_exists"], False)
        self.assertIn("repeat_inventory_and_probe_for_time_skew", self.report()["next_reads"])

    def test_unknown_operation_requires_read_before_repeat(self):
        self.payload("ledger", operation_state="OUTCOME_UNKNOWN")
        self.assertIn("read_operation_receipt_before_any_repeat", self.report()["next_reads"])

    def test_applied_requires_independent_goal_check(self):
        self.assertIn("read_independent_goal_verification", self.report()["next_reads"])

    def test_exit_requests_checkpoint_read(self):
        self.assertIn("read_command_exit_log_and_application_checkpoint", self.report()["next_reads"])

    def test_no_mutation_for_any_missing_source(self):
        for source in list(self.samples):
            with self.subTest(source=source):
                copy = dict(self.samples)
                del copy[source]
                self.assertEqual(self.observer.inspect(copy)["automatic_mutations"], 0)

    def test_each_identity_component_must_match(self):
        for field in vars(m.Identity()):
            with self.subTest(field=field):
                identity = replace(m.Identity(), **{field: "another"})
                self.change("runner", identity=identity)
                self.assertEqual(self.report()["unknown_sources"]["runner"], "identity_mismatch")

    def test_invalid_observation_times(self):
        for a, b in [(101, 101), (99, 98), (-1, 99), (90, 101), (True, 99), (90, False), (float("nan"), 99), (90, float("inf"))]:
            with self.subTest(a=a, b=b):
                self.change("runner", observed_at=a, fetched_at=b)
                self.assertEqual(self.report()["unknown_sources"]["runner"], "invalid_time")

    def test_invalid_observer_configuration(self):
        for params in [{"now": True}, {"now": float("nan")}, {"now": 100, "max_age": 0}, {"now": 100, "max_age": -1}]:
            with self.subTest(params=params), self.assertRaises(ValueError):
                m.Observer(m.Identity(), **params)

    def test_invalid_identity(self):
        for value in ["", 1, "x" * 101]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.Identity(run=value)

    def test_invalid_envelope(self):
        self.samples["runner"] = {"workspace_ready": True}
        self.assertEqual(self.report()["unknown_sources"]["runner"], "invalid_envelope")

    def test_invalid_payload_types(self):
        cases = [("substrate", {"actor_exists": 1}), ("runner", {"workspace_ready": "True"}),
                 ("runner", {"exit_code": True}), ("runner", {"exit_code": None}),
                 ("runner", {"command_state": "done"}), ("ax", {"workspace_condition": False}),
                 ("ax", {"phase": ""}), ("ledger", {"operation_state": "SUCCESS"}),
                 ("ledger", {"goal_verification": "unknown"})]
        for source, values in cases:
            with self.subTest(source=source, values=values):
                self.samples = m.fixtures()
                self.payload(source, **values)
                self.assertEqual(self.report()["unknown_sources"][source], "invalid_payload")

    def test_missing_fields_are_not_success(self):
        for source in self.samples:
            with self.subTest(source=source):
                copy = dict(self.samples)
                copy[source] = replace(copy[source], values={})
                self.assertEqual(self.observer.inspect(copy)["unknown_sources"][source], "invalid_payload")

    def test_goal_pass_cannot_accompany_unknown_write(self):
        self.payload("ledger", operation_state="OUTCOME_UNKNOWN", goal_verification="PASSED")
        self.assertEqual(self.report()["unknown_sources"]["ledger"], "invalid_payload")

    def test_false_readiness_is_preserved(self):
        self.payload("runner", workspace_ready=False, command_state="RUNNING", exit_code=None)
        self.assertIs(self.report()["claims"]["runner"]["workspace_ready"], False)
        self.assertIn("read_workspace_setup_log", self.report()["next_reads"])

    def test_running_command_cannot_have_exit_code(self):
        self.payload("runner", command_state="RUNNING", exit_code=0)
        self.assertEqual(self.report()["unknown_sources"]["runner"], "invalid_payload")

    def test_exit_zero_still_does_not_prove_goal(self):
        self.payload("runner", exit_code=0)
        self.assertEqual(self.report()["claims"]["ledger"]["goal_verification"], "NOT_EVALUATED")

    def test_unknown_command_requests_evidence(self):
        self.payload("runner", command_state="UNKNOWN", exit_code=None)
        self.assertIn("read_command_evidence", self.report()["next_reads"])

    def test_demo(self):
        r = m.demo()
        self.assertEqual(r["old_worker"], {"runner": "identity_mismatch"})
        self.assertEqual(r["fresh_fetch_old_fact"], {"runner": "stale_observation"})
        self.assertIsNone(r["cache_after_lost_watch"])
        self.assertEqual(r["network_calls"], 0)


class CacheTests(unittest.TestCase):
    def test_read_is_available_until_deadline(self):
        c = m.DirtyCache()
        self.assertTrue(c.finish_read(c.begin_read(), {"phase": "Running"}, now=100))
        self.assertIsNotNone(c.get(now=104))
        self.assertIsNone(c.get(now=105))

    def test_invalidate_clears_value(self):
        c = m.DirtyCache()
        c.finish_read(c.begin_read(), "old", now=100)
        c.invalidate()
        self.assertIsNone(c.get(now=100))

    def test_notification_during_read_rejects_its_result(self):
        c = m.DirtyCache()
        ticket = c.begin_read()
        c.invalidate()
        self.assertFalse(c.finish_read(ticket, "old", now=100))
        self.assertIsNone(c.get(now=100))

    def test_reconnect_requires_new_read(self):
        c = m.DirtyCache()
        c.finish_read(c.begin_read(), "before_disconnect", now=100)
        c.invalidate()
        self.assertTrue(c.finish_read(c.begin_read(), "after_reconnect", now=102))
        self.assertEqual(c.get(now=102), "after_reconnect")

    def test_duplicate_notifications_can_be_coalesced(self):
        c = m.DirtyCache()
        for _ in range(10):
            c.invalidate()
        ticket = c.begin_read()
        self.assertTrue(c.finish_read(ticket, "one_read", now=100))
        self.assertEqual(c.get(now=100), "one_read")

    def test_lost_notification_still_expires_cache(self):
        self.assertIsNone(m.demo()["cache_after_lost_watch"])

    def test_boolean_ticket_is_rejected(self):
        c = m.DirtyCache()
        c.invalidate()
        self.assertFalse(c.finish_read(True, "bad", now=100))

    def test_invalid_cache_settings(self):
        for value in [0, -1, True, float("inf")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                m.DirtyCache(poll_interval=value)

    def test_invalid_cache_clock(self):
        c = m.DirtyCache()
        for value in [-1, True, float("nan")]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                c.get(now=value)


if __name__ == "__main__":
    unittest.main()
