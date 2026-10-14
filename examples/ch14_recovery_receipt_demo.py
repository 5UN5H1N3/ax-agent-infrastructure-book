"""A local receipt experiment, not a Substrate checkpoint implementation.

Actor snapshots are deep-copied Python values. The separate toy SQLite service
keeps its receipt when those values are rolled back. No network, sandbox,
process-memory capture, Kubernetes, real payment or benchmark is involved.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
import tempfile


class LookupUnavailable(Exception):
    pass


class ToyService:
    """Single-threaded retained receipts with payload-bound operation IDs."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS receipts "
                        "(operation_id TEXT PRIMARY KEY, payload TEXT NOT NULL, "
                        "receipt TEXT NOT NULL)")
        self.db.commit()
        self.lookup_available = True

    def close(self):
        self.db.close()

    def publish(self, operation_id, payload):
        if not operation_id or not payload:
            raise ValueError("operation ID and payload must be nonempty")
        row = self.db.execute(
            "SELECT payload, receipt FROM receipts WHERE operation_id=?",
            (operation_id,)).fetchone()
        if row:
            if row[0] != payload:
                raise ValueError("operation ID is bound to another payload")
            return row[1]
        receipt = f"receipt-{self.effect_count() + 1}"
        with self.db:
            self.db.execute("INSERT INTO receipts VALUES (?, ?, ?)",
                            (operation_id, payload, receipt))
        return receipt

    def lookup(self, operation_id):
        if not self.lookup_available:
            raise LookupUnavailable("toy service lookup is unavailable")
        row = self.db.execute(
            "SELECT payload, receipt FROM receipts WHERE operation_id=?",
            (operation_id,)).fetchone()
        return None if row is None else {"payload": row[0], "receipt": row[1]}

    def effect_count(self):
        return self.db.execute("SELECT count(*) FROM receipts").fetchone()[0]


@dataclass(frozen=True)
class Snapshot:
    scope: str
    data: dict
    memory: dict | None


def capture(scope, data, memory):
    if scope not in ("FULL", "DATA"):
        raise ValueError("expected FULL or DATA")
    return Snapshot(scope, deepcopy(data),
                    deepcopy(memory) if scope == "FULL" else None)


def restore(snapshot):
    """Copy supplied values; DATA models a fresh entrypoint, not golden resume."""
    return {"data": deepcopy(snapshot.data),
            "memory": (deepcopy(snapshot.memory) if snapshot.scope == "FULL"
                       else {"phase": "ENTRYPOINT"})}


def reconcile(view, service):
    """Look up a pending intent; never publish another effect automatically."""
    intent = view["data"].get("pending")
    if intent is None:
        return "NO_PENDING_INTENT"
    try:
        receipt = service.lookup(intent["operation_id"])
    except LookupUnavailable:
        return "OUTCOME_UNKNOWN"
    if receipt is None:
        return "REQUIRES_DECISION"
    if receipt["payload"] != intent["payload"]:
        return "PAYLOAD_CONFLICT"
    view["data"]["result"] = receipt["receipt"]
    view["data"]["pending"] = None
    return "CONFIRMED_FROM_SERVICE"


def recovery_window(now_s, last_completed_external_s):
    """Supplied time window, not an actual loss measurement or an RPO policy."""
    if last_completed_external_s is None:
        return None
    if (type(now_s) is not int or type(last_completed_external_s) is not int
            or last_completed_external_s < 0 or now_s < last_completed_external_s):
        raise ValueError("expected ordered nonnegative integer timestamps")
    return now_s - last_completed_external_s


def demo():
    data = {"pending": {"operation_id": "review-42/publish-1",
                        "payload": "patch-sha-demo"}, "result": None}
    memory = {"phase": "WAITING_FOR_RECEIPT"}
    snapshots = [capture(scope, data, memory) for scope in ("FULL", "DATA")]
    with tempfile.TemporaryDirectory(prefix="ax-ch14-receipts-") as directory:
        path = str(Path(directory) / "toy-service.sqlite")
        service = ToyService(path)
        service.publish(data["pending"]["operation_id"], data["pending"]["payload"])
        # The service committed, but the Actor's result was never written.
        service.close()
        service = ToyService(path)
        try:
            recovered = []
            for snapshot in snapshots:
                view = restore(snapshot)
                recovered.append({"scope": snapshot.scope,
                                  "heap_phase": view["memory"]["phase"],
                                  "outcome": reconcile(view, service),
                                  "receipt": view["data"]["result"],
                                  "service_effects": service.effect_count()})
            service.lookup_available = False
            unknown = restore(snapshots[1])
            unavailable = {"outcome": reconcile(unknown, service),
                           "pending_preserved": unknown["data"]["pending"] is not None,
                           "service_effects": service.effect_count()}
            return {"source": "local_toy_service_and_value_copies",
                    "substrate_contacted": False, "recovered": recovered,
                    "lookup_unavailable": unavailable,
                    "supplied_node_loss_window_s": recovery_window(420, 120)}
        finally:
            service.close()


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
