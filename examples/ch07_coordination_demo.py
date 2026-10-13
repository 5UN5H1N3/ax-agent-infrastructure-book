"""In-memory fan-in fixture, not an LLM team or durable coordinator."""
from copy import deepcopy
import json

from ch06_tools_demo import REVISION, execute, validate_result

CONTRACTS = {
    "review-demo/code": {
        "task_id": "review-demo/code", "revision": REVISION, "attempt": 1,
        "evidence_refs": {"fixture://files/src/stats.py"},
    },
    "review-demo/tests": {
        "task_id": "review-demo/tests", "revision": REVISION, "attempt": 1,
        "evidence_refs": {"fixture://files/tests/test_stats.py",
                          "fixture://checks/job-42"},
    },
}


def validate_output(contract, output):
    fields = {"task_id", "revision", "attempt", "status", "findings"}
    if not isinstance(output, dict) or set(output) != fields:
        raise ValueError("exact output fields required")
    for key in ("task_id", "revision", "attempt"):
        if type(output[key]) is not type(contract[key]):
            raise ValueError("identity field type mismatch")
        if output[key] != contract[key]:
            raise ValueError("task, revision or attempt mismatch")
    if output["status"] not in ("complete", "partial"):
        raise ValueError("invalid completion status")
    findings = output["findings"]
    if not isinstance(findings, list) or not 1 <= len(findings) <= 8:
        raise ValueError("bounded nonempty findings required")
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {
            "key", "value", "evidence_refs"
        }:
            raise ValueError("invalid finding fields")
        if finding["key"] != "empty_input":
            raise ValueError("unknown question")
        if finding["value"] not in ("raises_IndexError", "returns_zero"):
            raise ValueError("unknown observation")
        refs = finding["evidence_refs"]
        if not isinstance(refs, list) or not 1 <= len(refs) <= 8:
            raise ValueError("bounded evidence list required")
        if any(not isinstance(ref, str) or
               ref not in contract["evidence_refs"] for ref in refs):
            raise ValueError("evidence outside contract")
    return findings


def collect(contracts, outputs):
    by_id, claims = {}, {}
    for output in outputs:
        if not isinstance(output, dict):
            raise ValueError("output must be an object")
        task_id = output.get("task_id")
        if not isinstance(task_id, str) or task_id not in contracts:
            raise ValueError("unknown task")
        if task_id in by_id:
            raise ValueError("duplicate output")
        findings = validate_output(contracts[task_id], output)
        by_id[task_id] = output["status"]
        for finding in findings:
            values = claims.setdefault(finding["key"], {})
            refs = values.setdefault(finding["value"], set())
            refs.update(finding["evidence_refs"])
    missing = sorted(set(contracts) - set(by_id))
    partial = sorted(key for key, value in by_id.items()
                     if value == "partial")
    conflicts = sorted(key for key, values in claims.items()
                       if len(values) > 1)
    status = "ready_for_synthesis"
    if missing or partial:
        status = "incomplete"
    elif conflicts:
        status = "unresolved"
    merged = {key: {value: sorted(refs) for value, refs in values.items()}
              for key, values in claims.items()}
    return {"status": status, "missing": missing, "partial": partial,
            "conflicts": conflicts, "claims": merged}


def fixture_outputs():
    """Use checked fixture references; the findings themselves are prescribed."""
    refs = []
    for name, args in [
        ("review.read_file", {"revision": REVISION, "path": "src/stats.py"}),
        ("review.read_file", {"revision": REVISION, "path": "tests/test_stats.py"}),
        ("review.get_check", {"revision": REVISION, "check_id": "job-42"}),
    ]:
        result = execute(name, args)
        validate_result(name, args, result)
        refs.append(result["evidence"]["raw_ref"])
    return [
        {"task_id": task_id, "revision": REVISION, "attempt": 1,
         "status": "complete", "findings": [
             {"key": "empty_input", "value": "raises_IndexError",
              "evidence_refs": evidence},
         ]}
        for task_id, evidence in [
            ("review-demo/code", refs[:1]),
            ("review-demo/tests", refs[1:]),
        ]
    ]


def main():
    outputs = fixture_outputs()
    print(json.dumps(collect(CONTRACTS, outputs), ensure_ascii=False))
    print(json.dumps(collect(CONTRACTS, outputs[:1]), ensure_ascii=False))
    stale = deepcopy(outputs)
    stale[1]["revision"] = "another-revision"
    try:
        collect(CONTRACTS, stale)
    except ValueError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
    conflict = deepcopy(outputs)
    conflict[1]["findings"][0]["value"] = "returns_zero"
    print(json.dumps(collect(CONTRACTS, conflict), ensure_ascii=False))


if __name__ == "__main__":
    main()
