"""Local behavior model, not an Actor framework or a Substrate integration.

SQLite commits session state and command receipts together. The bounded mailbox
is deliberately volatile. There is no external executor, timer, auth, placement,
process checkpoint, distributed ownership protocol or durable message transport.
Run with Python 3.10+ from the repository root.
"""
from collections import deque
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


class Conflict(Exception):
    pass


class Busy(Exception):
    pass


def initial_state():
    return {"phase": "waiting_plan", "revision": None, "revisions": [],
            "active_call": None, "calls": {}}


def record_result(state, command):
    state = deepcopy(state)
    call_id, revision = command["call_id"], command["revision"]
    call = state["calls"].get(call_id)
    if call is None or revision != call["revision"]:
        return state, {"code": "unknown_call"}
    result = {"outcome": command["outcome"], "report_ref": command["report_ref"]}
    if result["outcome"] not in {"passed", "failed"} or not result["report_ref"]:
        raise Conflict("a result needs an outcome and a report reference")
    if call["result"] is not None:
        if call["result"] != result:
            raise Conflict("one call cannot acquire a different result")
        return state, {"code": "result_already_saved"}
    call["result"] = result
    if revision != state["revision"] or call_id != state["active_call"]:
        return state, {"code": "historical_result_saved"}
    state["phase"] = ("waiting_acceptance" if result["outcome"] == "passed"
                      else "test_failed")
    return state, {"code": "current_result_saved"}


