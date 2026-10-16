"""Offline review of a synthetic lab passport and gate reports.

No shell, Docker, kubectl, KVM, Proxmox, AX, Substrate or network calls.
Records are unsigned trusted fixtures. A complete set of records is NOT proof
that a real laboratory was run or that the pinned pair is compatible.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import ipaddress
import itertools
import json
import re


AX = "ac2332829f22360ff97b0ba34d94dd0dd782f17e"
SUBSTRATE = "df788825e6fc13dd7aa5fd283c8f14bd4c818247"


@dataclass(frozen=True)
class Plan:
    lab_id: str = "DEMO-ONLY-ax26"
    kube_context: str = "kind-ax26"
    runtime: str = "gvisor"
    ax: str = AX
    substrate: str = SUBSTRATE
    runner: str = "example.invalid/runner@sha256:" + "b" * 64
    networks: tuple[tuple[str, str], ...] = (
        ("management", "192.0.2.0/24"),
        ("docker", "172.30.0.0/16"),
        ("pods", "10.240.0.0/16"),
        ("services", "10.241.0.0/16"),
    )

    def validate(self):
        if not self.lab_id or not self.kube_context:
            raise ValueError("missing_target")
        if self.runtime not in {"gvisor", "microvm"}:
            raise ValueError("unknown_runtime")
        if not all(re.fullmatch("[0-9a-f]{40}", value)
                   for value in (self.ax, self.substrate)):
            raise ValueError("unpinned_source")
        if not re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", self.runner):
            raise ValueError("unpinned_image")
        if len(self.networks) != 4 or {n for n, _ in self.networks} != {
            "management", "docker", "pods", "services"
        }:
            raise ValueError("invalid_network_set")
        try:
            nets = [ipaddress.ip_network(cidr, strict=True) for _, cidr in self.networks]
        except ValueError as error:
            raise ValueError("invalid_cidr") from error
        if any(n.version != 4 for n in nets):
            raise ValueError("ipv4_model_only")
        if any(a.overlaps(b) for a, b in itertools.combinations(nets, 2)):
            raise ValueError("network_overlap")

    def digest(self):
        self.validate()
        payload = asdict(self)
        payload["networks"] = sorted(self.networks)
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()


@dataclass(frozen=True)
class Report:
    gate: str
    run_id: str
    plan_digest: str
    observed_at: int
    status: str


def required_gates(plan):
    return ("guest",) + (("kvm",) if plan.runtime == "microvm" else ()) + (
        "kubernetes", "substrate", "ax_integration")


def review(plan, reports, run_id, now, max_age=30):
    """Find the first unproven gate, never certify a live platform."""
    digest = plan.digest()
    if (not run_id or type(now) is not int or type(max_age) is not int
            or now < 0 or max_age < 0):
        raise ValueError("invalid_review_scope")
    gates = required_gates(plan)
    if any(r.gate not in gates or r.status not in {"passed", "failed", "not_run"}
           or type(r.observed_at) is not int or r.observed_at < 0
           for r in reports):
        raise ValueError("invalid_report")
    if len({r.gate for r in reports}) != len(reports):
        raise ValueError("duplicate_gate")
    by_gate = {r.gate: r for r in reports}
    for gate in gates:
        record = by_gate.get(gate)
        if record is None or record.status == "not_run":
            return {"gate": gate, "result": "missing", "compatibility_proven": False}
        if record.run_id != run_id:
            return {"gate": gate, "result": "wrong_run", "compatibility_proven": False}
        if record.plan_digest != digest:
            return {"gate": gate, "result": "wrong_plan", "compatibility_proven": False}
        if record.observed_at > now or now - record.observed_at > max_age:
            return {"gate": gate, "result": "stale_or_future", "compatibility_proven": False}
        if record.status == "failed":
            return {"gate": gate, "result": "failed", "compatibility_proven": False}
    return {"gate": "-", "result": "records_complete", "compatibility_proven": False}


def failure_domains(nodes):
    """Count declared guest and host identities; do not infer HA or quorum."""
    # (node, guest VM identity, physical host identity), all trusted fixtures.
    if not nodes or any(not n for n, _, _ in nodes) or len({n for n, _, _ in nodes}) != len(nodes):
        raise ValueError("invalid_nodes")
    guest_hosts = {}
    for _, guest, host in nodes:
        if not guest or not host or (guest in guest_hosts and guest_hosts[guest] != host):
            raise ValueError("invalid_placement")
        guest_hosts[guest] = host
    return {"nodes": len(nodes), "guest_domains": len(guest_hosts),
            "host_domains": len(set(guest_hosts.values())), "ha_proven": False}


def passed(plan, count=None, run_id="run-1", observed_at=10):
    gates = required_gates(plan)
    chosen = gates if count is None else gates[:count]
    return [Report(g, run_id, plan.digest(), observed_at, "passed") for g in chosen]


def demo():
    plan = Plan()
    rows = {}
    scenarios = {
        "no_evidence": (plan, []),
        "guest_passed": (plan, passed(plan, 1)),
        "cluster_passed": (plan, passed(plan, 2)),
        "substrate_passed": (plan, passed(plan, 3)),
        "synthetic_complete": (plan, passed(plan)),
        "foreign_run": (plan, passed(plan, run_id="old-run")),
        "changed_plan": (replace(plan, kube_context="kind-other"), passed(plan)),
        "overlap": (replace(plan, networks=tuple(
            (name, "10.240.0.0/24" if name == "services" else cidr)
            for name, cidr in plan.networks)), []),
        "microvm_guest_passed": (replace(plan, runtime="microvm"),
                                 passed(replace(plan, runtime="microvm"), 1)),
        "stale_report": (plan, passed(plan, observed_at=0)),
    }
    for name, (candidate, reports) in scenarios.items():
        try:
            rows[name] = review(candidate, reports, "run-1", 40)
        except ValueError as error:
            rows[name] = {"gate": "-", "result": str(error), "compatibility_proven": False}
    placements = {
        "three_kind_nodes": [(f"node-{i}", "vm-1", "host-1") for i in range(3)],
        "three_vms_one_host": [(f"node-{i}", f"vm-{i}", "host-1") for i in range(3)],
        "three_vms_three_hosts": [(f"node-{i}", f"vm-{i}", f"host-{i}") for i in range(3)],
    }
    return {"reviews": rows, "placement": {k: failure_domains(v) for k, v in placements.items()},
            "live_lab_executed": False}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
