"""Deterministic tool-boundary fixture, not an MCP integration or real review."""
import json

REVISION = "demo-fixture-v1"
SPECS = {
    "review.read_file": {"revision", "path"},
    "review.get_check": {"revision", "check_id"},
}
FILES = {
    "src/stats.py": "def total(items):\n    return items[0] + sum(items[1:])\n",
    "tests/test_stats.py": "def test_empty_list():\n    assert total([]) == 0\n",
}
CHECK = {
    "revision": REVISION, "check_id": "job-42", "test_name": "test_empty_list",
    "status": "failed", "failure": "IndexError",
}


def execute(name, arguments):
    if not isinstance(name, str) or name not in SPECS:
        return {"status": "denied", "reason": "tool_not_allowed"}
    fields = SPECS[name]
    if not isinstance(arguments, dict) or set(arguments) != fields:
        return {"status": "invalid", "reason": "exact_fields_required"}
    if any(not isinstance(v, str) or not v or len(v) > 256
           for v in arguments.values()):
        return {"status": "invalid", "reason": "bounded_strings_required"}
    if arguments["revision"] != REVISION:
        return {"status": "conflict", "reason": "revision_not_available"}
    if name == "review.read_file":
        path = arguments["path"]
        if path not in FILES:
            return {"status": "denied", "reason": "path_not_allowed"}
        data = {"revision": REVISION, "path": path, "text": FILES[path]}
        raw_ref = "fixture://files/" + path
    else:
        if arguments["check_id"] != CHECK["check_id"]:
            return {"status": "invalid", "reason": "unknown_check"}
        data = dict(CHECK)
        raw_ref = "fixture://checks/" + CHECK["check_id"]
    return {"status": "succeeded", "data": data,
            "evidence": {"source": "local-fixture", "raw_ref": raw_ref}}


def validate_result(name, arguments, result):
    if name not in SPECS or not isinstance(result, dict):
        raise ValueError("unknown tool or malformed result")
    if result.get("status") != "succeeded":
        raise ValueError("result is not a successful observation")
    data = result.get("data")
    evidence = result.get("evidence")
    if not isinstance(data, dict) or not isinstance(evidence, dict):
        raise ValueError("data and evidence must be objects")
    if any(data.get(key) != arguments[key] for key in SPECS[name]):
        raise ValueError("result does not match requested object")
    if evidence.get("source") != "local-fixture":
        raise ValueError("unexpected fixture source")
    raw_ref = evidence.get("raw_ref")
    if not isinstance(raw_ref, str) or not raw_ref.startswith("fixture://"):
        raise ValueError("missing fixture reference")
    if name == "review.read_file" and not isinstance(data.get("text"), str):
        raise ValueError("file text must be a string")
    if name == "review.get_check" and data.get("status") not in {"passed", "failed"}:
        raise ValueError("invalid check status")
    return data


def main():
    args = {"revision": REVISION, "check_id": "job-42"}
    result = execute("review.get_check", args)
    data = validate_result("review.get_check", args, result)
    print(json.dumps({"read_status": result["status"], "check_status": data["status"]}))
    proposals = [
        ("review.get_check", {**args, "command": "publish"}),
        ("review.get_check", {**args, "revision": "another-revision"}),
        ("review.publish_comment", {"text": "publish me"}),
    ]
    for name, arguments in proposals:
        print(json.dumps(execute(name, arguments)))


if __name__ == "__main__":
    main()
