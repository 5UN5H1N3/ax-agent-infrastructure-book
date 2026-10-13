"""Classify synthetic, single-container observations; never contact Kubernetes.

Python 3.10+, standard library only. This is a teaching model, not a Kubernetes
API parser, controller, security check, or claim that a task has completed.
"""

from dataclasses import dataclass
import json


class ObservationError(ValueError):
    pass


@dataclass(frozen=True)
class Observation:
    uid: str
    phase: str
    node: str | None = None
    scheduled: bool | None = None
    ready: bool | None = None
    waiting_reason: str | None = None
    deleting: bool = False


def validate_observation(obs, expected_uid):
    if not isinstance(obs, Observation):
        raise ObservationError("expected an Observation")
    for value in (obs.uid, expected_uid):
        if not isinstance(value, str) or not value.strip():
            raise ObservationError("UID must be a nonempty string")
    if obs.phase not in ("Pending", "Running", "Succeeded", "Failed", "Unknown"):
        raise ObservationError("unsupported Pod phase")
    for value in (obs.node, obs.waiting_reason):
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ObservationError("optional strings must be nonempty")
    for value in (obs.scheduled, obs.ready):
        if value is not None and type(value) is not bool:
            raise ObservationError("conditions must be bool or None")
    if type(obs.deleting) is not bool:
        raise ObservationError("deleting must be bool")
    if obs.scheduled is True and obs.node is None:
        raise ObservationError("scheduled observation needs a node")
    if obs.scheduled is False and obs.node is not None:
        raise ObservationError("node assignment conflicts with condition")
    if obs.ready is True and (obs.phase != "Running" or obs.node is None
                             or obs.waiting_reason is not None):
        raise ObservationError("Ready conflicts with this teaching model")


def next_check(obs, expected_uid):
    validate_observation(obs, expected_uid)
    if obs.uid != expected_uid:
        return "identity", "read_new_attempt"
    if obs.deleting:
        return "termination", "observe_shutdown_and_result"
    if obs.phase == "Unknown":
        return "unknown", "refresh_node_and_pod_observation"
    if obs.phase == "Succeeded":
        return "pod_succeeded", "verify_job_and_result_revision"
    if obs.phase == "Failed":
        return "pod_failed", "read_termination_and_events"
    if obs.node is None:
        if obs.scheduled is False:
            return "placement", "read_scheduling_events"
        return "unknown", "read_assignment_and_conditions"
    if obs.waiting_reason is not None:
        return "container_start_or_restart", "read_waiting_reason_and_events"
    if obs.phase != "Running":
        return "unknown", "read_container_and_init_states"
    if obs.ready is False:
        return "readiness", "read_probe_and_application_evidence"
    if obs.ready is None:
        return "unknown", "read_ready_condition"
    return "pod_ready", "verify_service_or_task_contract"


def fixtures():
    return (
        Observation("pod-demo-a", "Pending", scheduled=False),
        Observation("pod-demo-a", "Pending", "node-demo", True, False,
                    "ImagePullBackOff"),
        Observation("pod-demo-a", "Running", "node-demo", True, False),
        Observation("pod-demo-a", "Running", "node-demo", True, True),
        Observation("pod-demo-a", "Succeeded", "node-demo", True, False),
        Observation("pod-demo-b", "Running", "node-demo", True, True),
    )


def demo():
    return {
        "source": "synthetic_fixture",
        "cluster_contacted": False,
        "task_completed": "not_established",
        "checks": [dict(zip(("stage", "next_check"), next_check(obs, "pod-demo-a")))
                   for obs in fixtures()],
    }


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
