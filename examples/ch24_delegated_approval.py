"""Offline authority model, NOT an authentication or production approval service.

All identities and root grants are trusted fixtures. No JWT, crypto, network,
Git, AX or Substrate is used. One process/lock models atomic claims; a separate
fake target uses compare-and-set. Nothing here proves distributed exactly-once.
"""

from dataclasses import asdict, dataclass, replace
import hashlib
import json
import re
from threading import Lock


AUDIENCE = "billing-publisher"
RESOURCE = "repo:billing-uid/ref:refs/heads/main"
RIGHT = ("candidate.promote", RESOURCE)
BASE = "a" * 64
CANDIDATE = "b" * 64
OTHER = "c" * 64


class Denied(Exception):
    """Stable reason code, not a public error with resource details."""


@dataclass(frozen=True)
class Identity:
    """Already authenticated by a boundary outside this demonstration."""

    principal: str


@dataclass(frozen=True)
class Operation:
    action: str = "candidate.promote"
    resource: str = RESOURCE
    expected: str = BASE
    candidate: str = CANDIDATE

    def digest(self):
        # A deliberately tiny schema: fixed ASCII identifiers and 64 hex digits.
        if self.action != RIGHT[0] or self.resource != RESOURCE:
            raise Denied("unknown_operation")
        if not all(isinstance(v, str) and re.fullmatch("[0-9a-f]{64}", v)
                   for v in (self.expected, self.candidate)):
            raise Denied("invalid_revision")
        payload = {"schema": 1, **asdict(self)}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("ascii")).hexdigest()


@dataclass(frozen=True)
class Grant:
    id: str
    subject: str
    actor: str
    parent: str | None
    rights: frozenset[tuple[str, str]]
    audience: str
    expires: int
    delegations_left: int


@dataclass(frozen=True)
class Approval:
    id: str
    grant_id: str
    subject: str
    actor: str
    audience: str
    operation_digest: str
    approver: str
    policy_version: int
    expires: int


def authorize(grant, actor, operation, approval, policy_version, approvers):
    """Pure decision over records already loaded from trusted stores."""
    if actor.principal != grant.actor:
        raise Denied("actor_mismatch")
    if (operation.action, operation.resource) not in grant.rights:
        raise Denied("scope_denied")
    if (approval.grant_id, approval.subject, approval.actor, approval.audience) != (
        grant.id, grant.subject, grant.actor, grant.audience
    ):
        raise Denied("approval_binding_mismatch")
    if operation.digest() != approval.operation_digest:
        raise Denied("proposal_changed")
    if approval.policy_version != policy_version:
        raise Denied("policy_changed")
    if approval.approver == grant.subject or approval.approver not in approvers:
        raise Denied("approver_denied")


class FakeRef:
    """A separate toy target, not a GitHub merge implementation."""

    def __init__(self):
        self.value = BASE
        self.effects = 0
        self.lock = Lock()

    def compare_and_set(self, expected, candidate):
        with self.lock:
            if self.value != expected:
                raise Denied("target_changed")
            self.value = candidate
            self.effects += 1


