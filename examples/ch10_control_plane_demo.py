"""Local operation journal and simulated provider, not AX/Substrate APIs.

Python 3.10+, standard library. Two independent SQLite databases. Crash points
are exceptions after commits, not killed processes or power-failure tests.
The list broker is volatile; rescan is explicit, not a running scheduler.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile


class Conflict(ValueError):
    pass


class Crash(RuntimeError):
    pass


class Unavailable(RuntimeError):
    pass


def canonical(payload):
    if not isinstance(payload, dict) or set(payload) != {"job", "revision", "test"}:
        raise ValueError("expected job, revision and test")
    if any(not isinstance(v, str) or not v.strip() for v in payload.values()):
        raise ValueError("payload values must be nonempty strings")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def initialize(local, provider):
    with local:
        local.executescript("""
            CREATE TABLE IF NOT EXISTS operations (
                id TEXT PRIMARY KEY, tenant TEXT NOT NULL, key TEXT NOT NULL,
                payload TEXT NOT NULL, state TEXT NOT NULL,
                version INTEGER NOT NULL, external_id TEXT,
                UNIQUE (tenant, key)
            );
            CREATE TABLE IF NOT EXISTS outbox (
                operation_id TEXT PRIMARY KEY REFERENCES operations(id),
                published INTEGER NOT NULL DEFAULT 0
            );
        """)
    with provider:
        provider.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            )
        """)


def accept(local, tenant, key, payload, crash_before_outbox=False):
    if any(not isinstance(v, str) or not v.strip() for v in (tenant, key)):
        raise ValueError("tenant and key must be nonempty strings")
    encoded = canonical(payload)
    identity = json.dumps([tenant, key], ensure_ascii=False, separators=(",", ":"))
    op_id = hashlib.sha256(identity.encode()).hexdigest()
    with local:
        local.execute("""
            INSERT INTO operations VALUES (?, ?, ?, ?, 'accepted', 0, NULL)
            ON CONFLICT (tenant, key) DO NOTHING
        """, (op_id, tenant, key, encoded))
        row = read(local, op_id)
        if row["payload"] != encoded:
            raise Conflict("same tenant/key, different request")
        if crash_before_outbox:
            raise Crash("before outbox: transaction rolls back")
        local.execute("INSERT OR IGNORE INTO outbox(operation_id) VALUES (?)", (op_id,))
    return op_id


def read(local, op_id):
    row = local.execute("SELECT * FROM operations WHERE id=?", (op_id,)).fetchone()
    if row is None:
        raise KeyError(op_id)
    return dict(row)


def cas(local, row, state, external_id=None):
    allowed = {"accepted": {"dispatching"},
               "dispatching": {"unknown", "confirmed"},
               "unknown": {"unknown", "confirmed"}, "confirmed": set()}
    if state not in allowed[row["state"]]:
        raise ValueError("invalid transition")
    if state == "confirmed" and not external_id:
        raise ValueError("confirmation needs provider evidence")
    with local:
        changed = local.execute("""
            UPDATE operations SET state=?, external_id=?, version=version+1
            WHERE id=? AND version=? AND state=?
        """, (state, external_id, row["id"], row["version"], row["state"])).rowcount
    return changed == 1


def publish(local, broker, crash_after_send=False):
    rows = local.execute("SELECT operation_id FROM outbox WHERE published=0 ORDER BY operation_id").fetchall()
    for row in rows:
        broker.append(row["operation_id"])
        if crash_after_send:
            raise Crash("sent, not marked: delivery may repeat")
        with local:
            local.execute("UPDATE outbox SET published=1 WHERE operation_id=?", (row["operation_id"],))


def unfinished(local):
    return [r[0] for r in local.execute("SELECT id FROM operations WHERE state!='confirmed' ORDER BY id")]


class Provider:
    """Fictional API: authoritative lookup and atomic key+payload deduplication.

    Real integrations must establish these two guarantees separately.
    """
    def __init__(self, db, lookup_available=True, lose_response=False):
        self.db = db
        self.lookup_available = lookup_available
        self.lose_response = lose_response

    def lookup(self, op_id):
        if not self.lookup_available:
            raise Unavailable("provider lookup unavailable")
        row = self.db.execute("SELECT * FROM jobs WHERE operation_id=?", (op_id,)).fetchone()
        return dict(row) if row else None

    def ensure(self, op_id, encoded):
        canonical(json.loads(encoded))
        with self.db:
            self.db.execute("INSERT INTO jobs(operation_id,payload) VALUES (?,?) ON CONFLICT(operation_id) DO NOTHING", (op_id, encoded))
            row = self.db.execute("SELECT * FROM jobs WHERE operation_id=?", (op_id,)).fetchone()
            if row["payload"] != encoded:
                raise Conflict("provider key reused with different request")
        if self.lose_response:
            raise TimeoutError("job committed, response lost")
        return dict(row)


def reconcile(local, provider, op_id, crash_after_effect=False):
    row = read(local, op_id)
    if row["state"] == "confirmed":
        return "confirmed"
    if row["state"] == "accepted":
        if not cas(local, row, "dispatching"):
            return "recheck"
        row = read(local, op_id)
    try:
        found = provider.lookup(op_id)
        if found is None:
            found = provider.ensure(op_id, row["payload"])
    except (TimeoutError, Unavailable):
        return "unknown" if cas(local, row, "unknown") else "recheck"
    if found["payload"] != row["payload"]:
        raise Conflict("provider evidence has different payload")
    if crash_after_effect:
        raise Crash("provider committed, journal not confirmed")
    saved = cas(local, row, "confirmed", str(found["id"]))
    return "confirmed" if saved else "recheck"


def demo():
    payload = {"job": "job-42", "revision": "demo-fixture-v1", "test": "test_empty_list"}
    with tempfile.TemporaryDirectory(prefix="ax-ch10-demo-") as directory:
        local_path, provider_path = Path(directory)/"local.db", Path(directory)/"provider.db"
        with closing(connect(local_path)) as local, closing(connect(provider_path)) as service:
            initialize(local, service)
            op_id = accept(local, "tenant-demo", "review-42:test-empty-list", payload)
            pending_after_accept = len(unfinished(local))
            broker = []
            try:
                publish(local, broker, crash_after_send=True)
            except Crash:
                pass
            publish(local, broker)
            first = reconcile(local, Provider(service, lose_response=True), broker[0])
            delivery_count = len(broker)
        # New connections and provider object; recovery uses persisted rows.
        with closing(connect(local_path)) as local, closing(connect(provider_path)) as service:
            recovery = reconcile(local, Provider(service), unfinished(local)[0])
            duplicate = reconcile(local, Provider(service), op_id)
            row = read(local, op_id)
            count = service.execute("SELECT count(*) FROM jobs").fetchone()[0]
            return {"source": "local_sqlite_model", "cluster_contacted": False,
                    "operation_records": 1, "unfinished_after_accept": pending_after_accept,
                    "deliveries_for_one_outbox_row": delivery_count,
                    "after_lost_response": first, "after_reopen": recovery,
                    "duplicate_delivery": duplicate, "provider_jobs": count,
                    "external_id": row["external_id"], "test_result": "not_executed",
                    "review_completed": "not_established"}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
