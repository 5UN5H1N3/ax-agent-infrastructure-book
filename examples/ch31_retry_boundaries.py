"""Offline antipattern exercise; only temporary local SQLite databases are used.

The simulated target atomically stores its effect and idempotency receipt in ONE
transaction. A real remote service must supply that guarantee itself. Trusted
scope and approval digest fixtures do not authenticate a user or grant authority.
One coordinator is modeled; this is not an SDK, distributed lock or production
approval service. No HTTP, AX/Substrate, Git publication or real fault injection.
"""
from dataclasses import asdict, dataclass
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory


class ReplyLost(Exception):
    pass


class KnownNoEffect(Exception):
    pass


class Rejected(Exception):
    pass


class CoordinatorStopped(Exception):
    pass


class ContractConflict(Exception):
    pass


def text(value):
    return isinstance(value, str) and 0 < len(value) <= 160 and value == value.strip()


@dataclass(frozen=True)
class Action:
    tenant: str
    repository: str
    input_commit: str
    report_sha256: str

    def __post_init__(self):
        if not text(self.tenant) or not text(self.repository):
            raise ValueError("nonempty bounded scope required")
        for value, length in ((self.input_commit, 40), (self.report_sha256, 64)):
            if not isinstance(value, str) or not re.fullmatch(f"[0-9a-f]{{{length}}}", value):
                raise ValueError("full lowercase input digests required")

    def digest(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True,
                                        separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Scope:
    tenant: str
    repository: str

    def __post_init__(self):
        if not text(self.tenant) or not text(self.repository):
            raise ValueError("valid trusted scope fixture required")


def checked_contract(action, scope, approved_digest):
    if not isinstance(action, Action) or not isinstance(scope, Scope):
        raise ValueError("Action and Scope required")
    if (action.tenant, action.repository) != (scope.tenant, scope.repository):
        raise PermissionError("outside supplied scope")
    fingerprint = action.digest()
    if type(approved_digest) is not str or approved_digest != fingerprint:
        raise PermissionError("approval fixture does not match action")
    return fingerprint


class Target:
    MODES = {"ok", "drop_reply", "crash_after_commit", "unavailable", "reject"}

    def __init__(self, path, idempotent=True):
        if type(idempotent) is not bool:
            raise ValueError("idempotent must be boolean")
        self.path, self.idempotent, self.sends = str(path), idempotent, 0
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS effects (id INTEGER PRIMARY KEY, "
                       "op_key TEXT UNIQUE, digest TEXT NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        try:
            with db:
                yield db
        finally:
            db.close()

    def lookup(self, key, fingerprint):
        if not self.idempotent:
            raise ValueError("this target has no authoritative operation lookup")
        with self.connect() as db:
            row = db.execute("SELECT id, digest FROM effects WHERE op_key=?", (key,)).fetchone()
        if row and row[1] != fingerprint:
            raise ContractConflict("target key reused for different payload")
        return f"receipt-{row[0]}" if row else None

    def send(self, key, fingerprint, mode="ok"):
        if (not text(key) or type(fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or type(mode) is not str or mode not in self.MODES):
            raise ValueError("invalid simulated target request")
        self.sends += 1
        if mode == "unavailable":
            raise KnownNoEffect("fixture: target rejected before transaction")
        if mode == "reject":
            raise Rejected("fixture: policy rejected before transaction")
        with self.connect() as db:
            # Both the publication and its receipt are the same durable row.
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id, digest FROM effects WHERE op_key=?", (key,)).fetchone() if self.idempotent else None
            if row and row[1] != fingerprint:
                raise ContractConflict("target key reused for different payload")
            if row:
                receipt = f"receipt-{row[0]}"
            else:
                cursor = db.execute("INSERT INTO effects(op_key,digest) VALUES(?,?)",
                                    (key if self.idempotent else None, fingerprint))
                receipt = f"receipt-{cursor.lastrowid}"
        if mode == "drop_reply":
            raise ReplyLost("fixture: effect committed, reply lost")
        if mode == "crash_after_commit":
            raise CoordinatorStopped("fixture: stop before updating coordinator journal")
        return receipt

    def count(self):
        with self.connect() as db:
            return db.execute("SELECT count(*) FROM effects").fetchone()[0]


class Journal:
    def __init__(self, path):
        self.path = str(path)
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS intents (op_key TEXT PRIMARY KEY, "
                       "digest TEXT NOT NULL, budget INTEGER NOT NULL, attempts INTEGER NOT NULL, "
                       "state TEXT NOT NULL, receipt TEXT)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def reserve(self, key, fingerprint, budget):
        if (not text(key) or type(fingerprint) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                or type(budget) is not int or not 1 <= budget <= 10):
            raise ValueError("bounded operation key and budget 1..10 required")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO intents VALUES(?,?,?,0,'pending',NULL)",
                       (key, fingerprint, budget))
        row = self.read(key)
        if row["digest"] != fingerprint or row["budget"] != budget:
            raise ContractConflict("intent changed under an existing operation key")
        return row

    def read(self, key):
        with self.connect() as db:
            row = db.execute("SELECT * FROM intents WHERE op_key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(row)

    def claim(self, key):
        with self.connect() as db:
            return db.execute("UPDATE intents SET attempts=attempts+1,state='uncertain' "
                              "WHERE op_key=? AND attempts<budget", (key,)).rowcount == 1

    def mark(self, key, state, receipt=None):
        with self.connect() as db:
            db.execute("UPDATE intents SET state=?,receipt=? WHERE op_key=?", (state, receipt, key))


def execute(journal, target, key, action, scope, approved_digest, budget=2, mode="ok"):
    if not isinstance(journal, Journal) or not isinstance(target, Target):
        raise ValueError("Journal and Target required")
    fingerprint = checked_contract(action, scope, approved_digest)
    if type(mode) is not str or mode not in Target.MODES:
        raise ValueError("unknown fault fixture")
    row = journal.reserve(key, fingerprint, budget)
    if row["state"] in {"done", "rejected", "manual_review"}:
        return row
    if row["state"] == "uncertain" and not target.idempotent:
        journal.mark(key, "manual_review")
        return journal.read(key)
    while True:
        if target.idempotent:
            receipt = target.lookup(key, fingerprint)
            if receipt:
                journal.mark(key, "done", receipt)
                return journal.read(key)
        if not journal.claim(key):
            journal.mark(key, "budget_exhausted")
            return journal.read(key)
        try:
            receipt = target.send(key, fingerprint, mode)
        except KnownNoEffect:
            journal.mark(key, "pending")
        except Rejected:
            journal.mark(key, "rejected")
            return journal.read(key)
        except ReplyLost:
            if not target.idempotent:
                journal.mark(key, "manual_review")
                return journal.read(key)
        else:
            journal.mark(key, "done", receipt)
            return journal.read(key)


def nested_retries(operation, attempts=(2, 2, 2)):
    if (not isinstance(attempts, tuple) or not attempts or len(attempts) > 6
            or any(type(n) is not int or not 1 <= n <= 5 for n in attempts)):
        raise ValueError("one to six retry layers, each with 1..5 total attempts")
    def call(layer):
        for index in range(attempts[layer]):
            try:
                return operation() if layer == len(attempts) - 1 else call(layer + 1)
            except ReplyLost:
                if index == attempts[layer] - 1:
                    raise
    return call(0)


ACTION = Action("team-a", "billing", "a" * 40, "b" * 64)
SCOPE = Scope("team-a", "billing")


def demo():
    results = {}
    with TemporaryDirectory(prefix="ax-ch31-example-") as directory:
        root = Path(directory)
        bad = Target(root / "bad.sqlite", idempotent=False)
        try:
            nested_retries(lambda: bad.send("publish-1", ACTION.digest(), "drop_reply"))
        except ReplyLost:
            pass
        results["retry_cascade"] = {"status": "unknown", "sends": bad.sends, "effects": bad.count()}
        def run_case(name, mode="ok", idempotent=True, scope=SCOPE, approved=None):
            journal, target = Journal(root / (name + "-journal.sqlite")), Target(root / (name + "-target.sqlite"), idempotent)
            try:
                row = execute(journal, target, "publish-1", ACTION, scope,
                              ACTION.digest() if approved is None else approved, mode=mode)
                status = row["state"]
            except PermissionError:
                status = "guard_rejected"
            results[name] = {"status": status, "sends": target.sends, "effects": target.count()}
            return journal, target
        journal, target = run_case("bounded_idempotent", "drop_reply")
        try:
            changed = Action("team-a", "billing", "a" * 40, "c" * 64)
            execute(journal, target, "publish-1", changed, SCOPE, changed.digest())
        except ContractConflict:
            results["changed_intent"] = {"status": "contract_conflict", "sends": 0, "effects": target.count()}
        run_case("wrong_scope", scope=Scope("team-b", "billing"))
        run_case("stale_approval", approved="0" * 64)
        run_case("policy_denied", "reject")
        outage_journal, outage_target = run_case("outage", "unavailable")
        execute(outage_journal, outage_target, "publish-1", ACTION, SCOPE, ACTION.digest(), mode="unavailable")
        results["outage"]["sends_after_second_run"] = outage_target.sends
        legacy_journal, legacy_target = run_case("legacy_timeout", "drop_reply", False)
        execute(legacy_journal, legacy_target, "publish-1", ACTION, SCOPE, ACTION.digest())
        results["legacy_timeout"]["sends_after_second_run"] = legacy_target.sends
        jp, tp = root / "restart-journal.sqlite", root / "restart-target.sqlite"
        try:
            execute(Journal(jp), Target(tp), "publish-1", ACTION, SCOPE, ACTION.digest(), mode="crash_after_commit")
        except CoordinatorStopped:
            pass
        reopened_target = Target(tp)
        row = execute(Journal(jp), reopened_target, "publish-1", ACTION, SCOPE, ACTION.digest())
        results["restart"] = {"status": row["state"], "sends": reopened_target.sends,
                              "effects": reopened_target.count(), "attempts": row["attempts"]}
        key_journal, key_target = Journal(root / "keys-journal.sqlite"), Target(root / "keys-target.sqlite")
        for key in ("logical-publication-1", "logical-publication-2"):
            execute(key_journal, key_target, key, ACTION, SCOPE, ACTION.digest())
        results["new_key"] = {"status": "two_operations", "sends": key_target.sends, "effects": key_target.count()}
    return {"cases": results, "simulated_target": True, "live_services_executed": False}


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
