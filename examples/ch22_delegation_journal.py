"""Offline delegation protocol exercise, not an AX client or workflow engine.

Two independent SQLite files represent the coordinator journal and a fake AX
service. A third directory holds synthetic reports by content hash. Reopening
objects demonstrates coordinator recovery, not guest or process restoration.
One active coordinator is assumed; production leases/fencing are not modeled.
"""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import tempfile

CANDIDATE = 'b' * 40
ROOT_ID = 'review-billing-142-v2'
REQUIRED = ('tests', 'review')


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


@contextmanager
def transaction(path):
    db = sqlite3.connect(path, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN IMMEDIATE')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


class FakeAX:
    """Only name uniqueness and Suspended -> Running; no AX RPC is called."""
    def __init__(self, path):
        self.path = path
        with transaction(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS tasks '
                       '(name TEXT PRIMARY KEY, fingerprint TEXT, phase TEXT)')

    def get(self, name):
        with transaction(self.path) as db:
            row = db.execute('SELECT * FROM tasks WHERE name=?', (name,)).fetchone()
            return dict(row) if row else None

    def create(self, name, fingerprint, lose_reply=False):
        with transaction(self.path) as db:
            if db.execute('SELECT 1 FROM tasks WHERE name=?', (name,)).fetchone():
                raise ValueError('name already occupied')
            db.execute('INSERT INTO tasks VALUES (?, ?, ?)',
                       (name, fingerprint, 'Suspended'))
        # Commit precedes the simulated transport failure.
        if lose_reply:
            raise TimeoutError('create reply lost after persistence')

    def resume(self, name):
        with transaction(self.path) as db:
            row = db.execute('SELECT phase FROM tasks WHERE name=?', (name,)).fetchone()
            if row is None or row['phase'] not in ('Suspended', 'Running'):
                raise ValueError('execution needs separate recovery')
            db.execute('UPDATE tasks SET phase=? WHERE name=?', ('Running', name))

    def count(self):
        with transaction(self.path) as db:
            return db.execute('SELECT COUNT(*) FROM tasks').fetchone()[0]


class Artifacts:
    """Local synthetic fixtures; a digest proves bytes, not report truth."""
    def __init__(self, path):
        self.path = Path(path)
        self.path.mkdir(exist_ok=True)

    def put(self, report):
        data = encode(report)
        key = hashlib.sha256(data).hexdigest()
        target = self.path / key
        if not target.exists():
            with target.open('xb') as stream:
                stream.write(data)
        return key

    def read(self, key):
        if not isinstance(key, str) or not re.fullmatch('[0-9a-f]{64}', key):
            raise ValueError('invalid artifact hash')
        data = (self.path / key).read_bytes()
        if len(data) > 4096 or hashlib.sha256(data).hexdigest() != key:
            raise ValueError('artifact bytes do not match')
        return json.loads(data)


@dataclass(frozen=True)
class Result:
    child_id: str
    commit: str
    verdict: str
    artifact_sha256: str
    used_steps: int


class Coordinator:
    def __init__(self, path, candidate=CANDIDATE):
        if not re.fullmatch('[0-9a-f]{40}', candidate):
            raise ValueError('full candidate commit required')
        self.path = path
        with transaction(path) as db:
            db.execute('CREATE TABLE IF NOT EXISTS root '
                       '(id TEXT PRIMARY KEY, candidate TEXT, total INTEGER, reserve INTEGER)')
            db.execute('CREATE TABLE IF NOT EXISTS children '
                       '(role TEXT PRIMARY KEY, child_id TEXT UNIQUE, fingerprint TEXT, '
                       'cap INTEGER, held INTEGER, used INTEGER, state TEXT, result TEXT)')
            db.execute('INSERT OR IGNORE INTO root VALUES (?, ?, 12, 4)',
                       (ROOT_ID, candidate))
            if db.execute('SELECT candidate FROM root').fetchone()[0] != candidate:
                raise ValueError('new candidate requires a new round journal')

    def child(self, role):
        with transaction(self.path) as db:
            row = db.execute('SELECT * FROM children WHERE role=?', (role,)).fetchone()
            if row is None:
                raise ValueError('unplanned role')
            return dict(row)

    def plan(self, role, cap=4):
        if not isinstance(role, str) or not re.fullmatch('[a-z]+', role):
            raise ValueError('role must be a short label')
        if not integer(cap, 1):
            raise ValueError('positive integer budget required')
        with transaction(self.path) as db:
            root = db.execute('SELECT * FROM root').fetchone()
            contract = {'root': ROOT_ID, 'round': 2, 'attempt': 1, 'role': role,
                        'commit': root['candidate'], 'schema': 1, 'cap': cap}
            fingerprint = digest(contract)
            old = db.execute('SELECT * FROM children WHERE role=?', (role,)).fetchone()
            if old:
                if old['fingerprint'] != fingerprint:
                    raise ValueError('existing contract differs')
                return old['child_id']
            held, used = db.execute('SELECT COALESCE(SUM(held),0), '
                                   'COALESCE(SUM(used),0) FROM children').fetchone()
            if used + held + cap > root['total'] - root['reserve']:
                raise ValueError('budget exhausted')
            if db.execute('SELECT COUNT(*) FROM children').fetchone()[0] >= 2:
                raise ValueError('child count limit')
            name = 'verify-' + fingerprint[:24]
            db.execute('INSERT INTO children VALUES (?, ?, ?, ?, ?, 0, ?, NULL)',
                       (role, name, fingerprint, cap, cap, 'reserved'))
            return name

    def dispatch(self, role, ax, lose_reply=False):
        child = self.child(role)
        if child['state'] == 'settled':
            return 'settled'
        task = ax.get(child['child_id'])
        action = 'attached'
        if task is None:
            ax.create(child['child_id'], child['fingerprint'], lose_reply)
            task = ax.get(child['child_id'])
            action = 'created'
        if task['fingerprint'] != child['fingerprint']:
            raise ValueError('task contract mismatch')
        if task['phase'] not in ('Suspended', 'Running'):
            raise ValueError('execution needs separate recovery')
        if task['phase'] == 'Suspended':
            ax.resume(child['child_id'])
        with transaction(self.path) as db:
            db.execute("UPDATE children SET state='waiting' WHERE role=? "
                       "AND state='reserved'", (role,))
        return action

    def accept(self, role, result, artifacts):
        if not isinstance(result, Result) or not integer(result.used_steps):
            raise ValueError('invalid result schema')
        payload = encode(asdict(result)).decode()
        with transaction(self.path) as db:
            child = db.execute('SELECT * FROM children WHERE role=?', (role,)).fetchone()
            root = db.execute('SELECT * FROM root').fetchone()
            if child is None or result.child_id != child['child_id']:
                raise ValueError('child identity mismatch')
            if result.commit != root['candidate']:
                raise ValueError('input revision mismatch')
            if result.verdict not in ('pass', 'changes_required'):
                raise ValueError('unknown verdict')
            if result.used_steps > child['cap']:
                raise ValueError('budget overrun requires reconciliation')
            if child['state'] == 'settled':
                if child['result'] == payload:
                    return 'duplicate'
                raise ValueError('conflicting terminal result')
            if child['state'] != 'waiting':
                raise ValueError('dispatch not reconciled yet')
            report = artifacts.read(result.artifact_sha256)
            expected = {'role': role, 'commit': result.commit, 'verdict': result.verdict}
            if report != expected:
                raise ValueError('artifact contract mismatch')
            db.execute("UPDATE children SET state='settled', held=0, used=?, result=? "
                       'WHERE role=?', (result.used_steps, payload, role))
        return 'accepted'

    def join(self):
        with transaction(self.path) as db:
            rows = {r['role']: r for r in db.execute('SELECT * FROM children')}
            if any(role not in rows or rows[role]['state'] != 'settled' for role in REQUIRED):
                return 'waiting'
            if any(json.loads(rows[role]['result'])['verdict'] != 'pass' for role in REQUIRED):
                return 'changes_required'
            return 'ready_for_human'

    def budget(self):
        with transaction(self.path) as db:
            root = db.execute('SELECT * FROM root').fetchone()
            held, used = db.execute('SELECT COALESCE(SUM(held),0), '
                                   'COALESCE(SUM(used),0) FROM children').fetchone()
            return {'spent': used, 'held': held, 'root_reserve': root['reserve'],
                    'available_for_children': root['total'] - root['reserve'] - used - held}


def fixture(coordinator, role, artifacts, commit=CANDIDATE, verdict='pass', used=3):
    key = artifacts.put({'role': role, 'commit': commit, 'verdict': verdict})
    return Result(coordinator.child(role)['child_id'], commit, verdict, key, used)


def demo():
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory)
        coordinator = Coordinator(base / 'journal.sqlite')
        ax = FakeAX(base / 'service.sqlite')
        artifacts = Artifacts(base / 'artifacts')
        coordinator.plan('tests')
        coordinator.plan('review')
        output = {'after_reservation': coordinator.budget()}
        try:
            coordinator.plan('extra')
        except ValueError as error:
            output['extra_child'] = str(error)
        try:
            coordinator.dispatch('tests', ax, lose_reply=True)
        except TimeoutError:
            output['lost_reply'] = {'journal': coordinator.child('tests')['state'],
                                    'execution': ax.get(coordinator.child('tests')['child_id'])['phase']}
        # Reopen both independent persistent stores, not the old Python objects.
        coordinator = Coordinator(base / 'journal.sqlite')
        ax = FakeAX(base / 'service.sqlite')
        output['after_restart'] = coordinator.dispatch('tests', ax)
        coordinator.dispatch('review', ax)
        try:
            coordinator.accept('tests', fixture(coordinator, 'tests', artifacts, 'a' * 40), artifacts)
        except ValueError as error:
            output['stale_result'] = str(error)
        output['after_stale'] = {'join': coordinator.join(), 'held': coordinator.budget()['held']}
        tests = fixture(coordinator, 'tests', artifacts)
        coordinator.accept('tests', tests, artifacts)
        output['after_tests'] = coordinator.join()
        coordinator.accept('review', fixture(coordinator, 'review', artifacts, used=2), artifacts)
        output['duplicate'] = coordinator.accept('tests', tests, artifacts)
        output['final'] = {'join': coordinator.join(), 'tasks': ax.count(),
                           'execution_phases': [ax.get(coordinator.child(r)['child_id'])['phase']
                                                for r in REQUIRED], 'budget': coordinator.budget()}
        return output


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