class Authority:
    def __init__(self):
        self.lock = Lock()
        self.available = True
        self.policy_version = 1
        self.approvers = {"human:boris"}
        root = Grant("root", "human:alice", "task:root", None,
                     frozenset({RIGHT, ("candidate.read", RESOURCE)}),
                     AUDIENCE, 100, 2)
        self.grants = {root.id: root}
        self.approvals = {}
        self.states = {}
        self.revoked = set()

    def _live(self, grant_id, now, seen=None):
        if not self.available:
            raise Denied("authority_unavailable")
        seen = set() if seen is None else seen
        if grant_id in seen:
            raise Denied("invalid_chain")
        seen.add(grant_id)
        grant = self.grants.get(grant_id)
        if grant is None or grant_id in self.revoked:
            raise Denied("grant_revoked_or_missing")
        if now >= grant.expires:
            raise Denied("grant_expired")
        if grant.parent is not None:
            self._live(grant.parent, now, seen)
        return grant

    def delegate(self, caller, parent_id, actor, rights, expires, audience,
                 now=0, delegations_left=0):
        with self.lock:
            parent = self._live(parent_id, now)
            rights = frozenset(rights)
            if caller.principal != parent.actor:
                raise Denied("actor_mismatch")
            if parent.delegations_left <= 0:
                raise Denied("delegation_denied")
            if not rights or not rights <= parent.rights:
                raise Denied("scope_amplification")
            if not now < expires <= parent.expires:
                raise Denied("invalid_expiry")
            if audience != parent.audience:
                raise Denied("audience_amplification")
            if not 0 <= delegations_left < parent.delegations_left:
                raise Denied("delegation_amplification")
            grant_id = f"grant-{len(self.grants)}"
            grant = Grant(grant_id, parent.subject, actor, parent_id, rights,
                          audience, expires, delegations_left)
            self.grants[grant_id] = grant
            return grant_id

    def approve(self, human, grant_id, operation, expires=40, now=0):
        with self.lock:
            grant = self._live(grant_id, now)
            if human.principal == grant.subject or human.principal not in self.approvers:
                raise Denied("approver_denied")
            if grant.audience != AUDIENCE:
                raise Denied("audience_mismatch")
            if (operation.action, operation.resource) not in grant.rights:
                raise Denied("scope_denied")
            if not now < expires <= grant.expires:
                raise Denied("invalid_expiry")
            approval_id = f"approval-{len(self.approvals)}"
            self.approvals[approval_id] = Approval(
                approval_id, grant_id, grant.subject, grant.actor, grant.audience,
                operation.digest(), human.principal, self.policy_version, expires)
            self.states[approval_id] = "approved"
            return approval_id

    def execute(self, actor, grant_id, operation, approval_id, target, now=1,
                before_target=None, lose_reply=False):
        # Claim and authority checks share ONE in-process lock. In a real system
        # this needs a durable transaction/conditional update, not a Python lock.
        with self.lock:
            grant = self._live(grant_id, now)
            if grant.audience != AUDIENCE:
                raise Denied("audience_mismatch")
            approval = self.approvals.get(approval_id)
            if approval is None:
                raise Denied("approval_missing")
            if now >= approval.expires:
                raise Denied("approval_expired")
            if self.states[approval_id] != "approved":
                raise Denied("approval_unavailable")
            authorize(grant, actor, operation, approval,
                      self.policy_version, self.approvers)
            self.states[approval_id] = "claimed"
        try:
            if before_target is not None:
                before_target()  # deterministic target race in the tests
            target.compare_and_set(operation.expected, operation.candidate)
            if lose_reply:
                raise TimeoutError("simulated lost reply AFTER target update")
        except Denied:
            self.states[approval_id] = "precondition_failed"
            raise
        except Exception:
            self.states[approval_id] = "uncertain"
            raise
        self.states[approval_id] = "applied"
        return "applied"


def fixture():
    authority = Authority()
    target = FakeRef()
    operation = Operation()
    grant = authority.delegate(Identity("task:root"), "root", "task:publisher",
                               {RIGHT}, 50, AUDIENCE)
    approval = authority.approve(Identity("human:boris"), grant, operation)
    return authority, target, operation, grant, approval


def demo():
    output = {}
    for case in ("approved", "changed_candidate", "stale_target", "root_revoked",
                 "policy_changed", "other_actor", "approval_expired", "replay",
                 "lost_reply", "authority_down"):
        authority, target, operation, grant, approval = fixture()
        caller = Identity("task:publisher")
        now = 1
        options = {}
        if case == "changed_candidate":
            operation = replace(operation, candidate=OTHER)
        elif case == "stale_target":
            options["before_target"] = lambda: setattr(target, "value", OTHER)
        elif case == "root_revoked":
            authority.revoked.add("root")
        elif case == "policy_changed":
            authority.policy_version += 1
        elif case == "other_actor":
            caller = Identity("task:reviewer")
        elif case == "approval_expired":
            now = 40
        elif case == "replay":
            authority.execute(caller, grant, operation, approval, target)
        elif case == "lost_reply":
            options["lose_reply"] = True
        elif case == "authority_down":
            authority.available = False
        try:
            reason = authority.execute(caller, grant, operation, approval, target,
                                       now=now, **options)
        except Denied as error:
            reason = str(error)
        except TimeoutError:
            reason = "uncertain"
        output[case] = {"reason": reason, "effects": target.effects,
                        "state": authority.states[approval]}
    return output


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
