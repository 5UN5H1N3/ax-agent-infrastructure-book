"""Offline evidence join, not a workflow engine or an authorization service.

Producer identities and timestamps are trusted fixtures supplied out of band.
Only the bundled ch32 Python fixtures are executed, in this process, WITHOUT
a sandbox. Never use that helper for untrusted patches. No model, Git, network,
AX, Substrate, BGP, configuration write or publication is performed.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import io
import json
from types import ModuleType
import unittest
from unittest.mock import patch

from ch32_batching_exercise import BROKEN_SOURCE, TEST_SOURCE


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_text(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip()


def valid_hash(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def integer(value):
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Subject:
    run: str
    target: str
    base_revision: str
    candidate_revision: str
    content_sha256: str
    policy_sha256: str
    stage: str

    def __post_init__(self):
        if not all(valid_text(v) for v in (self.run, self.target, self.base_revision,
                                          self.candidate_revision, self.stage)):
            raise ValueError("invalid subject labels")
        if not all(valid_hash(v) for v in (self.content_sha256, self.policy_sha256)):
            raise ValueError("invalid subject digest")

    def fingerprint(self):
        return digest(encoded(asdict(self)))


@dataclass(frozen=True)
class Rule:
    kind: str
    producer: str
    checks: tuple[str, ...]

    def __post_init__(self):
        if (not valid_text(self.kind) or not valid_text(self.producer)
                or not isinstance(self.checks, tuple) or not self.checks
                or not all(valid_text(x) for x in self.checks)
                or len(set(self.checks)) != len(self.checks)):
            raise ValueError("invalid evidence rule")


@dataclass(frozen=True)
class Policy:
    name: str
    rules: tuple[Rule, ...]
    max_age: int

    def __post_init__(self):
        if (not valid_text(self.name) or not isinstance(self.rules, tuple)
                or not self.rules or not all(isinstance(r, Rule) for r in self.rules)
                or len({r.kind for r in self.rules}) != len(self.rules)
                or not integer(self.max_age)):
            raise ValueError("invalid policy")

    def fingerprint(self):
        return digest(encoded(asdict(self)))


@dataclass(frozen=True)
class Receipt:
    """Trusted transport metadata, NOT fields accepted from an agent's report."""
    producer: str
    artifact_sha256: str
    captured_at: int

    def __post_init__(self):
        if not valid_text(self.producer) or not valid_hash(self.artifact_sha256) or not integer(self.captured_at):
            raise ValueError("invalid receipt")


class Artifacts:
    """In-memory bytes keyed by SHA256; no durability or hostile-writer defense."""
    def __init__(self):
        self.blobs = {}

    def put(self, value):
        data = encoded(value)
        if len(data) > 16384:
            raise ValueError("oversized report")
        key = digest(data)
        self.blobs.setdefault(key, data)
        return key

    def read(self, key):
        if not valid_hash(key):
            raise ValueError("invalid_artifact_key")
        data = self.blobs.get(key)
        if type(data) is not bytes or len(data) > 16384 or digest(data) != key:
            raise ValueError("artifact_missing_or_changed")
        try:
            result = json.loads(data)
        except (ValueError, UnicodeError) as error:
            raise ValueError("invalid_report_json") from error
        if encoded(result) != data:
            raise ValueError("noncanonical_report")
        return result


def join(subject, policy, receipts, artifacts, now, not_before=0):
    """Validate one deliberately small set of reports for the next workflow step.

    A ready result grants NO permission and performs NO external effect. A
    producer that lies consistently about checks cannot be detected here.
    """
    if (not isinstance(subject, Subject) or not isinstance(policy, Policy)
            or not isinstance(artifacts, Artifacts) or not integer(now)
            or not integer(not_before) or not_before > now):
        raise ValueError("invalid join context")
    receipts = list(receipts)
    if any(not isinstance(r, Receipt) for r in receipts):
        raise ValueError("Receipt objects required")
    if subject.policy_sha256 != policy.fingerprint():
        return "rejected:policy_changed"
    rules = {rule.kind: rule for rule in policy.rules}
    accepted = {}
    for receipt in receipts:
        try:
            report = artifacts.read(receipt.artifact_sha256)
        except ValueError as error:
            return "rejected:" + str(error)
        if not isinstance(report, dict) or set(report) != {"subject", "kind", "verdict", "checks"}:
            return "rejected:report_schema"
        if report["subject"] != subject.fingerprint():
            return "rejected:subject_mismatch"
        kind = report["kind"]
        if not isinstance(kind, str) or kind not in rules:
            return "rejected:unexpected_kind"
        rule = rules[kind]
        if receipt.producer != rule.producer:
            return "rejected:producer_mismatch"
        if not not_before <= receipt.captured_at <= now:
            return "rejected:outside_window"
        if now - receipt.captured_at > policy.max_age:
            return "rejected:stale"
        checks, verdict = report["checks"], report["verdict"]
        if (not isinstance(checks, dict) or set(checks) != set(rule.checks)
                or any(type(value) is not bool for value in checks.values())
                or not isinstance(verdict, str) or verdict not in {"pass", "fail", "inconclusive"}):
            return "rejected:checks_schema"
        if verdict == "inconclusive":
            return "manual_review:inconclusive"
        if verdict != "pass" or not all(checks.values()):
            return "rejected:checks_failed"
        if kind in accepted and accepted[kind] != receipt:
            return "rejected:ambiguous_attempts"
        accepted[kind] = receipt
    if set(accepted) != set(rules):
        return "waiting:missing_evidence"
    return "ready_for_next_step"


