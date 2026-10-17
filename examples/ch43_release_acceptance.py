"""Local POSIX process experiment and bounded acceptance-report validator.

Uses chapter 18's loopback-only LocalRunner, not Go AX or a real deployment.
Only owned children and TemporaryDirectory files are used. No cluster, model,
external tool, publication or recovery call. Reports come from a trusted local
collector. Hashes detect accidental content mismatch, NOT forgery or origin.
LOCAL_CONTRACT_PASS applies only to the two cases below, never production.
"""
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

import ch18_runner_observation as runner_module
from ch18_runner_observation import LocalRunner


REQUIRED = ("child-exit-visible", "application-result")
INPUT = "TODO: verify route\n"
EXPECTED_REPORT = "1: TODO: verify route\n"
EXPECTED_RESULT = {"stage": "completed", "report": "report.txt", "items": 1}


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"),
                     ensure_ascii=True, allow_nan=False).encode("ascii")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class Binding:
    candidate: str
    suite: str
    fixtures: str
    environment: str
    trial: str

    def __post_init__(self):
        if any(type(v) is not str or re.fullmatch("[0-9a-f]{64}", v) is None
               for v in (self.candidate, self.suite, self.fixtures)):
            raise ValueError("full SHA-256 identifiers required")
        if any(type(v) is not str or not 1 <= len(v) <= 100
               for v in (self.environment, self.trial)):
            raise ValueError("bounded environment and trial required")


@dataclass(frozen=True)
class Case:
    binding: Binding
    name: str
    status: str
    evidence: dict | None
    evidence_sha256: str | None


def oracle(name, evidence):
    """Recompute the narrow result, rather than trusting a PASS label."""
    if type(evidence) is not dict:
        return False
    common = (type(evidence.get("health")) is int and evidence["health"] == 200
              and type(evidence.get("ready")) is int and evidence["ready"] == 200
              and type(evidence.get("child_exit")) is int
              and evidence.get("child_running") is False)
    if name == "child-exit-visible":
        return (common and evidence["child_exit"] == 2
                and evidence.get("classification") == "COMMAND_FAILED")
    if name == "application-result":
        result = evidence.get("result")
        return (common and evidence["child_exit"] == 0
                and type(result) is dict and result == EXPECTED_RESULT
                and type(result.get("items")) is int
                and evidence.get("report") == EXPECTED_REPORT)
    return False


def gate(expected, cases):
    if type(expected) is not Binding:
        raise ValueError("trusted expected binding required")
    reasons = []
    seen = set()
    for case in cases:
        if type(case) is not Case:
            reasons.append("invalid_case")
            continue
        if type(case.name) is not str or case.name not in REQUIRED:
            reasons.append("unknown_case")
            continue
        if case.name in seen:
            reasons.append(case.name + ":duplicate")
        seen.add(case.name)
        if case.binding != expected:
            reasons.append(case.name + ":binding_mismatch")
        if type(case.status) is not str or case.status not in {"PASS", "FAIL", "BLOCKED", "SKIPPED"}:
            reasons.append(case.name + ":invalid_status")
        elif case.status != "PASS":
            reasons.append(case.name + ":" + case.status)
        if type(case.evidence) is not dict:
            reasons.append(case.name + ":missing_evidence")
            continue
        try:
            matches = digest(case.evidence) == case.evidence_sha256
        except (TypeError, ValueError):
            matches = False
        if not matches:
            reasons.append(case.name + ":evidence_mismatch")
        elif case.status == "PASS" and not oracle(case.name, case.evidence):
            reasons.append(case.name + ":false_pass")
    reasons.extend(name + ":missing_case" for name in REQUIRED if name not in seen)
    return {"decision": "HOLD" if reasons else "LOCAL_CONTRACT_PASS",
            "reasons": reasons, "required_cases": len(REQUIRED),
            "production_ready": False, "deployment_actions": 0}


def binding_for(mode):
    if mode not in {"report", "exit-zero", "exit-two"}:
        raise ValueError("fixed local fixture mode required")
    runner_hash = hashlib.sha256(Path(runner_module.__file__).read_bytes()).hexdigest()
    suite_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    return Binding(candidate=digest({"runner": runner_hash, "mode": mode}),
                   suite=digest({"suite": suite_hash, "runner": runner_hash}),
                   fixtures=digest({"service.txt": INPUT}),
                   environment=f"local-{os.name}-python{sys.version_info.major}.{sys.version_info.minor}",
                   trial=str(uuid4()))


def observe(mode):
    """Wait for one owned child, then read this fixture's probes and files."""
    if mode not in {"report", "exit-zero", "exit-two"}:
        raise ValueError("fixed local fixture mode required")
    with TemporaryDirectory(prefix="ax-ch43-") as directory:
        root = Path(directory)
        (root / "service.txt").write_text(INPUT, encoding="utf-8")
        with LocalRunner(True) as runner:
            runner.launch(mode, root / "checkpoint.json")
            runner.child.wait(timeout=3)
            observation = runner.observation()
            result_path, report_path = root / "result.json", root / "report.txt"
            return {"health": observation["health"], "ready": observation["ready"],
                    "child_running": observation["child_running"],
                    "child_exit": runner.child.returncode,
                    "classification": observation["decision"],
                    "result": json.loads(result_path.read_text()) if result_path.is_file() else None,
                    "report": report_path.read_text() if report_path.is_file() else None}


def case_from(binding, name, evidence):
    return Case(binding, name, "PASS" if oracle(name, evidence) else "FAIL",
                evidence, digest(evidence))


def run_suite(mode):
    binding = binding_for(mode)
    # Expected failure is a positive test of the observation contract.
    failed_child = observe("exit-two")
    application = observe(mode)
    cases = (case_from(binding, REQUIRED[0], failed_child),
             case_from(binding, REQUIRED[1], application))
    return binding, cases


def demo():
    bad_binding, bad = run_suite("exit-zero")
    good_binding, good = run_suite("report")
    application = bad[1].evidence
    missing = gate(good_binding, good[:1])
    skipped = gate(good_binding, (good[0], replace(good[1], status="SKIPPED")))
    false_pass = gate(bad_binding, (bad[0], replace(bad[1], status="PASS")))
    old = gate(good_binding, bad)
    return {
        "incident": {"health": application["health"], "ready": application["ready"],
                     "child_exit": application["child_exit"],
                     "result_present": application["result"] is not None,
                     "probe_only": "PASS", "application_case": bad[1].status},
        "failed_child_case": bad[0].status,
        "gates": {"exit_zero_no_result": gate(bad_binding, bad)["decision"],
                  "valid_result": gate(good_binding, good)["decision"],
                  "missing_case": missing["decision"], "skipped_case": skipped["decision"],
                  "false_pass": false_pass["decision"], "old_report": old["decision"]},
        "external_calls": 0, "loopback_probes_executed": True,
        "real_ax_or_substrate_executed": False, "production_ready": False,
    }


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
