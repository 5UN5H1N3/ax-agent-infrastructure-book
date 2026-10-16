"""Offline security-design exercise. No network, model, AX or sandbox is used.

Session lookup, object authorization and egress are deliberately small models.
Everything runs in one Python process: this is NOT an isolation boundary.
Controls can only be changed by the test driver to demonstrate missing layers.
"""
from dataclasses import dataclass, replace
import json
import re

COMMIT = 'b' * 40
ROUTES = {'artifact.read': 'artifacts', 'review.record': 'reviews',
          'debug.send': 'external'}


@dataclass(frozen=True)
class Context:
    principal: str
    tenant: str
    repository: str
    commit: str
    actions: frozenset[str]
    destinations: frozenset[str]


@dataclass(frozen=True)
class Artifact:
    tenant: str
    repository: str
    commit: str
    text: str


@dataclass(frozen=True)
class Proposal:
    action: str
    artifact_id: str
    verdict: str | None = None


@dataclass(frozen=True)
class Controls:
    operations: bool = True
    objects: bool = True
    egress: bool = True


def parse_proposal(body):
    if type(body) is not dict:
        raise ValueError('schema_denied')
    action = body.get('action')
    if not isinstance(action, str) or action not in ROUTES:
        raise ValueError('schema_denied')
    keys = {'action', 'artifact_id'}
    if action == 'review.record':
        keys.add('verdict')
    if set(body) != keys:
        raise ValueError('schema_denied')
    artifact_id = body['artifact_id']
    if not isinstance(artifact_id, str) or not re.fullmatch('[a-z0-9-]{1,64}', artifact_id):
        raise ValueError('schema_denied')
    verdict = body.get('verdict')
    if action == 'review.record' and verdict not in ('pass', 'changes_required'):
        raise ValueError('schema_denied')
    return Proposal(action, artifact_id, verdict)


def authorize(context, proposal, artifact, controls):
    if controls.operations and proposal.action not in context.actions:
        return 'operation_denied'
    if artifact is None:
        return 'resource_denied'
    expected = (context.tenant, context.repository, context.commit)
    actual = (artifact.tenant, artifact.repository, artifact.commit)
    if controls.objects and actual != expected:
        return 'resource_denied'
    return 'allow'


class Audit:
    """Decision evidence only, not an authenticated or durable audit service."""
    def __init__(self, available=True):
        self.available = available
        self.events = []

    def record(self, event):
        if not self.available:
            return False
        self.events.append(dict(event))
        return True


class FakeBackend:
    """Records local effects. The 'external' destination is just a list entry."""
    def __init__(self):
        self.effects = []

    def execute(self, proposal, artifact):
        destination = ROUTES[proposal.action]
        value = proposal.verdict if proposal.action == 'review.record' else artifact.text
        self.effects.append({'destination': destination, 'action': proposal.action,
                             'artifact_id': proposal.artifact_id, 'value': value})
        return {'status': 'recorded'}


class Gateway:
    def __init__(self, sessions, catalog, controls=Controls(), audit=None):
        # Trusted fixtures for the exercise, NOT caller-controlled request fields.
        self.sessions = dict(sessions)
        self.catalog = dict(catalog)
        self.controls = controls
        self.audit = audit if audit is not None else Audit()
        self.backend = FakeBackend()

    def execute(self, session, body):
        context = self.sessions.get(session) if isinstance(session, str) else None
        proposal = None
        artifact = None
        reason = 'authentication_denied'
        if context is not None:
            try:
                proposal = parse_proposal(body)
            except ValueError:
                reason = 'schema_denied'
            else:
                artifact = self.catalog.get(proposal.artifact_id)
                reason = authorize(context, proposal, artifact, self.controls)
                if reason == 'allow' and self.controls.egress:
                    if ROUTES[proposal.action] not in context.destinations:
                        reason = 'egress_denied'
        event = {'principal': context.principal if context else None,
                 'action': proposal.action if proposal else None,
                 'artifact_id': proposal.artifact_id if proposal else None,
                 'policy_revision': 'exercise-1', 'decision': reason}
        if not self.audit.record(event):
            return {'decision': 'deny', 'reason': 'audit_unavailable'}
        if reason != 'allow':
            return {'decision': 'deny', 'reason': reason}
        self.backend.execute(proposal, artifact)
        return {'decision': 'allow', 'reason': 'allow'}


def fixtures(controls=Controls(), audit=None):
    context = Context('task:billing-review', 'engineering', 'billing', COMMIT,
                      frozenset({'artifact.read', 'review.record'}),
                      frozenset({'artifacts', 'reviews'}))
    own = Artifact('engineering', 'billing', COMMIT, 'DEMO-ONLY-billing-report')
    catalog = {'billing-report': own,
               'finance-report': replace(own, tenant='finance', text='DEMO-ONLY-finance-report'),
               'other-repo': replace(own, repository='payroll'),
               'old-report': replace(own, commit='a' * 40)}
    return Gateway({'fixture-session': context}, catalog, controls, audit)


def request(action='artifact.read', artifact_id='billing-report', **extra):
    return {'action': action, 'artifact_id': artifact_id, **extra}


def run_case(body, controls=Controls(), audit=None, session='fixture-session'):
    gateway = fixtures(controls, audit)
    result = gateway.execute(session, body)
    return {**result, 'effects': len(gateway.backend.effects),
            'external_deliveries': sum(e['destination'] == 'external'
                                       for e in gateway.backend.effects)}


def demo():
    send = request('debug.send')
    return {
        'legitimate_read': run_case(request()),
        'foreign_read': run_case(request(artifact_id='finance-report')),
        'forged_tenant': run_case(request(tenant='finance')),
        'injected_send': run_case(send),
        'operation_check_removed': run_case(send, Controls(operations=False)),
        'egress_check_removed': run_case(send, Controls(egress=False)),
        'both_checks_removed': run_case(send, Controls(operations=False, egress=False)),
        'object_check_removed': run_case(request(artifact_id='finance-report'), Controls(objects=False)),
        'audit_unavailable': run_case(request('review.record', verdict='pass'), audit=Audit(False)),
        # A typed and authorized verdict is not proof of correct review.
        'unproven_pass': run_case(request('review.record', verdict='pass')),
    }


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
