"""Offline investigation exercise, not an AX/Substrate diagnostic client.

All observations are fixtures about one frozen, completed-attempt snapshot.
Context labels and values are trusted inputs, not authenticated evidence. A
single synthetic clock scopes freshness. Changing real states must be split
into separate snapshots, rather than treated as contradictory static facts.
The output proposes only the next reading; no command or recovery is executed.
"""
from dataclasses import dataclass, replace
import json
import re


NAMES = ('worker_assigned', 'runner_ready', 'child_exit', 'checkout', 'result')
EXPECTED_COMMIT = 'b' * 40


def integer(value):
    return type(value) is int and value >= 0


def commit(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) is not None


@dataclass(frozen=True)
class Context:
    task: str
    actor_uid: str
    activation: str
    attempt: str
    snapshot: str

    def __post_init__(self):
        if any(not isinstance(v, str) or not v.strip() for v in
               (self.task, self.actor_uid, self.activation, self.attempt, self.snapshot)):
            raise ValueError('complete nonempty context required')


@dataclass(frozen=True)
class Observation:
    context: Context
    captured_ms: int
    name: str
    value: bool | int | str | None

    def __post_init__(self):
        if not isinstance(self.context, Context) or not integer(self.captured_ms):
            raise ValueError('context and integer capture time required')
        if self.name not in NAMES:
            raise ValueError('unknown observation name')
        v = self.value
        if v is None:
            return
        valid = {
            'worker_assigned': type(v) is bool,
            'runner_ready': type(v) is bool,
            'child_exit': type(v) is int and -1 <= v <= 255,
            'checkout': v == 'NO_HEAD' or commit(v),
            'result': isinstance(v, str) and v in ('valid', 'absent', 'invalid'),
        }[self.name]
        if not valid:
            raise ValueError('invalid value for observation')


def scoped_observations(observations, context, start_ms, as_of_ms):
    return [o for o in observations
            if o.context == context
            and start_ms <= o.captured_ms <= as_of_ms]


def collect(observations):
    facts, conflicting = {}, []
    for name in NAMES:
        values = {o.value for o in observations
                  if o.name == name and o.value is not None}
        if len(values) > 1:
            conflicting.append(name)
        facts[name] = next(iter(values)) if len(values) == 1 else None
    return facts, conflicting


def assess(observations, context, start_ms=100, as_of_ms=200,
           expected_commit=EXPECTED_COMMIT):
    observations = list(observations)
    if not isinstance(context, Context) or any(not isinstance(o, Observation) for o in observations):
        raise ValueError('Context and Observation objects required')
    if not integer(start_ms) or not integer(as_of_ms) or start_ms > as_of_ms:
        raise ValueError('ordered nonnegative integer window required')
    if not commit(expected_commit):
        raise ValueError('expected full commit hash required')
    relevant = scoped_observations(observations, context, start_ms, as_of_ms)
    facts, conflicting = collect(relevant)

    def hypothesis(name, predicate):
        if name in conflicting:
            return 'conflicting'
        return ('supported' if predicate(facts[name]) else 'refuted') if facts[name] is not None else 'unknown'

    hypotheses = {
        'no_worker': hypothesis('worker_assigned', lambda v: v is False),
        'runner_unready': hypothesis('runner_ready', lambda v: v is False),
        'command_nonzero': hypothesis('child_exit', lambda v: v != 0),
        'wrong_checkout': hypothesis('checkout', lambda v: v != expected_commit),
    }
    if conflicting:
        next_read = 'reconcile_evidence'
    elif facts['worker_assigned'] is None:
        next_read = 'read_assignment'
    elif not facts['worker_assigned']:
        next_read = 'inspect_eligibility'
    elif facts['runner_ready'] is None:
        next_read = 'read_runner'
    elif not facts['runner_ready']:
        next_read = 'inspect_bootstrap'
    elif facts['child_exit'] is None:
        next_read = 'read_child_outcome'
    elif facts['checkout'] is None:
        next_read = 'read_checkout'
    elif facts['checkout'] != expected_commit:
        next_read = 'inspect_workspace_setup'
    elif facts['child_exit'] != 0:
        next_read = 'inspect_child_exit'
    elif facts['result'] is None:
        next_read = 'read_result'
    elif facts['result'] != 'valid':
        next_read = 'inspect_result_contract'
    else:
        next_read = 'verify_user_delivery'
    return {
        'facts': facts, 'hypotheses': hypotheses, 'conflicting': conflicting,
        'ignored_observations': len(observations) - len(relevant),
        'next_read': next_read, 'root_cause': None,
        'root_cause_proven': False, 'actions_executed': False,
        'real_system_inspected': False,
    }


def demo_context():
    return Context('billing-review', 'actor-uid-1', 'activation-2', 'attempt-7', 'snapshot-1')


def fixture(**values):
    return [Observation(demo_context(), 150, name, value) for name, value in values.items()]


def demo():
    base = fixture(worker_assigned=True, runner_ready=True)
    exited = base + fixture(child_exit=2)
    wrong = exited + fixture(checkout='NO_HEAD', result='absent')
    other = replace(demo_context(), activation='activation-1')
    variants = {
        'only_ready': base,
        'child_exit_2': exited,
        'checkout_missing': wrong,
        'other_activation': exited + [Observation(other, 150, 'checkout', 'NO_HEAD')],
        'old_observation': exited + [Observation(demo_context(), 99, 'checkout', 'NO_HEAD')],
        'conflicting_exit': exited + fixture(child_exit=0),
        'no_worker': fixture(worker_assigned=False),
        'runner_unready': fixture(worker_assigned=True, runner_ready=False),
        'result_invalid': fixture(worker_assigned=True, runner_ready=True, child_exit=0,
                                  checkout=EXPECTED_COMMIT, result='invalid'),
        'all_checks_pass': fixture(worker_assigned=True, runner_ready=True, child_exit=0,
                                  checkout=EXPECTED_COMMIT, result='valid'),
    }
    return {name: assess(values, demo_context()) for name, values in variants.items()}


if __name__ == '__main__':
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
