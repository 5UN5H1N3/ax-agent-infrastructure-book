"""Explain a supplied resource report; no Kubernetes, RPC or placement occurs.

The teaching model accepts already parsed integer millicores and MiB. It models
equality selectors, ACTIVE state, sandbox class, local-node restriction and
declared CPU/memory/slot accounting. It neither chooses nor reserves a Worker.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping


@dataclass(frozen=True)
class WorkerReport:
    name: str
    node: str
    sandbox_class: str
    state: str
    labels: Mapping[str, str]
    cpu_m: int | None
    memory_mib: int | None
    slots: int
    used_cpu_m: int = 0
    used_memory_mib: int = 0
    used_slots: int = 0


@dataclass(frozen=True)
class PlacementIntent:
    sandbox_class: str
    template_labels: Mapping[str, str]
    actor_labels: Mapping[str, str]
    cpu_m: int
    memory_mib: int
    required_nodes: tuple[str, ...] = ()


def _nonnegative_integer(value):
    return type(value) is int and value >= 0


def explain_fit(worker: WorkerReport, intent: PlacementIntent):
    """Return all failed checks, not a scheduling priority or an RPC response."""
    numbers = (worker.slots, worker.used_slots, worker.used_cpu_m,
               worker.used_memory_mib, intent.cpu_m, intent.memory_mib)
    if not all(_nonnegative_integer(x) for x in numbers):
        raise ValueError("resource counts must be nonnegative integers")
    if any(x is not None and not _nonnegative_integer(x)
           for x in (worker.cpu_m, worker.memory_mib)):
        raise ValueError("capacity must be a nonnegative integer or None")
    reasons = []
    if worker.sandbox_class != intent.sandbox_class:
        reasons.append("sandbox_class")
    if worker.state != "ACTIVE":
        reasons.append("worker_state")
    for surface, selector in (("template", intent.template_labels),
                              ("actor", intent.actor_labels)):
        for key, value in sorted(selector.items()):
            if worker.labels.get(key) != value:
                reasons.append(f"{surface}_label:{key}")
    if intent.required_nodes and worker.node not in intent.required_nodes:
        reasons.append("local_snapshot_node")
    if worker.used_slots >= worker.slots:
        reasons.append("actor_slots")
    for name, capacity, allocated, requested in (
            ("cpu", worker.cpu_m, worker.used_cpu_m, intent.cpu_m),
            ("memory", worker.memory_mib, worker.used_memory_mib,
             intent.memory_mib)):
        # Missing capacity reads as zero, not as an unbounded physical resource.
        free = (capacity if capacity is not None else 0) - allocated
        if requested > 0 and free < requested:
            reasons.append(name)
    return {"worker": worker.name, "candidate": not reasons,
            "failed_checks": reasons}


def guard_result(current_uid, current_version, sent_uid, sent_version):
    """Model only update guards, not full replacement or lifecycle validation."""
    if not sent_uid or type(sent_version) is not int or sent_version < 1:
        return "INVALID_ARGUMENT"
    if sent_uid != current_uid or sent_version != current_version:
        return "ABORTED"
    return "GUARDS_MATCH"


def demo():
    intent = PlacementIntent("gvisor", {"tier": "general"},
                             {"region": "eu"}, 1000, 2048)
    workers = (
        WorkerReport("w-a", "node-a", "gvisor", "ACTIVE",
                     {"tier": "general", "region": "eu"},
                     4000, 8192, 4, 1000, 7168, 1),
        WorkerReport("w-b", "node-b", "gvisor", "ACTIVE",
                     {"tier": "general", "region": "us"}, 4000, 8192, 4),
        WorkerReport("w-c", "node-c", "microvm", "ACTIVE",
                     {"tier": "general", "region": "eu"}, 4000, 8192, 4),
        WorkerReport("w-d", "node-d", "gvisor", "ACTIVE",
                     {"tier": "general", "region": "eu"},
                     4000, 8192, 4, 1000, 2048, 4),
    )
    added = WorkerReport("w-e", "node-e", "gvisor", "ACTIVE",
                         {"tier": "general", "region": "eu"}, 2000, 4096, 4)
    return {
        "source": "offline_supplied_report",
        "cluster_contacted": False,
        "worker_selected_or_reserved": False,
        "initial": [explain_fit(w, intent) for w in workers],
        "added_worker": explain_fit(added, intent),
        "update_guards": {
            "same_lifetime_stale_version": guard_result("uid-a", 18, "uid-a", 17),
            "recreated_name_same_version": guard_result("uid-b", 17, "uid-a", 17),
            "fresh_read": guard_result("uid-b", 17, "uid-b", 17),
        },
    }


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
