"""Offline MCP 2026-07-28 teaching slice, not a general-purpose MCP SDK.

Three methods, one read-only fixture, sequential newline-delimited JSON-RPC.
No network, authentication, model, HTTP, MRTR, subscriptions or real device.
Input/output checks implement only the fixed contract below, not JSON Schema.
"""
from copy import deepcopy
from datetime import datetime, timezone
import ipaddress
import json
from pathlib import Path
import subprocess
import sys

VERSION = "2026-07-28"
PREFIX = "io.modelcontextprotocol/"
LIMIT = 16384  # UTF-8 bytes per fixture line, not model tokens.
NOW = datetime(2026, 10, 17, 7, 0, 30, tzinfo=timezone.utc)
ARGS = {"device": "lab-edge-1", "vrf": "LAB", "prefix": "192.0.2.0/24"}
OBSERVATION = {
    **ARGS, "present": True, "config_revision": "config-18",
    "local_pref": 150, "observed_at": "2026-10-17T07:00:00Z",
    "source": "local-fixture", "raw_ref": "fixture://lab33-net-001/route-18",
}
TOOL = {
    "name": "network.read_route",
    "description": "Read one route from the fixed network-lab fixture.",
    "inputSchema": {
        "type": "object", "properties": {k: {"type": "string"} for k in ARGS},
        "required": list(ARGS), "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {k: {"type": "boolean" if k == "present" else
                              "integer" if k == "local_pref" else "string"}
                       for k in OBSERVATION},
        "required": list(OBSERVATION), "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True},
}


def line(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False,
                       separators=(",", ":")) + "\n").encode("utf-8")


def decode(raw):
    def invalid_constant(value):
        raise ValueError("Non-JSON numeric constant: " + value)
    return json.loads(raw.decode("utf-8"), parse_constant=invalid_constant)


def request(identifier, method, **params):
    return {"jsonrpc": "2.0", "id": identifier, "method": method,
            "params": {**params, "_meta": {
                PREFIX + "protocolVersion": VERSION,
                PREFIX + "clientInfo": {"name": "lab-harness", "version": "1.0.0"},
                PREFIX + "clientCapabilities": {},
            }}}


def error(identifier, code, message, data=None):
    result = {"jsonrpc": "2.0", "error": {"code": code, "message": message}}
    if identifier is not None:
        result["id"] = identifier
    if data is not None:
        result["error"]["data"] = data
    return result


def complete(identifier, **fields):
    return {"jsonrpc": "2.0", "id": identifier,
            "result": {"resultType": "complete", **fields}}


def tool_error(identifier, message):
    return complete(identifier, isError=True,
                    content=[{"type": "text", "text": message}])


def valid_id(value):
    return type(value) in (str, int)


def dispatch(msg):
    """Trusted fixed fixture scope; clientInfo is NOT authenticated identity."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return error(None, -32600, "Invalid request")
    if "id" not in msg:
        return None  # No replies or fixture actions for notifications.
    identifier = msg["id"]
    if not valid_id(identifier):
        return error(None, -32600, "Invalid request ID")
    params = msg.get("params")
    if not isinstance(params, dict):
        return error(identifier, -32602, "Object params required")
    meta = params.get("_meta")
    if (not isinstance(meta, dict)
            or not isinstance(meta.get(PREFIX + "protocolVersion"), str)
            or not isinstance(meta.get(PREFIX + "clientCapabilities"), dict)):
        return error(identifier, -32602, "Required per-request metadata missing or invalid")
    version = meta[PREFIX + "protocolVersion"]
    if version != VERSION:
        return error(identifier, -32022, "Unsupported protocol version",
                     {"supported": [VERSION], "requested": version})
    method = msg["method"]
    if method == "server/discover":
        return complete(identifier, supportedVersions=[VERSION], capabilities={"tools": {}},
                        _meta={PREFIX + "serverInfo": {"name": "network-lab", "version": "1.0.0"}})
    if method == "tools/list":
        if "cursor" in params:
            return error(identifier, -32602, "Fixture has one catalog page and no cursor")
        return complete(identifier, tools=[deepcopy(TOOL)])
    if method != "tools/call":
        return error(identifier, -32601, "Method not found; supported protocol " + VERSION)
    if not isinstance(params.get("name"), str) or not isinstance(params.get("arguments"), dict):
        return error(identifier, -32602, "Malformed tools/call envelope")
    if params["name"] != TOOL["name"]:
        return error(identifier, -32602, "Unknown tool")
    args = params["arguments"]
    if set(args) != set(ARGS) or any(not isinstance(v, str) for v in args.values()):
        return tool_error(identifier, "invalid_arguments")
    try:
        ipaddress.ip_network(args["prefix"], strict=True)
    except ValueError:
        return tool_error(identifier, "invalid_prefix")
    if args != ARGS:
        return tool_error(identifier, "outside_fixture_scope")
    data = deepcopy(OBSERVATION)
    return complete(identifier, isError=False, structuredContent=data,
                    content=[{"type": "text", "text": json.dumps(data, ensure_ascii=False)}])


def serve(reader, writer):
    """Bound a line BEFORE parsing. Oversized input closes this fixture process."""
    for raw in iter(lambda: reader.readline(LIMIT + 1), b""):
        if len(raw) > LIMIT or not raw.endswith(b"\n"):
            return 2
        try:
            msg = decode(raw)
        except (ValueError, UnicodeError):
            reply = error(None, -32700, "Parse error")
        else:
            reply = dispatch(msg)
        if reply is not None:
            writer.write(line(reply))
            writer.flush()
    return 0


def validate_response(msg, identifier):
    """Validate this fixture's response envelope; never treat unknown variants as success."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0":
        raise ValueError("invalid_envelope")
    if type(msg.get("id")) is not type(identifier) or msg["id"] != identifier:
        raise ValueError("id_mismatch")
    if ("result" in msg) == ("error" in msg):
        raise ValueError("ambiguous_response")
    if "error" in msg:
        err = msg["error"]
        if not isinstance(err, dict) or type(err.get("code")) is not int or not isinstance(err.get("message"), str):
            raise ValueError("invalid_error")
        return "rpc_error"
    result = msg["result"]
    if not isinstance(result, dict) or result.get("resultType") != "complete":
        raise ValueError("unsupported_result_type")
    return "complete"