def transition(state, command):
    """One short synchronous turn. It records intent; it never calls a tool."""
    state = deepcopy(state)
    kind = command["kind"]
    revision = command.get("revision")
    if kind == "ProposePlan":
        if not revision:
            raise Conflict("a plan needs a revision")
        if revision == state["revision"]:
            return state, {"code": "plan_already_current"}
        if revision in state["revisions"]:
            raise Conflict("a superseded revision cannot become a new plan")
        state["revisions"].append(revision)
        state.update(phase="waiting_approval", revision=revision,
                     active_call=None)
        return state, {"code": "plan_proposed"}
    if kind == "Approve":
        if revision != state["revision"]:
            return state, {"code": "stale_approval"}
        if state["phase"] != "waiting_approval":
            return state, {"code": "wrong_phase"}
        call_id = command["call_id"]
        if not call_id or call_id in state["calls"]:
            raise Conflict("call ID must be new and nonempty")
        operation_id = command["operation_id"]
        if not operation_id or any(c["operation_id"] == operation_id
                                   for c in state["calls"].values()):
            raise Conflict("a new tool intent needs a new operation ID")
        state["calls"][call_id] = {
            "revision": revision, "operation_id": operation_id,
            "result": None,
        }
        state.update(phase="waiting_tool", active_call=call_id)
        return state, {"code": "tool_requested", "call_id": call_id}
    if kind == "ToolResult":
        return record_result(state, command)
    if kind == "AcceptReview":
        if revision != state["revision"]:
            return state, {"code": "stale_acceptance"}
        if state["phase"] != "waiting_acceptance":
            return state, {"code": "wrong_phase"}
        state["phase"] = "completed"
        return state, {"code": "review_accepted"}
    raise Conflict("unsupported command")


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class SessionStore:
    """The key is (tenant, session, incarnation UID), supplied by a trusted caller."""
    def __init__(self, path):
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                actor_key TEXT PRIMARY KEY, state TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS receipts (
                actor_key TEXT NOT NULL, message_id TEXT NOT NULL,
                payload TEXT NOT NULL, reply TEXT NOT NULL,
                PRIMARY KEY (actor_key, message_id));
        """)

    def close(self):
        self.db.close()

    def state(self, key):
        row = self.db.execute("SELECT state FROM sessions WHERE actor_key=?",
                              (encode(key),)).fetchone()
        return json.loads(row[0]) if row else initial_state()

    def dispatch(self, key, command, crash_before_commit=False):
        command = deepcopy(command)
        actor_key, payload = encode(key), encode(command)
        if not command.get("id"):
            raise Conflict("message ID is required")
        if command.get("actor_uid") != key[2]:
            raise Conflict("message belongs to another incarnation")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            old = self.db.execute(
                "SELECT payload, reply FROM receipts WHERE actor_key=? AND message_id=?",
                (actor_key, command["id"])).fetchone()
            if old:
                if old[0] != payload:
                    raise Conflict("same message ID, different payload")
                reply = json.loads(old[1])
            else:
                state, reply = transition(self.state(key), command)
                self.db.execute("INSERT INTO sessions VALUES (?, ?) "
                                "ON CONFLICT(actor_key) DO UPDATE SET state=excluded.state",
                                (actor_key, encode(state)))
                self.db.execute("INSERT INTO receipts VALUES (?, ?, ?, ?)",
                                (actor_key, command["id"], payload, encode(reply)))
            if crash_before_commit:
                raise RuntimeError("injected failure before local commit")
            self.db.execute("COMMIT")
            return reply
        except BaseException:
            self.db.execute("ROLLBACK")
            raise


class Mailbox:
    """Manually drained, single-caller, in-memory queue, not durable acceptance."""
    def __init__(self, store, capacity=2):
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.store, self.capacity = store, capacity
        self.queue = deque()

    def submit(self, key, command):
        if len(self.queue) >= self.capacity:
            raise Busy("mailbox full; command has not been durably accepted")
        self.queue.append((deepcopy(key), deepcopy(command)))
        return "queued_in_memory"

    def turn(self):
        key, command = self.queue.popleft()
        return self.store.dispatch(key, command)


def message(message_id, kind, revision, **fields):
    return {"id": message_id, "actor_uid": "uid-42", "kind": kind,
            "revision": revision, **fields}


def demo():
    key = ("demo-tenant", "review-42", "uid-42")
    with TemporaryDirectory(prefix="ax-ch11-") as directory:
        path = Path(directory) / "sessions.db"
        store = SessionStore(path)
        first = Mailbox(store)
        first.submit(key, message("m1", "ProposePlan", "r1")); first.turn()
        approval = message("m2", "Approve", "r1", call_id="c1", operation_id="op-1")
        first.submit(key, approval); first.turn()
        first.submit(key, message("m3", "ProposePlan", "r2")); first.turn()
        # This command exists only in RAM. Dropping the mailbox loses it.
        first.submit(key, message("m4", "ToolResult", "r1", call_id="c1",
                                  outcome="passed", report_ref="report/r1"))
        queued_before_restart = len(first.queue)
        store.close()
        del first
        store = SessionStore(path)
        second = Mailbox(store)
        pending_after_restart = len(second.queue)
        late = store.dispatch(key, message("m4", "ToolResult", "r1", call_id="c1",
                                           outcome="passed", report_ref="report/r1"))
        phase_after_late = store.state(key)["phase"]
        replay = store.dispatch(key, approval)
        old_approval = store.dispatch(key, message("m5", "Approve", "r1",
                                                   call_id="bad", operation_id="bad"))
        store.dispatch(key, message("m6", "Approve", "r2", call_id="c2", operation_id="op-2"))
        store.dispatch(key, message("m7", "ToolResult", "r2", call_id="c2",
                                    outcome="passed", report_ref="report/r2"))
        phase_after_report = store.state(key)["phase"]
        store.dispatch(key, message("m8", "AcceptReview", "r2"))
        final = store.state(key)
        store.close()
    return {"source": "local_sqlite_behavior_model", "cluster_contacted": False,
            "logical_actor": "demo-tenant/review-42/uid-42",
            "mailbox_instances": 2, "queued_before_restart": queued_before_restart,
            "queued_after_restart": pending_after_restart,
            "late_result": late["code"], "phase_after_late": phase_after_late,
            "replayed_approval_reply": replay["code"],
            "new_old_revision_approval": old_approval["code"],
            "phase_after_report": phase_after_report, "final_phase": final["phase"],
            "saved_results": sum(c["result"] is not None for c in final["calls"].values()),
            "real_test_executed": False, "real_human_approval_verified": False}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
