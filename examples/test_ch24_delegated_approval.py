"""Deterministic tests of the chapter's local model, not of live auth services."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
from threading import Barrier
import unittest

from ch24_delegated_approval import (
    AUDIENCE, BASE, CANDIDATE, OTHER, RESOURCE, RIGHT, Authority, Denied,
    Identity, Operation, demo, fixture,
)


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.a, self.target, self.op, self.grant, self.approval = fixture()
        self.actor = Identity("task:publisher")

    def execute(self, **kwargs):
        return self.a.execute(self.actor, self.grant, self.op, self.approval,
                              self.target, **kwargs)

    def denied(self, reason, operation=None):
        with self.assertRaisesRegex(Denied, "^" + reason + "$"):
            (operation or self.execute)()
        self.assertEqual(self.target.effects, 0)

    def delegate(self, **changes):
        args = dict(caller=Identity("task:root"), parent_id="root",
                    actor="task:child", rights={RIGHT}, expires=50,
                    audience=AUDIENCE)
        args.update(changes)
        return self.a.delegate(**args)

    def test_approved_updates_target(self):
        self.assertEqual(self.execute(), "applied")
        self.assertEqual(self.target.value, CANDIDATE)
        self.assertEqual(self.target.effects, 1)
        self.assertEqual(self.a.states[self.approval], "applied")

    def test_other_actor(self):
        self.actor = Identity("task:reviewer")
        self.denied("actor_mismatch")

    def test_changed_candidate(self):
        self.op = replace(self.op, candidate=OTHER)
        self.denied("proposal_changed")

    def test_changed_expected(self):
        self.op = replace(self.op, expected=OTHER)
        self.denied("proposal_changed")

    def test_other_action(self):
        self.op = replace(self.op, action="repo.delete")
        self.denied("scope_denied")

    def test_other_resource(self):
        self.op = replace(self.op, resource="repo:foreign")
        self.denied("scope_denied")

    def test_root_revocation(self):
        self.a.revoked.add("root")
        self.denied("grant_revoked_or_missing")

    def test_child_revocation(self):
        self.a.revoked.add(self.grant)
        self.denied("grant_revoked_or_missing")

    def test_missing_parent(self):
        del self.a.grants["root"]
        self.denied("grant_revoked_or_missing")

    def test_child_expiry(self):
        self.denied("grant_expired", lambda: self.execute(now=50))

    def test_parent_expiry_checked_online(self):
        self.a.grants["root"] = replace(self.a.grants["root"], expires=1)
        self.denied("grant_expired")

    def test_approval_expiry_at_boundary(self):
        self.denied("approval_expired", lambda: self.execute(now=40))

    def test_policy_change(self):
        self.a.policy_version += 1
        self.denied("policy_changed")

    def test_approver_membership_removed(self):
        self.a.approvers.clear()
        self.denied("approver_denied")

    def test_self_approval_disallowed_even_for_approver(self):
        self.a.approvers.add("human:alice")
        self.denied("approver_denied", lambda: self.a.approve(
            Identity("human:alice"), self.grant, self.op))

    def test_unknown_approver(self):
        self.denied("approver_denied", lambda: self.a.approve(
            Identity("human:mallory"), self.grant, self.op))

    def test_no_approval(self):
        self.approval = "unknown"
        self.denied("approval_missing")

    def test_revoked_approval(self):
        self.a.states[self.approval] = "revoked"
        self.denied("approval_unavailable")

    def test_subject_bound(self):
        self.a.approvals[self.approval] = replace(
            self.a.approvals[self.approval], subject="human:other")
        self.denied("approval_binding_mismatch")

    def test_actor_bound(self):
        self.a.approvals[self.approval] = replace(
            self.a.approvals[self.approval], actor="task:other")
        self.denied("approval_binding_mismatch")

    def test_grant_bound_even_for_same_actor_and_rights(self):
        self.grant = self.delegate(actor="task:publisher")
        self.denied("approval_binding_mismatch")

    def test_approval_audience_bound(self):
        self.a.approvals[self.approval] = replace(
            self.a.approvals[self.approval], audience="other-publisher")
        self.denied("approval_binding_mismatch")

    def test_executor_audience(self):
        self.a.grants[self.grant] = replace(
            self.a.grants[self.grant], audience="other-publisher")
        self.denied("audience_mismatch")

    def test_authority_down_fails_closed(self):
        self.a.available = False
        self.denied("authority_unavailable")

    def test_stale_target_after_claim(self):
        self.denied("target_changed", lambda: self.execute(
            before_target=lambda: setattr(self.target, "value", OTHER)))
        self.assertEqual(self.target.value, OTHER)
        self.assertEqual(self.a.states[self.approval], "precondition_failed")

    def test_failed_precondition_cannot_reuse_approval(self):
        self.target.value = OTHER
        self.denied("target_changed")
        self.target.value = BASE
        self.denied("approval_unavailable")

    def test_replay(self):
        self.execute()
        with self.assertRaisesRegex(Denied, "approval_unavailable"):
            self.execute()
        self.assertEqual(self.target.effects, 1)

    def test_double_claim_one_winner_in_this_process(self):
        barrier = Barrier(2)
        def attempt():
            barrier.wait(timeout=5)
            try:
                return self.execute()
            except Denied as error:
                return str(error)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: attempt(), range(2)))
        self.assertCountEqual(outcomes, ["applied", "approval_unavailable"])
        self.assertEqual(self.target.effects, 1)

    def test_lost_reply_does_not_imply_no_effect(self):
        with self.assertRaises(TimeoutError):
            self.execute(lose_reply=True)
        self.assertEqual(self.target.effects, 1)
        self.assertEqual(self.target.value, CANDIDATE)
        self.assertEqual(self.a.states[self.approval], "uncertain")
        with self.assertRaisesRegex(Denied, "approval_unavailable"):
            self.execute()

    def test_revocation_after_claim_is_not_retroactive(self):
        self.execute(before_target=lambda: self.a.revoked.add("root"))
        self.assertEqual(self.target.effects, 1)
        self.assertEqual(self.a.states[self.approval], "applied")

    def test_delegated_scope_and_subject(self):
        grant = self.a.grants[self.grant]
        self.assertEqual(grant.subject, "human:alice")
        self.assertEqual(grant.rights, frozenset({RIGHT}))
        self.assertEqual(grant.delegations_left, 0)

    def test_scope_amplification(self):
        self.denied("scope_amplification", lambda: self.delegate(
            rights={RIGHT, ("repo.delete", RESOURCE)}))

    def test_empty_scope(self):
        self.denied("scope_amplification", lambda: self.delegate(rights=set()))

    def test_foreign_resource_delegation(self):
        self.denied("scope_amplification", lambda: self.delegate(
            rights={(RIGHT[0], "repo:other")}))

    def test_expiry_amplification(self):
        self.denied("invalid_expiry", lambda: self.delegate(expires=101))

    def test_already_expired_delegation(self):
        self.denied("invalid_expiry", lambda: self.delegate(expires=10, now=10))

    def test_audience_amplification(self):
        self.denied("audience_amplification", lambda: self.delegate(audience="other"))

    def test_depth_amplification(self):
        self.denied("delegation_amplification", lambda: self.delegate(delegations_left=2))

    def test_child_cannot_delegate(self):
        self.denied("delegation_denied", lambda: self.delegate(
            caller=self.actor, parent_id=self.grant))

    def test_caller_cannot_use_someone_elses_parent(self):
        self.denied("actor_mismatch", lambda: self.delegate(caller=Identity("task:other")))

    def test_read_only_child_cannot_get_write_approval(self):
        child = self.delegate(rights={("candidate.read", RESOURCE)})
        self.denied("scope_denied", lambda: self.a.approve(
            Identity("human:boris"), child, self.op))

    def test_approval_cannot_outlive_grant(self):
        self.denied("invalid_expiry", lambda: self.a.approve(
            Identity("human:boris"), self.grant, self.op, expires=51))

    def test_cycle_fails_closed(self):
        self.a.grants["root"] = replace(self.a.grants["root"], parent=self.grant)
        self.denied("invalid_chain")

    def test_immutable_record(self):
        with self.assertRaises(FrozenInstanceError):
            self.op.candidate = OTHER

    def test_tiny_schema_rejects_ambiguous_revision(self):
        for value in ("B" * 64, "b" * 63, 12, " b" * 32):
            with self.subTest(value=value), self.assertRaisesRegex(Denied, "invalid_revision"):
                replace(self.op, candidate=value).digest()

    def test_unknown_operation_cannot_get_digest(self):
        with self.assertRaisesRegex(Denied, "unknown_operation"):
            replace(self.op, resource="repo:other").digest()

    def test_digest_is_deterministic_and_sensitive(self):
        self.assertEqual(self.op.digest(), Operation().digest())
        self.assertNotEqual(self.op.digest(), replace(self.op, candidate=OTHER).digest())

    def test_demo_claims_match_effects(self):
        result = demo()
        self.assertEqual(len(result), 10)
        self.assertEqual(sum(x["effects"] for x in result.values()), 3)
        self.assertEqual(result["lost_reply"], {
            "reason": "uncertain", "effects": 1, "state": "uncertain"})
        self.assertEqual(result["stale_target"]["state"], "precondition_failed")


if __name__ == "__main__":
    unittest.main()