def validate_observation(msg, identifier, args, now=NOW, ttl=60):
    if validate_response(msg, identifier) != "complete":
        raise ValueError("rpc_error")
    result = msg["result"]
    if result.get("isError") is not False:
        raise ValueError("tool_error_or_missing_status")
    data = result.get("structuredContent")
    if not isinstance(data, dict) or set(data) != set(OBSERVATION):
        raise ValueError("invalid_output")
    if type(data["present"]) is not bool or type(data["local_pref"]) is not int:
        raise ValueError("invalid_output_type")
    if any(not isinstance(data[k], str) for k in data if k not in {"present", "local_pref"}):
        raise ValueError("invalid_output_type")
    if any(data[k] != args[k] for k in ARGS):
        raise ValueError("wrong_object")
    if data["source"] != "local-fixture" or not data["raw_ref"].startswith("fixture://"):
        raise ValueError("wrong_fixture_provenance")
    try:
        observed = datetime.fromisoformat(data["observed_at"].replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("timestamp_without_timezone")
        age = (now - observed).total_seconds()
    except (ValueError, TypeError) as exc:
        raise ValueError("invalid_timestamp") from exc
    if not 0 <= age <= ttl:
        raise ValueError("stale_or_future_observation")
    return deepcopy(data)


def exchange(messages, timeout=3):
    """Finite, trusted exercise batches; NOT a bounded streaming SDK client.

    communicate captures output before our size check. Do not use it with an
    arbitrary/untrusted server; use incremental bounded reading in production.
    """
    identifiers = [m["id"] for m in messages]
    if any(not valid_id(x) for x in identifiers) or len(set(identifiers)) != len(identifiers):
        raise ValueError("duplicate_or_invalid_request_id")
    process = subprocess.Popen([sys.executable, "-u", str(Path(__file__).resolve()), "--server"],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = process.communicate(b"".join(line(m) for m in messages), timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate()
        raise TimeoutError("local_fixture_deadline") from None
    if process.returncode:
        raise RuntimeError("fixture_process_failed")
    rows = stdout.splitlines(keepends=True)
    if len(rows) != len(messages) or any(len(row) > LIMIT or not row.endswith(b"\n") for row in rows):
        raise ValueError("invalid_framing")
    replies = [decode(row) for row in rows]
    for reply, identifier in zip(replies, identifiers):
        validate_response(reply, identifier)
    return replies, stderr


def demo():
    # Each stage deliberately uses a fresh child: no hidden initialization state.
    first, _ = exchange([request("d1", "server/discover")])
    assert VERSION in first[0]["result"]["supportedVersions"]
    catalog, _ = exchange([request("l1", "tools/list")])
    assert catalog[0]["result"]["tools"][0] == TOOL
    read = request("r1", "tools/call", name=TOOL["name"], arguments=ARGS)
    responses, diagnostics = exchange([read])
    data = validate_observation(responses[0], "r1", ARGS)
    unsupported = request("v1", "tools/list")
    unsupported["params"]["_meta"][PREFIX + "protocolVersion"] = "2025-11-25"
    missing = request("m1", "tools/list"); del missing["params"]["_meta"]
    bad_prefix = request("p1", "tools/call", name=TOOL["name"], arguments={**ARGS, "prefix": "not-cidr"})
    denied = request("s1", "tools/call", name=TOOL["name"], arguments={**ARGS, "device": "production-edge-1"})
    unknown = request("u1", "tools/call", name="network.apply_config", arguments=ARGS)
    failures, _ = exchange([unsupported, missing, bad_prefix, denied, unknown])
    wrong = deepcopy(responses[0]); wrong["result"]["structuredContent"]["device"] = "other-device"
    try:
        validate_observation(wrong, "r1", ARGS)
    except ValueError as exc:
        rejected_output = str(exc)
    return {"discovered_version": VERSION, "catalog_tools": [TOOL["name"]],
            "config_revision": data["config_revision"], "local_pref": data["local_pref"],
            "unsupported_version": failures[0]["error"]["code"],
            "missing_metadata": failures[1]["error"]["code"],
            "invalid_prefix": failures[2]["result"]["content"][0]["text"],
            "outside_scope": failures[3]["result"]["content"][0]["text"],
            "unknown_write": failures[4]["error"]["code"],
            "wrong_object": rejected_output, "stderr_has_diagnostic": bool(diagnostics),
            "operation_outcome": "OUTCOME_UNKNOWN", "model_calls": 0, "network_calls": 0}


if __name__ == "__main__":
    if sys.argv[1:] == ["--server"]:
        print("network-lab: fixed offline fixture", file=sys.stderr)
        raise SystemExit(serve(sys.stdin.buffer, sys.stdout.buffer))
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