TEST_NAMES = ("test_coverage", "test_duplicates", "test_empty", "test_exact",
              "test_input_unchanged", "test_invalid_size", "test_one",
              "test_short", "test_size_types", "test_tail")
CODE_POLICY = Policy("batching-contract-1:" + digest(TEST_SOURCE.encode()), (
    Rule("tests", "trusted-ci", TEST_NAMES),
    Rule("review", "trusted-review-channel", ("scope", "contract")),
), max_age=3600)
NET_POLICY = Policy("LAB-routing-post-checks-1", (
    Rule("config", "config-reader", ("post_state", "scope")),
    Rule("traffic", "traffic-collector", ("loss", "control_flow")),
), max_age=30)


def trusted_fixture_checks(repaired):
    """Run ONLY fixed book fixtures; this is not a sandbox for candidate code."""
    if type(repaired) is not bool:
        raise ValueError("fixture selector must be boolean")
    source = (BROKEN_SOURCE.replace("len(items) - size + 1", "len(items)")
              if repaired else BROKEN_SOURCE)
    module = ModuleType("batching")
    exec(compile(source, "trusted_fixture.py", "exec"), module.__dict__)
    namespace = {"__name__": "trusted_fixture_checks"}
    with patch.dict("sys.modules", {"batching": module}):
        exec(compile(TEST_SOURCE, "protected_tests.py", "exec"), namespace)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(namespace["BatchingTests"])
    result = unittest.TextTestRunner(stream=io.StringIO()).run(suite)
    assert result.testsRun == len(TEST_NAMES)
    failed = {case._testMethodName for case, _ in result.failures + result.errors}
    return source, {name: name not in failed for name in TEST_NAMES}


def evidence(artifacts, subject, rule, when, checks=None, verdict="pass"):
    """Create a trusted fixture; never accept a caller's identity this way in production."""
    report = {"subject": subject.fingerprint(), "kind": rule.kind, "verdict": verdict,
              "checks": dict.fromkeys(rule.checks, True) if checks is None else checks}
    return Receipt(rule.producer, artifacts.put(report), when)


def code_fixture(repaired=True):
    source, checks = trusted_fixture_checks(repaired)
    subject = Subject("code-run-1", "repo:batching", "base-1", "candidate-1",
                      digest(source.encode()), CODE_POLICY.fingerprint(), "candidate_checks")
    artifacts = Artifacts()
    reports = [evidence(artifacts, subject, CODE_POLICY.rules[0], 100, checks,
                        "pass" if all(checks.values()) else "fail"),
               evidence(artifacts, subject, CODE_POLICY.rules[1], 100)]
    return subject, artifacts, reports


def net_fixture():
    subject = Subject("net-run-1", "lab-edge-1/VRF:LAB", "config-17", "config-18",
                      digest(encoded({"LAB-UPSTREAM-A": {"192.0.2.0/24": 150}})),
                      NET_POLICY.fingerprint(), "post_change")
    artifacts = Artifacts()
    reports = [evidence(artifacts, subject, rule, 120) for rule in NET_POLICY.rules]
    return subject, artifacts, reports


def demo():
    subject, artifacts, reports = code_fixture()
    rows = {"code_ok": join(subject, CODE_POLICY, reports, artifacts, 120)}
    rows["missing_review"] = join(subject, CODE_POLICY, reports[:1], artifacts, 120)
    rows["new_candidate"] = join(replace(subject, candidate_revision="candidate-2"), CODE_POLICY, reports, artifacts, 120)
    rows["new_base"] = join(replace(subject, base_revision="base-2"), CODE_POLICY, reports, artifacts, 120)
    rows["policy_changed"] = join(subject, replace(CODE_POLICY, name="new-policy"), reports, artifacts, 120)
    rows["producer_substitution"] = join(subject, CODE_POLICY, [replace(reports[0], producer="implementer"), reports[1]], artifacts, 120)
    rows["duplicate_delivery"] = join(subject, CODE_POLICY, reports + reports, artifacts, 120)
    artifacts.blobs[reports[0].artifact_sha256] = b'changed bytes'
    rows["artifact_changed"] = join(subject, CODE_POLICY, reports, artifacts, 120)
    broken, store, evidence_set = code_fixture(False)
    rows["real_tests_fail"] = join(broken, CODE_POLICY, evidence_set, store, 120)
    net, store, evidence_set = net_fixture()
    rows["net_ok"] = join(net, NET_POLICY, evidence_set, store, 130, not_before=110)
    rows["old_telemetry"] = join(net, NET_POLICY, evidence_set, store, 151, not_before=110)
    rows["before_change"] = join(net, NET_POLICY, evidence_set, store, 130, not_before=121)
    inconclusive = evidence(store, net, NET_POLICY.rules[1], 120, verdict="inconclusive")
    rows["no_traffic_verdict"] = join(net, NET_POLICY, [evidence_set[0], inconclusive], store, 130, not_before=110)
    # A valid trusted receipt with a false claim is indistinguishable here.
    forged_by_trusted_producer = evidence(store, broken, CODE_POLICY.rules[0], 100)
    review = evidence(store, broken, CODE_POLICY.rules[1], 100)
    rows["trusted_lie"] = join(broken, CODE_POLICY, [forged_by_trusted_producer, review], store, 120)
    return {"cases": rows, "baseline_tests": 10, "baseline_failures": 3,
            "repaired_fixture_failures": 0, "external_effects_executed": False,
            "producer_authentication_implemented": False, "live_network_verified": False}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
