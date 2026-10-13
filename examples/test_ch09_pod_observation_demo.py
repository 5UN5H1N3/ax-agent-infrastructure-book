"""Tests of the reduced observation model, not Kubernetes integration tests."""

from dataclasses import replace
import unittest

from ch09_pod_observation_demo import Observation, ObservationError, demo, next_check


class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.obs = Observation("pod-demo-a", "Running", "node-demo", True, True)

    def check(self, obs, stage, evidence):
        self.assertEqual(next_check(obs, "pod-demo-a"), (stage, evidence))

    def reject(self, **changes):
        with self.assertRaises(ObservationError):
            next_check(replace(self.obs, **changes), "pod-demo-a")

    def test_ready_is_not_task_completion(self):
        self.check(self.obs, "pod_ready", "verify_service_or_task_contract")

    def test_no_node_scheduling_false(self):
        self.check(Observation("pod-demo-a", "Pending", scheduled=False),
                   "placement", "read_scheduling_events")

    def test_no_node_missing_condition(self):
        self.check(Observation("pod-demo-a", "Pending"),
                   "unknown", "read_assignment_and_conditions")

    def test_pending_assigned_without_waiting_reason_is_unknown(self):
        self.check(replace(self.obs, phase="Pending", ready=False),
                   "unknown", "read_container_and_init_states")

    def test_image_pull_is_not_scheduler_error(self):
        self.check(replace(self.obs, phase="Pending", ready=False,
                           waiting_reason="ImagePullBackOff"),
                   "container_start_or_restart", "read_waiting_reason_and_events")

    def test_crash_loop_is_not_pod_phase(self):
        self.check(replace(self.obs, ready=False, waiting_reason="CrashLoopBackOff"),
                   "container_start_or_restart", "read_waiting_reason_and_events")

    def test_readiness_false(self):
        self.check(replace(self.obs, ready=False),
                   "readiness", "read_probe_and_application_evidence")

    def test_readiness_missing(self):
        self.check(replace(self.obs, ready=None), "unknown", "read_ready_condition")

    def test_unknown_node_observation(self):
        self.check(replace(self.obs, phase="Unknown", ready=None),
                   "unknown", "refresh_node_and_pod_observation")

    def test_succeeded_requires_result_verification(self):
        self.check(replace(self.obs, phase="Succeeded", ready=False),
                   "pod_succeeded", "verify_job_and_result_revision")

    def test_failed_requires_evidence(self):
        self.check(replace(self.obs, phase="Failed", ready=False),
                   "pod_failed", "read_termination_and_events")

    def test_termination_takes_precedence_over_ready(self):
        self.check(replace(self.obs, deleting=True),
                   "termination", "observe_shutdown_and_result")

    def test_new_uid_requires_new_attempt(self):
        self.check(replace(self.obs, uid="pod-demo-b"), "identity", "read_new_attempt")

    def test_new_uid_takes_precedence_over_deleting(self):
        self.check(replace(self.obs, uid="pod-demo-b", deleting=True),
                   "identity", "read_new_attempt")

    def test_empty_uid(self):
        self.reject(uid="")

    def test_whitespace_uid(self):
        self.reject(uid=" ")

    def test_invalid_expected_uid(self):
        with self.assertRaises(ObservationError):
            next_check(self.obs, None)

    def test_wrong_observation_type(self):
        with self.assertRaises(ObservationError):
            next_check({}, "pod-demo-a")

    def test_invalid_phase(self):
        self.reject(phase="CrashLoopBackOff")

    def test_invalid_node_type(self):
        self.reject(node=10)

    def test_empty_waiting_reason(self):
        self.reject(waiting_reason="")

    def test_scheduled_not_bool(self):
        self.reject(scheduled=1)

    def test_ready_not_bool(self):
        self.reject(ready="False")

    def test_deleting_not_bool(self):
        self.reject(deleting=1)

    def test_scheduled_without_node(self):
        self.reject(node=None, ready=False)

    def test_false_scheduled_with_node(self):
        self.reject(scheduled=False)

    def test_ready_pending(self):
        self.reject(phase="Pending")

    def test_ready_with_waiting_reason(self):
        self.reject(waiting_reason="ContainerCreating")

    def test_input_is_unchanged(self):
        before = repr(self.obs)
        next_check(self.obs, "pod-demo-a")
        self.assertEqual(repr(self.obs), before)

    def test_fixture_disclaims_cluster_and_completion(self):
        result = demo()
        self.assertFalse(result["cluster_contacted"])
        self.assertEqual(result["source"], "synthetic_fixture")
        self.assertEqual(result["task_completed"], "not_established")
        self.assertEqual([c["stage"] for c in result["checks"]],
                         ["placement", "container_start_or_restart", "readiness",
                          "pod_ready", "pod_succeeded", "identity"])


if __name__ == "__main__":
    unittest.main()
