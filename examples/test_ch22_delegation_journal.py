"""Protocol invariants on synthetic data; these tests do not connect to AX."""
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from ch22_delegation_journal import (
    Artifacts, CANDIDATE, Coordinator, FakeAX, Result, demo, fixture, transaction,
)


class DelegationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name)
        self.coordinator = Coordinator(self.base / 'journal.sqlite')
        self.ax = FakeAX(self.base / 'service.sqlite')
        self.artifacts = Artifacts(self.base / 'artifacts')

    def waiting(self, role='tests'):
        self.coordinator.plan(role)
        self.coordinator.dispatch(role, self.ax)
        return fixture(self.coordinator, role, self.artifacts)

    def test_reservation_is_persisted_before_any_task(self):
        self.coordinator.plan('tests')
        self.assertEqual(self.ax.count(), 0)
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_planning_same_contract_is_idempotent(self):
        first = self.coordinator.plan('tests')
        self.assertEqual(self.coordinator.plan('tests'), first)
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_changed_cap_cannot_reuse_contract(self):
        self.coordinator.plan('tests')
        with self.assertRaisesRegex(ValueError, 'contract differs'):
            self.coordinator.plan('tests', 3)

    def test_second_connection_observes_reserved_budget(self):
        other = Coordinator(self.base / 'journal.sqlite')
        self.coordinator.plan('tests', 5)
        with self.assertRaisesRegex(ValueError, 'budget exhausted'):
            other.plan('review', 4)
        self.assertEqual(other.budget()['held'], 5)

    def test_invalid_caps_are_rejected(self):
        for value in (0, -1, True, 1.5, '4'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.coordinator.plan('tests', value)

    def test_two_small_children_still_enforce_count_limit(self):
        self.coordinator.plan('tests', 1)
        self.coordinator.plan('review', 1)
        with self.assertRaisesRegex(ValueError, 'child count limit'):
            self.coordinator.plan('extra', 1)

    def test_new_candidate_requires_new_journal(self):
        with self.assertRaisesRegex(ValueError, 'new round'):
            Coordinator(self.base / 'journal.sqlite', 'a' * 40)

    def test_full_commit_required(self):
        with self.assertRaises(ValueError):
            Coordinator(self.base / 'journal.sqlite', 'bbbbbbb')

    def test_lost_create_reply_keeps_reservation_and_suspended_task(self):
        name = self.coordinator.plan('tests')
        with self.assertRaises(TimeoutError):
            self.coordinator.dispatch('tests', self.ax, lose_reply=True)
        self.assertEqual(self.coordinator.child('tests')['state'], 'reserved')
        self.assertEqual(self.coordinator.budget()['held'], 4)
        self.assertEqual(self.ax.get(name)['phase'], 'Suspended')

    def test_reopened_coordinator_attaches_without_duplicate(self):
        name = self.coordinator.plan('tests')
        with self.assertRaises(TimeoutError):
            self.coordinator.dispatch('tests', self.ax, lose_reply=True)
        reopened = Coordinator(self.base / 'journal.sqlite')
        service = FakeAX(self.base / 'service.sqlite')
        self.assertEqual(reopened.dispatch('tests', service), 'attached')
        self.assertEqual(service.count(), 1)
        self.assertEqual(service.get(name)['phase'], 'Running')

    def test_name_is_not_enough_for_attach(self):
        name = self.coordinator.plan('tests')
        self.ax.create(name, 'different-launch-contract')
        with self.assertRaisesRegex(ValueError, 'contract mismatch'):
            self.coordinator.dispatch('tests', self.ax)
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_read_error_is_not_absence(self):
        self.coordinator.plan('tests')
        class Unavailable:
            def get(self, name):
                raise OSError('service unreachable')
            def create(self, *args):
                raise AssertionError('must not create on a read error')
        with self.assertRaises(OSError):
            self.coordinator.dispatch('tests', Unavailable())

    def test_unknown_phase_does_not_trigger_replacement(self):
        name = self.coordinator.plan('tests')
        self.ax.create(name, self.coordinator.child('tests')['fingerprint'])
        with transaction(self.ax.path) as db:
            db.execute("UPDATE tasks SET phase='Failed'")
        with self.assertRaisesRegex(ValueError, 'separate recovery'):
            self.coordinator.dispatch('tests', self.ax)
        self.assertEqual(self.ax.count(), 1)

    def test_stale_result_keeps_join_and_budget_open(self):
        valid = self.waiting()
        with self.assertRaisesRegex(ValueError, 'revision mismatch'):
            self.coordinator.accept('tests', replace(valid, commit='a' * 40), self.artifacts)
        self.assertEqual(self.coordinator.join(), 'waiting')
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_wrong_child_cannot_claim_result(self):
        result = self.waiting()
        with self.assertRaisesRegex(ValueError, 'identity mismatch'):
            self.coordinator.accept('tests', replace(result, child_id='other'), self.artifacts)

    def test_missing_artifact_does_not_settle(self):
        result = self.waiting()
        with self.assertRaises(FileNotFoundError):
            self.coordinator.accept('tests', replace(result, artifact_sha256='0' * 64), self.artifacts)
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_tampered_artifact_does_not_settle(self):
        result = self.waiting()
        (self.artifacts.path / result.artifact_sha256).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'bytes do not match'):
            self.coordinator.accept('tests', result, self.artifacts)

    def test_artifact_path_traversal_rejected(self):
        with self.assertRaisesRegex(ValueError, 'invalid artifact hash'):
            self.artifacts.read('../journal.sqlite')

    def test_artifact_of_other_role_rejected(self):
        result = self.waiting()
        key = self.artifacts.put({'role': 'review', 'commit': CANDIDATE, 'verdict': 'pass'})
        with self.assertRaisesRegex(ValueError, 'artifact contract'):
            self.coordinator.accept('tests', replace(result, artifact_sha256=key), self.artifacts)

    def test_over_budget_result_requires_reconciliation(self):
        result = self.waiting()
        with self.assertRaisesRegex(ValueError, 'budget overrun'):
            self.coordinator.accept('tests', replace(result, used_steps=5), self.artifacts)
        self.assertEqual(self.coordinator.budget()['held'], 4)

    def test_invalid_usage_rejected(self):
        result = self.waiting()
        for value in (-1, True, 2.5):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'schema'):
                self.coordinator.accept('tests', replace(result, used_steps=value), self.artifacts)

    def test_plain_dictionary_is_not_result_schema(self):
        self.waiting()
        with self.assertRaisesRegex(ValueError, 'schema'):
            self.coordinator.accept('tests', {}, self.artifacts)

    def test_unknown_verdict_rejected(self):
        result = self.waiting()
        with self.assertRaisesRegex(ValueError, 'unknown verdict'):
            self.coordinator.accept('tests', replace(result, verdict='seems fine'), self.artifacts)

    def test_result_before_dispatch_reconciliation_deferred(self):
        self.coordinator.plan('tests')
        result = fixture(self.coordinator, 'tests', self.artifacts)
        with self.assertRaisesRegex(ValueError, 'not reconciled'):
            self.coordinator.accept('tests', result, self.artifacts)

    def test_only_one_required_partition_never_completes_join(self):
        result = self.waiting()
        self.coordinator.accept('tests', result, self.artifacts)
        self.assertEqual(self.coordinator.join(), 'waiting')

    def test_repeated_result_not_charged_twice(self):
        result = self.waiting()
        self.coordinator.accept('tests', result, self.artifacts)
        before = self.coordinator.budget()
        self.assertEqual(self.coordinator.accept('tests', result, self.artifacts), 'duplicate')
        self.assertEqual(self.coordinator.budget(), before)

    def test_conflicting_terminal_result_rejected(self):
        result = self.waiting()
        self.coordinator.accept('tests', result, self.artifacts)
        with self.assertRaisesRegex(ValueError, 'conflicting terminal'):
            self.coordinator.accept('tests', replace(result, used_steps=2), self.artifacts)

    def test_negative_verdict_blocks_human_gate(self):
        self.waiting('tests')
        self.waiting('review')
        self.coordinator.accept('tests', fixture(self.coordinator, 'tests', self.artifacts), self.artifacts)
        self.coordinator.accept('review', fixture(self.coordinator, 'review', self.artifacts,
                                                verdict='changes_required'), self.artifacts)
        self.assertEqual(self.coordinator.join(), 'changes_required')

    def test_success_does_not_delete_execution(self):
        for role in ('tests', 'review'):
            result = self.waiting(role)
            self.coordinator.accept(role, result, self.artifacts)
        self.assertEqual(self.coordinator.join(), 'ready_for_human')
        self.assertEqual(self.ax.count(), 2)
        self.assertEqual(self.ax.get(self.coordinator.child('tests')['child_id'])['phase'], 'Running')

    def test_settled_child_does_not_respawn_if_resource_disappears(self):
        result = self.waiting()
        self.coordinator.accept('tests', result, self.artifacts)
        with transaction(self.ax.path) as db:
            db.execute('DELETE FROM tasks')
        self.assertEqual(self.coordinator.dispatch('tests', self.ax), 'settled')
        self.assertEqual(self.ax.count(), 0)

    def test_result_and_usage_survive_reopen(self):
        result = self.waiting()
        self.coordinator.accept('tests', result, self.artifacts)
        reopened = Coordinator(self.base / 'journal.sqlite')
        self.assertEqual(reopened.budget()['spent'], 3)
        self.assertEqual(json.loads(reopened.child('tests')['result'])['commit'], CANDIDATE)

    def test_demo_numbers(self):
        output = demo()
        self.assertEqual(output['after_reservation']['held'], 8)
        self.assertEqual(output['after_stale'], {'join': 'waiting', 'held': 8})
        self.assertEqual(output['final']['tasks'], 2)
        self.assertEqual(output['final']['budget'], {'spent': 5, 'held': 0,
                         'root_reserve': 4, 'available_for_children': 3})


if __name__ == '__main__':
    unittest.main()
