"""Tests of a teaching calculator, not of a running Substrate installation."""
from dataclasses import replace
import unittest

from ch13_resource_fit_demo import (
    PlacementIntent, WorkerReport, demo, explain_fit, guard_result,
)


class FitTests(unittest.TestCase):
    def setUp(self):
        self.worker = WorkerReport("w", "node-a", "gvisor", "ACTIVE",
                                   {"tier": "general", "region": "eu"},
                                   4000, 8192, 4)
        self.intent = PlacementIntent("gvisor", {"tier": "general"},
                                      {"region": "eu"}, 1000, 2048)

    def check(self, worker=None, intent=None):
        return explain_fit(worker or self.worker, intent or self.intent)

    def test_exact_fit(self):
        result = self.check(replace(self.worker, cpu_m=1000, memory_mib=2048,
                                    slots=1))
        self.assertTrue(result["candidate"])

    def test_cpu_insufficient(self):
        self.assertEqual(self.check(replace(self.worker, cpu_m=999))["failed_checks"], ["cpu"])

    def test_memory_insufficient(self):
        self.assertEqual(self.check(replace(self.worker, memory_mib=2047))["failed_checks"], ["memory"])

    def test_subtracts_allocation(self):
        self.assertEqual(self.check(replace(self.worker, used_memory_mib=7168))["failed_checks"], ["memory"])

    def test_slots_independent_of_compute(self):
        self.assertEqual(self.check(replace(self.worker, used_slots=4))["failed_checks"], ["actor_slots"])

    def test_no_capacity_report_blocks_assignment(self):
        result = self.check(replace(self.worker, cpu_m=None, memory_mib=None, slots=0))
        self.assertEqual(result["failed_checks"], ["actor_slots", "cpu", "memory"])

    def test_missing_cpu_is_not_unlimited(self):
        self.assertEqual(self.check(replace(self.worker, cpu_m=None))["failed_checks"], ["cpu"])

    def test_missing_memory_is_not_unlimited(self):
        self.assertEqual(self.check(replace(self.worker, memory_mib=None))["failed_checks"], ["memory"])

    def test_zero_capacity_blocks_positive_request(self):
        self.assertEqual(self.check(replace(self.worker, cpu_m=0))["failed_checks"], ["cpu"])

    def test_no_declared_size_still_costs_slot(self):
        intent = replace(self.intent, cpu_m=0, memory_mib=0)
        worker = replace(self.worker, cpu_m=None, memory_mib=None)
        self.assertTrue(self.check(worker, intent)["candidate"])
        self.assertEqual(self.check(replace(worker, used_slots=4), intent)["failed_checks"], ["actor_slots"])

    def test_sandbox_class_is_hard_gate(self):
        self.assertEqual(self.check(replace(self.worker, sandbox_class="microvm"))["failed_checks"], ["sandbox_class"])

    def test_draining_is_not_candidate(self):
        self.assertEqual(self.check(replace(self.worker, state="DRAINING"))["failed_checks"], ["worker_state"])

    def test_unspecified_state_is_not_active(self):
        self.assertFalse(self.check(replace(self.worker, state="UNSPECIFIED"))["candidate"])

    def test_template_selector(self):
        self.assertEqual(self.check(replace(self.worker, labels={"region": "eu"}))["failed_checks"], ["template_label:tier"])

    def test_actor_selector(self):
        self.assertEqual(self.check(replace(self.worker, labels={"tier": "general", "region": "us"}))["failed_checks"], ["actor_label:region"])

    def test_actor_cannot_override_template(self):
        intent = replace(self.intent, actor_labels={"tier": "isolated"})
        self.assertEqual(self.check(intent=intent)["failed_checks"], ["actor_label:tier"])
        worker = replace(self.worker, labels={"tier": "isolated"})
        self.assertEqual(self.check(worker, intent)["failed_checks"], ["template_label:tier"])

    def test_empty_selectors_do_not_require_labels(self):
        self.assertTrue(self.check(replace(self.worker, labels={}),
                                   replace(self.intent, template_labels={}, actor_labels={}))["candidate"])

    def test_local_snapshot_node(self):
        self.assertEqual(self.check(intent=replace(self.intent, required_nodes=("node-b",)))["failed_checks"], ["local_snapshot_node"])

    def test_allowed_local_node(self):
        self.assertTrue(self.check(intent=replace(self.intent, required_nodes=("node-a", "node-b")))["candidate"])

    def test_negative_remaining_does_not_fit(self):
        self.assertFalse(self.check(replace(self.worker, used_cpu_m=5000))["candidate"])

    def test_undeclared_dimension_is_not_compared(self):
        self.assertTrue(self.check(replace(self.worker, used_cpu_m=5000),
                                   replace(self.intent, cpu_m=0))["candidate"])

    def test_rejects_negative_or_noninteger_numbers(self):
        for bad in (-1, 1.5, True, "1000"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.check(replace(self.worker, cpu_m=bad))

    def test_does_not_mutate_reports(self):
        before = replace(self.worker)
        self.check()
        self.assertEqual(self.worker, before)

    def test_demo_diagnostics(self):
        result = demo()
        self.assertEqual([x["failed_checks"] for x in result["initial"]],
                         [["memory"], ["actor_label:region"], ["sandbox_class"], ["actor_slots"]])
        self.assertTrue(result["added_worker"]["candidate"])
        self.assertFalse(result["cluster_contacted"])
        self.assertFalse(result["worker_selected_or_reserved"])


class GuardTests(unittest.TestCase):
    def test_fresh_guards(self):
        self.assertEqual(guard_result("u", 17, "u", 17), "GUARDS_MATCH")

    def test_stale_version(self):
        self.assertEqual(guard_result("u", 18, "u", 17), "ABORTED")

    def test_recreated_name_even_when_version_matches(self):
        self.assertEqual(guard_result("v", 17, "u", 17), "ABORTED")

    def test_missing_uid(self):
        self.assertEqual(guard_result("u", 17, "", 17), "INVALID_ARGUMENT")

    def test_missing_or_invalid_version(self):
        for version in (None, 0, -1, "17", True):
            with self.subTest(version=version):
                self.assertEqual(guard_result("u", 17, "u", version), "INVALID_ARGUMENT")


if __name__ == "__main__":
    unittest.main()
