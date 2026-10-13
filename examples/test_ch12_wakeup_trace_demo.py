import unittest
from ch12_wakeup_trace_demo import (
    Attempt, boot_source, caller_result, demo, flight_result, restore_result, wakeup_gate,
)


class FlightTests(unittest.TestCase):
    def test_first_success_does_not_park(self):
        self.assertFalse(flight_result([Attempt(0, 20, "ok")])["parked"])

    def test_attempt_started_before_budget_can_finish_late(self):
        f = flight_result([Attempt(0, 10, "retryable"), Attempt(100, 5200, "ok")])
        self.assertEqual((f["outcome"], f["finished_ms"], f["attempts"]),
                         ("routable", 5200, 2))
        self.assertTrue(f["served_late"])

    def test_first_slow_success_does_not_take_parking_slot(self):
        f = flight_result([Attempt(0, 5200, "ok")])
        self.assertFalse(f["parked"])

    def test_retry_at_budget_is_not_started(self):
        f = flight_result([Attempt(0, 10, "retryable"), Attempt(5000, 5200, "ok")])
        self.assertEqual((f["outcome"], f["attempts"], f["finished_ms"]),
                         ("budget_exhausted", 1, 5000))

    def test_late_retryable_error_exhausts_after_attempt(self):
        f = flight_result([Attempt(0, 5200, "retryable")])
        self.assertEqual((f["outcome"], f["finished_ms"]), ("budget_exhausted", 5200))

    def test_fatal_error_not_retried(self):
        self.assertEqual(flight_result([Attempt(0, 20, "fatal")])["outcome"], "error")

    def test_empty_trace_rejected(self):
        with self.assertRaises(ValueError):
            flight_result([])

    def test_invalid_budget_rejected(self):
        with self.assertRaises(ValueError):
            flight_result([Attempt(0, 1, "ok")], 0)

    def test_overlap_rejected(self):
        with self.assertRaises(ValueError):
            flight_result([Attempt(0, 20, "retryable"), Attempt(10, 40, "ok")])

    def test_unfinished_trace_rejected(self):
        with self.assertRaises(ValueError):
            flight_result([Attempt(0, 20, "retryable")])

    def test_continuation_after_success_rejected(self):
        with self.assertRaises(ValueError):
            flight_result([Attempt(0, 10, "ok"), Attempt(20, 30, "ok")])

    def test_negative_and_unknown_trace_rejected(self):
        for attempt in [Attempt(-1, 1, "ok"), Attempt(0, -1, "ok"), Attempt(0, 1, "other")]:
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                flight_result([attempt])


class CallerTests(unittest.TestCase):
    def setUp(self):
        self.flight = flight_result([Attempt(0, 10, "retryable"), Attempt(100, 5200, "ok")])

    def test_late_joiner_shares_completion_not_fresh_budget(self):
        self.assertEqual(caller_result(self.flight, 4900, 14900)["wait_ms"], 300)

    def test_short_deadline_does_not_change_flight(self):
        before = dict(self.flight)
        result = caller_result(self.flight, 4900, 5100)
        self.assertEqual(result["outcome"], "caller_timeout")
        self.assertTrue(result["flight_outlives_caller"])
        self.assertEqual(self.flight, before)

    def test_completion_at_deadline_is_observed(self):
        self.assertEqual(caller_result(self.flight, 0, 5200)["outcome"], "routable")

    def test_caller_cannot_join_finished_flight(self):
        with self.assertRaises(ValueError):
            caller_result(self.flight, 5200, 5300)

    def test_deadline_must_follow_arrival(self):
        with self.assertRaises(ValueError):
            caller_result(self.flight, 100, 100)


class RestoreTests(unittest.TestCase):
    def test_local_has_priority(self):
        self.assertEqual(boot_source(True, True), "local_restore")

    def test_external_selected_when_declared(self):
        self.assertEqual(boot_source(False, True), "external_restore")

    def test_cold_only_when_no_source(self):
        self.assertEqual(boot_source(), "cold_boot")

    def test_failed_external_has_no_cold_fallback(self):
        self.assertEqual(restore_result("external_restore", False),
                         {"state": "CRASHED", "automatic_cold_fallback": False})

    def test_success_does_not_mean_business_done(self):
        self.assertEqual(restore_result("external_restore", True)["state"], "RUNNING")

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError):
            restore_result("backup_guess", True)


class ProbeTests(unittest.TestCase):
    def test_all_probed_still_not_continuous_health(self):
        result = wakeup_gate([True, True])
        self.assertTrue(result["all_containers_probed"])
        self.assertFalse(result["continuous_health_verified"])
        self.assertFalse(result["business_safety_verified"])

    def test_absent_probe_not_inferred_as_passed(self):
        result = wakeup_gate([True, None])
        self.assertEqual(result["state"], "RUNNING")
        self.assertFalse(result["all_containers_probed"])

    def test_failed_probe_crashes_wakeup(self):
        self.assertEqual(wakeup_gate([True, False])["state"], "CRASHED")

    def test_all_absent_can_run_without_readiness_evidence(self):
        result = wakeup_gate([None, None])
        self.assertEqual(result["state"], "RUNNING")
        self.assertFalse(result["all_containers_probed"])

    def test_empty_or_non_bool_probe_rejected(self):
        for value in [[], [1], ["200"]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                wakeup_gate(value)

    def test_demo_declares_actual_scope(self):
        result = demo()
        self.assertFalse(result["cluster_contacted"])
        self.assertFalse(result["clock_or_latency_measured"])
        self.assertEqual(result["shared_flight"]["finished_ms"], 5200)


if __name__ == "__main__":
    unittest.main()
