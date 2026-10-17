from dataclasses import replace
import unittest

from ch43_release_acceptance import (
    Binding, Case, REQUIRED, EXPECTED_REPORT, EXPECTED_RESULT, case_from,
    binding_for, demo, digest, gate, observe, oracle, run_suite,
)


class ReleaseAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.good_binding, cls.good = run_suite("report")
        cls.bad_binding, cls.bad = run_suite("exit-zero")

    def holds(self, cases, reason):
        result = gate(self.good_binding, cases)
        self.assertEqual(result["decision"], "HOLD")
        self.assertIn(reason, result["reasons"])
        self.assertFalse(result["production_ready"])
        self.assertEqual(result["deployment_actions"], 0)

    def test_local_pass_is_not_production(self):
        result = gate(self.good_binding, self.good)
        self.assertEqual(result["decision"], "LOCAL_CONTRACT_PASS")
        self.assertEqual(result["reasons"], [])
        self.assertFalse(result["production_ready"])

    def test_green_probes_survive_child_failure(self):
        evidence = self.good[0].evidence
        self.assertEqual((evidence["health"], evidence["ready"], evidence["child_exit"]), (200, 200, 2))
        self.assertFalse(evidence["child_running"])
        self.assertEqual(evidence["classification"], "COMMAND_FAILED")

    def test_expected_child_failure_case_passes(self):
        self.assertEqual(self.good[0].status, "PASS")

    def test_exit_zero_without_result_is_failure(self):
        evidence = self.bad[1].evidence
        self.assertEqual((evidence["health"], evidence["ready"], evidence["child_exit"]), (200, 200, 0))
        self.assertIsNone(evidence["result"])
        self.assertEqual(self.bad[1].status, "FAIL")
        self.assertEqual(gate(self.bad_binding, self.bad)["decision"], "HOLD")

    def test_application_result_is_real_file_content(self):
        self.assertEqual(self.good[1].evidence["result"], EXPECTED_RESULT)
        self.assertEqual(self.good[1].evidence["report"], EXPECTED_REPORT)

    def test_empty_report(self):
        self.holds([], "child-exit-visible:missing_case")

    def test_missing_case(self):
        self.holds(self.good[:1], "application-result:missing_case")

    def test_duplicate_case(self):
        self.holds((*self.good, self.good[0]), "child-exit-visible:duplicate")

    def test_unknown_case(self):
        self.holds((*self.good, replace(self.good[0], name="health-only")), "unknown_case")

    def test_missing_evidence(self):
        self.holds((self.good[0], replace(self.good[1], evidence=None)), "application-result:missing_evidence")

    def test_missing_digest(self):
        self.holds((self.good[0], replace(self.good[1], evidence_sha256=None)), "application-result:evidence_mismatch")

    def test_changed_evidence_with_old_digest(self):
        changed = {**self.good[1].evidence, "report": "different"}
        self.holds((self.good[0], replace(self.good[1], evidence=changed)), "application-result:evidence_mismatch")

    def test_pass_label_does_not_override_oracle(self):
        forged = replace(self.bad[1], binding=self.good_binding, status="PASS")
        self.holds((self.good[0], forged), "application-result:false_pass")

    def test_failure_status_closes_gate(self):
        self.holds((self.good[0], replace(self.good[1], status="FAIL")), "application-result:FAIL")

    def test_blocked_status_closes_gate(self):
        self.holds((self.good[0], replace(self.good[1], status="BLOCKED")), "application-result:BLOCKED")

    def test_skipped_status_closes_gate(self):
        self.holds((self.good[0], replace(self.good[1], status="SKIPPED")), "application-result:SKIPPED")

    def test_unknown_status(self):
        self.holds((self.good[0], replace(self.good[1], status="OK")), "application-result:invalid_status")

    def test_non_string_status(self):
        self.holds((self.good[0], replace(self.good[1], status=[])), "application-result:invalid_status")

    def test_old_candidate(self):
        b = replace(self.good_binding, candidate="0" * 64)
        self.holds((self.good[0], replace(self.good[1], binding=b)), "application-result:binding_mismatch")

    def test_other_suite(self):
        b = replace(self.good_binding, suite="0" * 64)
        self.holds((self.good[0], replace(self.good[1], binding=b)), "application-result:binding_mismatch")

    def test_other_fixtures(self):
        b = replace(self.good_binding, fixtures="0" * 64)
        self.holds((self.good[0], replace(self.good[1], binding=b)), "application-result:binding_mismatch")

    def test_other_environment(self):
        b = replace(self.good_binding, environment="other")
        self.holds((self.good[0], replace(self.good[1], binding=b)), "application-result:binding_mismatch")

    def test_old_trial(self):
        b = replace(self.good_binding, trial="previous-trial")
        self.holds((self.good[0], replace(self.good[1], binding=b)), "application-result:binding_mismatch")

    def test_invalid_expected_binding(self):
        with self.assertRaises(ValueError):
            gate(None, self.good)

    def test_invalid_case(self):
        self.holds((*self.good, "PASS"), "invalid_case")

    def test_binding_rejects_abbreviated_digest(self):
        with self.assertRaises(ValueError):
            replace(self.good_binding, candidate="abcdef")

    def test_binding_rejects_empty_trial(self):
        with self.assertRaises(ValueError):
            replace(self.good_binding, trial="")

    def test_trial_is_fresh(self):
        self.assertNotEqual(binding_for("report").trial, binding_for("report").trial)

    def test_binding_rejects_arbitrary_child_mode(self):
        with self.assertRaises(ValueError):
            binding_for("arbitrary-command")

    def test_observer_rejects_arbitrary_child_mode(self):
        with self.assertRaises(ValueError):
            observe("arbitrary-command")

    def test_actual_process_exit_two_fails_application_case(self):
        self.assertFalse(oracle("application-result", self.good[0].evidence))

    def test_nonzero_even_with_result_fails(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "child_exit": 2}))

    def test_missing_report_file(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "report": None}))

    def test_wrong_report_content(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "report": "empty"}))

    def test_wrong_items(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "result": {**EXPECTED_RESULT, "items": 0}}))

    def test_boolean_items_are_not_integer_count(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "result": {**EXPECTED_RESULT, "items": True}}))

    def test_running_child_is_not_completed(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "child_running": True}))

    def test_boolean_exit_code(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "child_exit": False}))

    def test_health_failure(self):
        self.assertFalse(oracle("application-result", {**self.good[1].evidence, "health": 503}))

    def test_unknown_oracle(self):
        self.assertFalse(oracle("other", self.good[1].evidence))

    def test_non_json_evidence_closes_gate(self):
        self.holds((self.good[0], replace(self.good[1], evidence={"x": object()})), "application-result:evidence_mismatch")

    def test_demo_summary(self):
        output = demo()
        self.assertEqual(output["incident"], {"health": 200, "ready": 200, "child_exit": 0,
                         "result_present": False, "probe_only": "PASS", "application_case": "FAIL"})
        self.assertEqual(output["failed_child_case"], "PASS")
        self.assertEqual(output["gates"]["valid_result"], "LOCAL_CONTRACT_PASS")
        self.assertTrue(all(v == "HOLD" for k, v in output["gates"].items() if k != "valid_result"))
        self.assertFalse(output["production_ready"])
        self.assertFalse(output["real_ax_or_substrate_executed"])
        self.assertTrue(output["loopback_probes_executed"])
        self.assertEqual(output["external_calls"], 0)


if __name__ == "__main__":
    unittest.main()
