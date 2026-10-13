"""Chapter 5: simulated service, real process exit and durable local recovery.

Run sequentially with a fresh temporary directory. No LLM, network, real test
execution, concurrent-run ownership, approvals, snapshots or model budget.
Two independent SQLite transactions deliberately do not form a shared commit.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3


OPERATION = "review-42:test-empty-list"
REVISION = "demo-fixture-v1"  # A fixture label, not a verified Git SHA.


def connect(path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    return db


def initialize(local, service):
    with local:
        local.execute(
            "CREATE TABLE IF NOT EXISTS operations ("
            "operation_id TEXT PRIMARY KEY, revision TEXT NOT NULL, "
            "status TEXT NOT NULL, job_id INTEGER)"
        )
    with service:
        service.execute(
            "CREATE TABLE IF NOT EXISTS jobs ("
            "job_id INTEGER PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, "
            "revision TEXT NOT NULL, outcome TEXT NOT NULL)"
        )


def prepare(db):
    db.execute(
        "INSERT OR IGNORE INTO operations VALUES (?, ?, ?, NULL)",
        (OPERATION, REVISION, "prepared"),
    )
    row = db.execute(
        "SELECT revision FROM operations WHERE operation_id = ?",
        (OPERATION,),
    ).fetchone()
    if row[0] != REVISION:
        raise ValueError("Operation belongs to another revision")


def lookup(service, operation_id):
    row = service.execute(
        "SELECT * FROM jobs WHERE operation_id = ?", (operation_id,)
    ).fetchone()
    return dict(row) if row is not None else None


def create_or_get(service, operation_id, revision):
    with service:
        service.execute(
            "INSERT OR IGNORE INTO jobs "
            "(operation_id, revision, outcome) VALUES (?, ?, ?)",
            (operation_id, revision, "failed"),
        )
        job = lookup(service, operation_id)
        if job["revision"] != revision:
            raise ValueError("Key was already used for another revision")
    return job


def job_count(service):
    return service.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]


def recover(local, service, crash=False):
    with local:
        prepare(local)
    job = lookup(service, OPERATION)
    if job is None:
        job = create_or_get(service, OPERATION, REVISION)
    if crash:
        print("Stopped after service commit", flush=True)
        raise SystemExit(23)
    if job["revision"] != REVISION:
        raise ValueError("Service returned another revision")
    with local:
        local.execute(
            "UPDATE operations SET status = ?, job_id = ? "
            "WHERE operation_id = ?",
            ("observed", job["job_id"], OPERATION),
        )
    return {
        "operation_id": OPERATION,
        "job_id": job["job_id"],
        "test_outcome": job["outcome"],
        "service_jobs": job_count(service),
        "simulated": True,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--crash", action="store_true")
    args = parser.parse_args()
    args.directory.mkdir(parents=True, exist_ok=True)
    with closing(connect(args.directory / "harness.sqlite")) as local:
        with closing(connect(args.directory / "service.sqlite")) as service:
            initialize(local, service)
            result = recover(local, service, args.crash)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
