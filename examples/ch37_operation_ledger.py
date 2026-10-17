"""Offline write/recovery exercise, using two separate temporary SQLite files.

The toy target owns configuration AND idempotency receipts in one transaction.
This models a guarantee supplied by a backend, not a proxy around a real router.
The ledger is a separate database; no transaction spans the two files.
Gate/approval/identity are trusted fixtures, not authentication or a policy SDK.
No network, model, MCP, AX, router, actual process kill or power-loss test is used.
Fault exceptions mark deterministic boundaries after a real SQLite commit.
Receipts never expire in this fixture. No automatic retries or lock reclamation.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
from tempfile import TemporaryDirectory


RUN = "lab-network-017"
OPERATION = "op-17"
KEY = "lab33-net-001"
ACTOR = "executor-37"
AUDIENCE = "lab-network-writer"


class Refused(Exception):
    """A local guard refused to start a send; no claim of remote outcome."""


class Conflict(Exception):
    """Toy backend rejected a request before applying its effect."""


class ReplyLost(Exception):
    pass


class WorkerStopped(BaseException):
    """Simulated stop: deliberately bypass normal executor error handling."""


def identifier(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9_.:-]{1,80}", value)


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


@dataclass(frozen=True)
class Action:
    tenant: str = "lab"
    device_uid: str = "router-lab-01"
    vrf: str = "LAB"
    prefix: str = "192.0.2.0/24"
    path: str = "A"
    expected_revision: str = "config-17"
    local_pref: int = 150
    contract: str = "network.set_local_pref/v1"

    def __post_init__(self):
        if not all(identifier(x) for x in (self.tenant, self.device_uid, self.vrf, self.path)):
            raise ValueError("invalid bounded identifier")
        if type(self.prefix) is not str or len(self.prefix) > 50:
            raise ValueError("invalid prefix")
        if str(ipaddress.ip_network(self.prefix, strict=True)) != self.prefix:
            raise ValueError("canonical CIDR required")
        if (type(self.expected_revision) is not str
                or not re.fullmatch(r"config-[1-9][0-9]{0,8}", self.expected_revision)):
            raise ValueError("invalid fixture revision")
        if type(self.local_pref) is not int or not 0 <= self.local_pref <= 4294967295:
            raise ValueError("local preference must be an unsigned 32-bit integer")
        if self.contract != "network.set_local_pref/v1":
            raise ValueError("unsupported fixture contract")

    def resource(self):
        return encode([self.tenant, self.device_uid, self.vrf, self.prefix, self.path])

    def digest(self):
        return hashlib.sha256(encode(asdict(self)).encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Approval:
    """Record already loaded from a trusted store; digest alone grants nothing."""
    actor: str
    audience: str
    run: str
    operation: str
    key: str
    digest: str
    expires: int = 100
    revoked: bool = False


@dataclass
class Gate:
    approval: Approval
    allow_write: bool = True
    allow_lookup: bool = True

    def check(self, key, action, actor, now, *, write):
        if type(now) is not int or now < 0:
            raise ValueError("nonnegative integer fixture clock required")
        if actor != ACTOR or action.resource() != Action().resource():
            raise Refused("scope_denied")
        if not write:
            if not self.allow_lookup:
                raise Refused("lookup_denied")
            return
        a = self.approval
        binding = (actor, AUDIENCE, RUN, OPERATION, key, action.digest())
        if (a.actor, a.audience, a.run, a.operation, a.key, a.digest) != binding:
            raise Refused("approval_binding_changed")
        if not self.allow_write or a.revoked or now >= a.expires:
            raise Refused("write_no_longer_authorized")


def gate_for(action=None):
    action = Action() if action is None else action
    return Gate(Approval(ACTOR, AUDIENCE, RUN, OPERATION, KEY, action.digest()))


@contextmanager
def connection(path, *, write=False):
    db = sqlite3.connect(str(path), timeout=3, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        if write:
            db.execute("BEGIN IMMEDIATE")
        yield db
        if write:
            db.execute("COMMIT")
    except BaseException:
        if db.in_transaction:
            db.execute("ROLLBACK")
        raise
    finally:
        db.close()


class Target:
    """A synchronous fake controller with permanent key retention in this DB."""
    def __init__(self, path):
        self.path = Path(path)
        with connection(self.path, write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS config "
                       "(id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER, local_pref INTEGER)")
            db.execute("INSERT OR IGNORE INTO config VALUES(1,17,100)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts "
                       "(key TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL)")

    def lookup(self, key, action):
        with connection(self.path) as db:
            row = db.execute("SELECT digest,body FROM receipts WHERE key=?", (key,)).fetchone()
        if row and row["digest"] != action.digest():
            raise Conflict("key_parameter_mismatch")
        return json.loads(row["body"]) if row else None

    def apply(self, key, action, *, fault="none"):
        if not identifier(key) or type(action) is not Action:
            raise ValueError("typed action and bounded key required")
        if fault not in {"none", "drop_reply", "stop_after_commit"}:
            raise ValueError("unknown fault")
        if action.resource() != Action().resource():
            raise Conflict("target_scope_denied")
        with connection(self.path, write=True) as db:
            # Replay a historical result BEFORE checking the current revision.
            row = db.execute("SELECT digest,body FROM receipts WHERE key=?", (key,)).fetchone()
            if row:
                if row["digest"] != action.digest():
                    raise Conflict("key_parameter_mismatch")
                receipt = json.loads(row["body"])
            else:
                current = db.execute("SELECT revision FROM config WHERE id=1").fetchone()[0]
                if action.expected_revision != f"config-{current}":
                    raise Conflict("stale_revision")
                revision = current + 1
                receipt = {"key": key, "digest": action.digest(),
                           "applied_revision": f"config-{revision}",
                           "local_pref": action.local_pref}
                db.execute("UPDATE config SET revision=?,local_pref=? WHERE id=1",
                           (revision, action.local_pref))
                db.execute("INSERT INTO receipts VALUES(?,?,?)",
                           (key, action.digest(), encode(receipt)))
        if fault == "drop_reply":
            raise ReplyLost("effect committed, response lost")
        if fault == "stop_after_commit":
            raise WorkerStopped("target committed; executor has not saved receipt")
        return receipt

    def read(self):
        with connection(self.path) as db:
            row = db.execute("SELECT revision,local_pref FROM config WHERE id=1").fetchone()
        return {"revision": f"config-{row[0]}", "local_pref": row[1]}

    def effect_count(self):
        with connection(self.path) as db:
            return db.execute("SELECT count(*) FROM receipts").fetchone()[0]


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        with connection(self.path, write=True) as db:
            db.execute("CREATE TABLE IF NOT EXISTS operations "
                       "(key TEXT PRIMARY KEY, run TEXT, operation TEXT, resource TEXT UNIQUE, "
                       "action TEXT, digest TEXT, state TEXT, attempts INTEGER, budget INTEGER, "
                       "receipt TEXT, UNIQUE(run,operation))")

    def prepare(self, action=None, *, key=KEY, run=RUN, operation=OPERATION, budget=2):
        action = Action() if action is None else action
        if type(action) is not Action or not all(identifier(x) for x in (key,run,operation)):
            raise ValueError("invalid intent")
        if type(budget) is not int or not 1 <= budget <= 5:
            raise ValueError("attempt budget must be 1..5")
        with connection(self.path, write=True) as db:
            row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if row:
                if (row["run"],row["operation"],row["digest"],row["budget"]) != (
                        run,operation,action.digest(),budget):
                    raise Refused("intent_changed")
                return
            try:
                db.execute("INSERT INTO operations VALUES(?,?,?,?,?,?,'PREPARED',0,?,NULL)",
                           (key,run,operation,action.resource(),encode(asdict(action)),action.digest(),budget))
            except sqlite3.IntegrityError as exc:
                raise Refused("operation_or_resource_reserved") from exc

    def read(self, key=KEY):
        with connection(self.path) as db:
            row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        return dict(row)

    def claim(self, key, gate, actor, now, *, retry=False):
        if type(retry) is not bool:
            raise ValueError("retry must be boolean")
        with connection(self.path, write=True) as db:
            row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            action = Action(**json.loads(row["action"]))
            if (row["run"],row["operation"]) != (RUN,OPERATION):
                raise Refused("unsupported_run")
            gate.check(key,action,actor,now,write=True)
            expected = "OUTCOME_UNKNOWN" if retry else "PREPARED"
            if row["state"] != expected:
                raise Refused("dispatch_not_allowed")
            if row["attempts"] >= row["budget"]:
                raise Refused("attempt_budget_exhausted")
            db.execute("UPDATE operations SET state='DISPATCHING',attempts=attempts+1 WHERE key=?", (key,))
        return action

    def unknown(self, key):
        with connection(self.path, write=True) as db:
            db.execute("UPDATE operations SET state='OUTCOME_UNKNOWN' "
                       "WHERE key=? AND state='DISPATCHING'", (key,))

    def accept_receipt(self, key, receipt):
        with connection(self.path, write=True) as db:
            row = db.execute("SELECT * FROM operations WHERE key=?", (key,)).fetchone()
            if row is None:
                raise KeyError(key)
            action = Action(**json.loads(row["action"]))
            expected = {"key":key,"digest":row["digest"],
                        "applied_revision":f"config-{int(action.expected_revision.split('-')[1])+1}",
                        "local_pref":action.local_pref}
            if type(receipt) is not dict or encode(receipt) != encode(expected):
                raise Refused("receipt_binding_invalid")
            if row["state"] not in {"DISPATCHING","OUTCOME_UNKNOWN","APPLIED"}:
                raise Refused("receipt_out_of_order")
            db.execute("UPDATE operations SET state='APPLIED',receipt=? WHERE key=?", (encode(receipt),key))

    def conflict(self, key):
        with connection(self.path, write=True) as db:
            db.execute("UPDATE operations SET state='CONFLICT' WHERE key=? AND state='DISPATCHING'", (key,))


class Executor:
    def __init__(self, ledger, target, gate):
        self.ledger,self.target,self.gate = ledger,target,gate

    def dispatch(self, *, key=KEY, now=10, retry=False, fault="none"):
        if fault not in {"none","before_send","drop_reply","stop_after_commit"}:
            raise ValueError("unknown fault")
        action = self.ledger.claim(key,self.gate,ACTOR,now,retry=retry)
        if fault == "before_send":
            raise WorkerStopped("claim committed; send not started")
        try:
            receipt = self.target.apply(key,action,fault=fault)
            self.ledger.accept_receipt(key,receipt)
        except ReplyLost:
            self.ledger.unknown(key)
        except Conflict:
            self.ledger.conflict(key)
        # Unexpected exceptions leave DISPATCHING for conservative recovery.
        return self.ledger.read(key)["state"]

    def recover(self, *, key=KEY, now=20):
        row = self.ledger.read(key)
        action = Action(**json.loads(row["action"]))
        self.gate.check(key,action,ACTOR,now,write=False)
        if row["state"] not in {"DISPATCHING","OUTCOME_UNKNOWN"}:
            raise Refused("recovery_not_needed")
        self.ledger.unknown(key)
        receipt = self.target.lookup(key,action)
        if receipt is not None:
            self.ledger.accept_receipt(key,receipt)
        # Absence alone never queues a write, even in the synchronous fixture.
        return self.ledger.read(key)["state"]


def demo():
    with TemporaryDirectory(prefix="ax-ch37-") as folder:
        ledger_path,target_path = Path(folder)/"ledger.db",Path(folder)/"target.db"
        ledger,target = Ledger(ledger_path),Target(target_path)
        gate = gate_for();ledger.prepare();executor = Executor(ledger,target,gate)
        first = executor.dispatch(fault="drop_reply")
        before = target.effect_count()
        # New Python objects open the existing files, not an in-memory checkpoint.
        resumed = Executor(Ledger(ledger_path),Target(target_path),gate)
        recovered = resumed.recover(now=101) # write approval has expired; lookup is allowed.
        row = resumed.ledger.read()
        return {"key":KEY,"after_lost_reply":first,"after_recovery":recovered,
                "dispatch_attempts":row["attempts"],"effects_before_lookup":before,
                "effects_after_lookup":resumed.target.effect_count(),
                "current_config":resumed.target.read(),
                "goal_verification":"NOT_EVALUATED","network_calls":0}


if __name__ == "__main__":
    print(json.dumps(demo(),indent=2,ensure_ascii=False))
