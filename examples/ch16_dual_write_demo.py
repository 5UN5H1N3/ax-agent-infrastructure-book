"""Two local SQLite records model a resume/status-write failure window.

This is not AX, Redis or Substrate. The recovery planner only reads supplied
records. UID and launch markers are explicit fixture inputs, not AX fields.
"""
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory


class SimulatedCrash(RuntimeError):
    pass


class ImmutableTask(RuntimeError):
    pass


class RecordDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS records (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self.conn.commit()

    def get(self, key):
        row = self.conn.execute("SELECT value FROM records WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, key, value):
        with self.conn:
            self.conn.execute("INSERT INTO records VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              (key, json.dumps(value, sort_keys=True)))

    def create_task(self, key, launch):
        try:
            with self.conn:
                self.conn.execute("INSERT INTO records VALUES (?, ?)",
                                  (key, json.dumps({"key": key, "launch": launch, "phase": "Suspended"})))
        except sqlite3.IntegrityError as exc:
            raise ImmutableTask("task exists; create is not a resume") from exc

    def count(self):
        return self.conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]

    def close(self):
        self.conn.close()


def resume_then_crash(tasks, actors, key):
    task, actor = tasks.get(key), actors.get(key)
    if task is None or actor is None:
        raise ValueError("the fixture requires both existing resources")
    task["phase"] = "Running"  # Local request state, not committed yet.
    actor["state"] = "Running"
    actors.put(key, actor)       # Runtime-side commit survives the exception.
    raise SimulatedCrash("stopped before writing the AX-like status record")


def recovery_plan(task, actor, *, expected_uid, requested_launch):
    if task is None:
        return "STOP_ORPHAN_REVIEW" if actor else "NO_RESOURCES"
    if task["launch"] != requested_launch:
        return "STOP_REQUEST_CHANGED"
    if actor is None:
        return "STOP_RUNTIME_MISSING"
    if not expected_uid:
        return "STOP_MISSING_PROVENANCE"
    if actor["key"] != task["key"] or actor["uid"] != expected_uid:
        return "STOP_IDENTITY_CHANGED"
    if actor["launch"] != task["launch"]:
        return "STOP_LAUNCH_CHANGED"
    if actor["state"] not in {"Running", "Suspended"}:
        return "STOP_LIFECYCLE_REVIEW"
    return "REVIEW_EXISTING_BEFORE_RESUME"


def demo():
    with TemporaryDirectory(prefix="ax-book-ch16-") as directory:
        paths = [Path(directory)/"tasks.sqlite", Path(directory)/"actors.sqlite"]
        tasks, actors = (RecordDB(path) for path in paths)
        key, launch, uid = "team-a/review-42", "approved-launch-a", "actor-u1"
        try:
            tasks.create_task(key, launch)
            actors.put(key, {"key": key, "launch": launch, "uid": uid, "state": "Suspended"})
            try:
                resume_then_crash(tasks, actors, key)
            except SimulatedCrash:
                pass
        finally:
            tasks.close(); actors.close()
        tasks, actors = (RecordDB(path) for path in paths)
        try:
            task, actor = tasks.get(key), actors.get(key)
            try:
                tasks.create_task(key, launch)
            except ImmutableTask:
                retry_create = "REJECTED_IMMUTABLE"
            plan = recovery_plan(task, actor, expected_uid=uid, requested_launch=launch)
            changed = dict(actor, uid="actor-u2")
            return {
                "source": "two_local_sqlite_records",
                "ax_and_substrate_contacted": False,
                "reopened": {"task_phase": task["phase"], "actor_state": actor["state"]},
                "retry_create": retry_create,
                "plan": plan,
                "planner_mutated_records": task != tasks.get(key) or actor != actors.get(key),
                "changed_uid_plan": recovery_plan(task, changed, expected_uid=uid, requested_launch=launch),
                "task_records": tasks.count(),
                "actor_records": actors.count(),
            }
        finally:
            tasks.close(); actors.close()


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
