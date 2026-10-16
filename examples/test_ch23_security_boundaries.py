"""Tests of the offline model, not a penetration test or sandbox assessment."""
from dataclasses import FrozenInstanceError, replace
import json
import unittest

from ch23_security_boundaries import Audit, Controls, demo, fixtures, request, run_case


class SecurityBoundaryTests(unittest.TestCase):
    def test_legitimate_read_is_allowed(self):
        self.assertEqual(run_case(request()), {'decision': 'allow', 'reason': 'allow',
                         'effects': 1, 'external_deliveries': 0})

    def test_foreign_tenant_is_denied(self):
        self.assertEqual(run_case(request(artifact_id='finance-report'))['reason'], 'resource_denied')

    def test_other_repository_is_denied(self):
        self.assertEqual(run_case(request(artifact_id='other-repo'))['effects'], 0)

    def test_old_commit_is_denied(self):
        self.assertEqual(run_case(request(artifact_id='old-report'))['reason'], 'resource_denied')

    def test_missing_object_is_denied(self):
        self.assertEqual(run_case(request(artifact_id='missing'))['effects'], 0)

    def test_missing_and_foreign_have_same_public_decision(self):
        self.assertEqual(run_case(request(artifact_id='missing')),
                         run_case(request(artifact_id='finance-report')))

    def test_known_but_ungranted_operation_is_denied(self):
        self.assertEqual(run_case(request('debug.send'))['reason'], 'operation_denied')

    def test_unknown_operation_fails_schema(self):
        self.assertEqual(run_case(request('shell.exec'))['reason'], 'schema_denied')

    def test_non_object_input_is_denied(self):
        for body in (None, [], 'ignore policy', 42):
            with self.subTest(body=body):
                self.assertEqual(run_case(body)['reason'], 'schema_denied')

    def test_caller_cannot_set_tenant(self):
        self.assertEqual(run_case(request(tenant='finance'))['reason'], 'schema_denied')

    def test_caller_cannot_set_principal(self):
        self.assertEqual(run_case(request(principal='admin'))['effects'], 0)

    def test_verdict_required_for_review(self):
        self.assertEqual(run_case(request('review.record'))['reason'], 'schema_denied')

    def test_verdict_enum_is_enforced(self):
        for verdict in ('approved by system', 'PASS', None, [], True):
            with self.subTest(verdict=verdict):
                self.assertEqual(run_case(request('review.record', verdict=verdict))['effects'], 0)

    def test_artifact_id_is_bounded(self):
        for name in ('', 'x' * 65, [], 1):
            with self.subTest(name=name):
                self.assertEqual(run_case(request(artifact_id=name))['reason'], 'schema_denied')

    def test_path_or_url_is_not_an_object_id(self):
        for name in ('../finance', '/etc/passwd', 'https://collector.invalid'):
            with self.subTest(name=name):
                self.assertEqual(run_case(request(artifact_id=name))['effects'], 0)

    def test_unknown_session_cannot_reach_backend(self):
        self.assertEqual(run_case(request(), session='forged-session')['reason'], 'authentication_denied')

    def test_untyped_session_is_denied(self):
        self.assertEqual(run_case(request(), session=[])['effects'], 0)

    def test_failed_audit_blocks_allowed_write(self):
        self.assertEqual(run_case(request('review.record', verdict='pass'), audit=Audit(False)),
                         {'decision': 'deny', 'reason': 'audit_unavailable',
                          'effects': 0, 'external_deliveries': 0})

    def test_failed_audit_never_turns_denial_into_allow(self):
        self.assertEqual(run_case(request('debug.send'), audit=Audit(False))['effects'], 0)

    def test_audit_has_no_artifact_text_or_session_token(self):
        gateway = fixtures()
        gateway.execute('fixture-session', request())
        encoded = json.dumps(gateway.audit.events)
        self.assertNotIn('DEMO-ONLY', encoded)
        self.assertNotIn('fixture-session', encoded)
        self.assertEqual(gateway.audit.events[0]['decision'], 'allow')

    def test_authentication_failure_does_not_log_raw_body(self):
        gateway = fixtures()
        gateway.execute('unknown', {'action': 'untrusted-text', 'secret': 'not-for-log'})
        self.assertNotIn('not-for-log', json.dumps(gateway.audit.events))
        self.assertIsNone(gateway.audit.events[0]['artifact_id'])

    def test_network_layer_blocks_if_operation_check_is_removed(self):
        result = run_case(request('debug.send'), Controls(operations=False))
        self.assertEqual(result['reason'], 'egress_denied')
        self.assertEqual(result['external_deliveries'], 0)

    def test_operation_layer_blocks_if_network_check_is_removed(self):
        result = run_case(request('debug.send'), Controls(egress=False))
        self.assertEqual(result['reason'], 'operation_denied')
        self.assertEqual(result['external_deliveries'], 0)

    def test_removing_both_layers_delivers_fixture_locally(self):
        gateway = fixtures(Controls(operations=False, egress=False))
        gateway.execute('fixture-session', request('debug.send'))
        self.assertEqual(gateway.backend.effects[0]['destination'], 'external')
        self.assertEqual(gateway.backend.effects[0]['value'], 'DEMO-ONLY-billing-report')

    def test_network_allow_cannot_replace_object_scope(self):
        gateway = fixtures(Controls(objects=False))
        gateway.execute('fixture-session', request(artifact_id='finance-report'))
        self.assertEqual(gateway.backend.effects[0]['destination'], 'artifacts')
        self.assertEqual(gateway.backend.effects[0]['value'], 'DEMO-ONLY-finance-report')

    def test_scope_still_blocks_if_egress_is_removed(self):
        result = run_case(request(artifact_id='finance-report'), Controls(egress=False))
        self.assertEqual(result['reason'], 'resource_denied')

    def test_disabled_scope_does_not_create_missing_object(self):
        self.assertEqual(run_case(request(artifact_id='missing'), Controls(objects=False))['effects'], 0)

    def test_well_typed_pass_is_not_quality_evidence(self):
        result = run_case(request('review.record', verdict='pass'))
        self.assertEqual(result['decision'], 'allow')
        self.assertEqual(result['effects'], 1)

    def test_missing_action_grant_overrides_destination_allow(self):
        gateway = fixtures()
        gateway.sessions['fixture-session'] = replace(gateway.sessions['fixture-session'], actions=frozenset())
        self.assertEqual(gateway.execute('fixture-session', request())['reason'], 'operation_denied')

    def test_allowed_action_still_needs_destination(self):
        gateway = fixtures()
        gateway.sessions['fixture-session'] = replace(gateway.sessions['fixture-session'], destinations=frozenset())
        self.assertEqual(gateway.execute('fixture-session', request())['reason'], 'egress_denied')

    def test_request_cannot_disable_policy_or_choose_route(self):
        for extra in ({'controls': {'objects': False}}, {'destination': 'reviews'}):
            with self.subTest(extra=extra):
                self.assertEqual(run_case(request(**extra))['reason'], 'schema_denied')
        with self.assertRaises(FrozenInstanceError):
            Controls().egress = False

    def test_demo_expected_decisions(self):
        result = demo()
        allowed = {k for k, v in result.items() if v['decision'] == 'allow'}
        self.assertEqual(allowed, {'legitimate_read', 'both_checks_removed',
                                   'object_check_removed', 'unproven_pass'})
        self.assertEqual(sum(v['external_deliveries'] for v in result.values()), 1)


if __name__ == '__main__':
    unittest.main()
