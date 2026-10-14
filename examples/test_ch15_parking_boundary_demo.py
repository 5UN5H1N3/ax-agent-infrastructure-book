import unittest
from ch15_parking_boundary_demo import RETRYABLE, RouterLab, demo, retry_action


class RetryActionTests(unittest.TestCase):
    def action(self, result, elapsed=100, running=False):
        return retry_action(result, elapsed_ms=elapsed, budget_ms=5000, in_flight=running)

    def test_each_retryable_code(self):
        for code in RETRYABLE:
            with self.subTest(code=code):self.assertEqual(self.action(code), "START_NEXT_ATTEMPT")

    def test_nonretryable_codes(self):
        for code in ["NotFound", "PermissionDenied", "Unauthenticated", "DeadlineExceeded", "Internal"]:
            with self.subTest(code=code):self.assertEqual(self.action(code), "FAIL_FAST")

    def test_budget_boundary_stops_new_attempts(self):
        self.assertEqual(self.action("ResourceExhausted", 5000), "STOP_RETRIES")

    def test_inflight_survives_budget(self):
        self.assertEqual(self.action("ResourceExhausted", 6200, True), "WAIT_FOR_IN_FLIGHT")

    def test_late_success_routes(self):
        self.assertEqual(self.action("OK", 6200), "ROUTE")

    def test_invalid_times(self):
        for value in [-1, True, 0.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):self.action("OK",value)

    def test_zero_budget(self):
        with self.assertRaises(ValueError):retry_action("OK",elapsed_ms=0,budget_ms=0,in_flight=False)


class ParkingModelTests(unittest.TestCase):
    def setUp(self):self.router=RouterLab(capacity=1)

    def test_joiners_share_first_attempt(self):
        f=self.router.arrive("a","r1",0)
        self.assertIs(self.router.arrive("a","r2",1),f)
        self.assertEqual(self.router.rpc_starts,1)

    def test_first_attempt_uses_no_lot(self):
        self.router.arrive("a","r1",0)
        self.assertFalse(self.router.parked)

    def test_full_lot_rejects_joiner(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Unavailable",1)
        self.router.arrive("a","r2",2)
        self.assertEqual(self.router.results["r2"],"ROUTER_AT_CAPACITY")

    def test_fast_path_bypasses_full_lot(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Unavailable",1)
        self.router.arrive("b","fast",2)
        self.router.complete_attempt("b","OK",3)
        self.assertEqual(self.router.results["fast"],"ROUTE")
        self.assertEqual(self.router.parked,{"r1"})

    def test_cancel_leader_does_not_remove_flight(self):
        f=self.router.arrive("a","r1",0)
        self.router.leave("a","r1")
        self.assertTrue(f.in_flight)
        self.assertIn("a",self.router.flights)

    def test_all_callers_can_leave_while_attempt_runs(self):
        self.router.arrive("a","r1",0)
        self.router.leave("a","r1","CALLER_TIMEOUT")
        self.router.complete_attempt("a","OK",6000)
        self.assertEqual(self.router.results["r1"],"CALLER_TIMEOUT")
        self.assertFalse(self.router.flights)

    def test_cancel_releases_lot_slot(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Aborted",1)
        self.router.leave("a","r1")
        self.assertFalse(self.router.parked)

    def test_late_success_releases_all_slots(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Aborted",100)
        self.router.next_attempt("a",4900)
        self.router.complete_attempt("a","OK",6200)
        self.assertEqual(self.router.results["r1"],"ROUTE")
        self.assertFalse(self.router.parked)

    def test_budget_stops_retry_between_attempts(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","ResourceExhausted",100)
        self.assertEqual(self.router.next_attempt("a",5000),"STOP_RETRIES")
        self.assertEqual(self.router.rpc_starts,1)
        self.assertFalse(self.router.parked)

    def test_late_retryable_failure_stops(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Unavailable",6200)
        self.assertEqual(self.router.results["r1"],"STOP_RETRIES")

    def test_inflight_does_not_start_another_rpc(self):
        self.router.arrive("a","r1",0)
        self.assertEqual(self.router.next_attempt("a",5100),"WAIT_FOR_IN_FLIGHT")
        self.assertEqual(self.router.rpc_starts,1)

    def test_not_found_uses_no_slot(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","NotFound",1)
        self.assertEqual(self.router.results["r1"],"FAIL_FAST:NotFound")
        self.assertFalse(self.router.parked)

    def test_second_router_has_independent_flight(self):
        self.router.arrive("a","r1",0)
        other=RouterLab();other.arrive("a","r2",0)
        self.assertEqual(self.router.rpc_starts+other.rpc_starts,2)

    def test_completed_flight_is_removed(self):
        f=self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","OK",1)
        self.assertIsNot(self.router.arrive("a","r2",2),f)
        self.assertEqual(self.router.rpc_starts,2)

    def test_request_ids_are_unique(self):
        self.router.arrive("a","r1",0)
        with self.assertRaises(ValueError):self.router.arrive("b","r1",1)

    def test_time_cannot_move_backwards(self):
        self.router.arrive("a","r1",2)
        with self.assertRaises(ValueError):self.router.arrive("b","r2",1)

    def test_completion_requires_running_attempt(self):
        self.router.arrive("a","r1",0)
        self.router.complete_attempt("a","Unavailable",1)
        with self.assertRaises(ValueError):self.router.complete_attempt("a","OK",2)

    def test_invalid_capacity(self):
        for value in [0,-1,True]:
            with self.subTest(value=value),self.assertRaises(ValueError):RouterLab(capacity=value)

    def test_demo_claims(self):
        result=demo()
        self.assertEqual(result["review_flight_attempts"],2)
        self.assertEqual(result["action_after_budget"],"WAIT_FOR_IN_FLIGHT")
        self.assertEqual(result["results"],{"fast":"ROUTE","late":"ROUTE","r1":"CALLER_CANCELED","r2":"ROUTE","r3":"ROUTER_AT_CAPACITY"})
        self.assertFalse(result["substrate_contacted"])
        self.assertEqual(result["parked_after_completion"],0)


if __name__=="__main__":unittest.main()
