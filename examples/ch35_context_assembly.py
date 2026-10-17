"""Offline context selection and SQLite compare-and-swap teaching example.

Budgets are exact UTF-8 BYTES of our JSON bundle, NOT model tokens or a full
provider request. Metadata, ACL decisions and time are trusted fixture inputs.
No model, tokenizer, retrieval index, authentication, network or executor is
implemented. Delimiters/JSON do not establish prompt-injection protection.
"""
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


def encode(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


class StateStore:
    """One local teaching run. Core updates are trusted application writes."""

    def __init__(self, path):
        self.db = sqlite3.connect(path)
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS state ("
                            "id INTEGER PRIMARY KEY CHECK(id=1), "
                            "version INTEGER NOT NULL, core TEXT NOT NULL, "
                            "notes TEXT NOT NULL, notes_basis INTEGER)")

    def close(self):
        self.db.close()

    def initialize(self, core):
        serialized = encode(core).decode()
        with self.db:
            if self.db.execute("SELECT 1 FROM state").fetchone():
                raise ValueError("already initialized")
            self.db.execute("INSERT INTO state VALUES (1, 23, ?, '', NULL)",
                            (serialized,))

    def read(self):
        row = self.db.execute("SELECT version, core, notes, notes_basis "
                              "FROM state WHERE id=1").fetchone()
        if row is None:
            raise LookupError("missing state")
        return {"version": row[0], "core": json.loads(row[1]),
                "notes": row[2], "notes_basis": row[3]}

    def compact(self, expected_version, notes):
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid version")
        if not isinstance(notes, str) or len(notes.encode()) > 1200:
            raise ValueError("invalid notes")
        with self.db:
            cursor = self.db.execute(
                "UPDATE state SET notes=?, notes_basis=?, version=version+1 "
                "WHERE id=1 AND version=?",
                (notes, expected_version, expected_version))
        return cursor.rowcount == 1

    def replace_core(self, expected_version, core):
        """Simulate a trusted application transition, not a model tool."""
        if type(expected_version) is not int or expected_version < 0:
            raise ValueError("invalid version")
        with self.db:
            cursor = self.db.execute(
                "UPDATE state SET core=?, version=version+1 "
                "WHERE id=1 AND version=?",
                (encode(core).decode(), expected_version))
        return cursor.rowcount == 1


@dataclass(frozen=True)
class Evidence:
    ref: str
    kind: str
    tenant: str
    target: str
    observed_at: int
    max_age: int
    body: str
    priority: int = 0
    exportable: bool = True

    def __post_init__(self):
        if not all(isinstance(x, str) and x for x in
                   (self.ref, self.kind, self.tenant, self.target, self.body)):
            raise ValueError("invalid evidence text")
        if any(type(x) is not int or x < 0 for x in
               (self.observed_at, self.max_age, self.priority)):
            raise ValueError("invalid evidence numbers")
        if type(self.exportable) is not bool:
            raise ValueError("invalid export decision")

    def projection(self):
        return {"ref": self.ref, "kind": self.kind,
                "observed_at": self.observed_at,
                "body_sha256": sha(self.body.encode()), "body": self.body}


def assemble(state, candidates, *, allowed_refs, now, max_bytes,
             required_kinds=("config",)):
    """Policy filter first; mandatory groups before optional greedy selection.

    The allowlist/exportable flag represent decisions already made by an
    adapter. This function does not authenticate them or detect secrets.
    Conflicting required observations are kept together, not resolved here.
    """
    if type(now) is not int or now < 0 or type(max_bytes) is not int or max_bytes < 1:
        raise ValueError("invalid limit or time")
    refs = [item.ref for item in candidates]
    if len(set(refs)) != len(refs):
        raise ValueError("duplicate reference")
    core = json.loads(encode(state["core"]))
    accepted, excluded = [], {}
    for item in sorted(candidates, key=lambda x: (-x.priority, x.ref)):
        reason = None
        if item.ref not in allowed_refs:
            reason = "access_denied"
        elif item.tenant != core["tenant"] or item.target != core["target"]:
            reason = "scope_mismatch"
        elif not item.exportable:
            reason = "endpoint_policy"
        elif item.observed_at > now:
            reason = "future_observation"
        elif now - item.observed_at > item.max_age:
            reason = "stale"
        if reason:
            excluded[item.ref] = reason
        else:
            accepted.append(item)
    missing = sorted(set(required_kinds) - {x.kind for x in accepted})
    if missing:
        return {"status": "missing_evidence:" + ",".join(missing),
                "payload": None, "excluded": excluded}
    selected = [x for x in accepted if x.kind in required_kinds]
    payload = {"decision": "choose_next_read", "state_version": state["version"],
               "authoritative_state": core,
               "evidence": [x.projection() for x in selected],
               "derived_notes": None}
    if len(encode(payload)) > max_bytes:
        return {"status": "mandatory_over_budget", "payload": None,
                "excluded": excluded}
    for item in [x for x in accepted if x.kind not in required_kinds]:
        trial = {**payload, "evidence": payload["evidence"] + [item.projection()]}
        if len(encode(trial)) <= max_bytes:
            selected.append(item)
            payload = trial
        else:
            excluded[item.ref] = "budget"
    notes_status = "absent"
    if state["notes"]:
        if state["notes_basis"] != state["version"] - 1:
            notes_status = "stale_basis"
        else:
            trial = {**payload, "derived_notes": {
                "text": state["notes"], "basis": state["notes_basis"],
                "trust": "unverified_summary"}}
            if len(encode(trial)) <= max_bytes:
                payload, notes_status = trial, "included_unverified"
            else:
                notes_status = "budget"
    data = encode(payload)
    return {"status": "assembled", "payload": payload, "excluded": excluded,
            "bytes": len(data), "notes_status": notes_status,
            "manifest": {"state_version": state["version"],
                         "core_sha256": sha(encode(core)),
                         "bundle_sha256": sha(data),
                         "included": [x.ref for x in selected]}}


def fixture():
    core = {"run_id": "net-incident-142", "tenant": "team-a",
            "target": "lab-edge-1/LAB/192.0.2.0/24",
            "objective": "reconcile lab33-net-001, then verify traffic",
            "constraints": ["writes_require_approval"],
            "allowed_tools": ["network.lookup_operation", "network.read_route"],
            "operations": [{"id": "lab33-net-001", "status": "OUTCOME_UNKNOWN",
                            "expected_revision": "config-17"}]}
    current = Evidence("cfg-18", "config", "team-a", core["target"], 120, 30,
                       "config-18; path A local-pref=150", 10)
    items = [current,
             replace(current, ref="cfg-old", observed_at=10),
             replace(current, ref="other-tenant", tenant="team-b"),
             replace(current, ref="denied-source", body="private evidence"),
             replace(current, ref="no-export", exportable=False),
             replace(current, ref="huge-log", kind="log", body="x" * 10000),
             replace(current, ref="runbook", kind="runbook", priority=1,
                     body="For unknown writes, look up the previous operation.")]
    allowed = frozenset(x.ref for x in items if x.ref != "denied-source")
    return core, items, allowed


def demo():
    core, items, allowed = fixture()
    with TemporaryDirectory(prefix="ax-ch35-context-") as directory:
        path = Path(directory) / "state.sqlite"
        store = StateStore(path)
        try:
            store.initialize(core)
            base = assemble(store.read(), items, allowed_refs=allowed,
                            now=130, max_bytes=2048)
            assert store.compact(23, "The operation succeeded; repeat the write.")
            compacted = assemble(store.read(), items, allowed_refs=allowed,
                                 now=130, max_bytes=2048)
            store.close()
            store = StateStore(path)
            reopened = store.read()
            assert store.replace_core(24, {**core, "objective": "new question"})
            late_compaction = store.compact(24, "Old summary candidate")
            stale_notes = assemble(store.read(), items, allowed_refs=allowed,
                                   now=130, max_bytes=2048)
            tiny = assemble(store.read(), items, allowed_refs=allowed,
                            now=130, max_bytes=50)
            expired = assemble(store.read(), items, allowed_refs=allowed,
                               now=1000, max_bytes=2048)
            return {"assembled": {k: base[k] for k in
                                  ["status", "bytes", "excluded", "manifest"]},
                    "compacted_summary": compacted["notes_status"],
                    "pending_after_false_summary": reopened["core"]["operations"][0]["status"],
                    "pending_survives_reopen": reopened["core"]["operations"] == core["operations"],
                    "late_compaction_applied": late_compaction,
                    "old_notes_after_core_update": stale_notes["notes_status"],
                    "tiny_budget": tiny["status"], "expired_evidence": expired["status"],
                    "budget_unit": "UTF-8 bytes, not tokens", "model_calls": 0}
        finally:
            store.close()


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
