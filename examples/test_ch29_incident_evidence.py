from dataclasses import replace
import unittest
from ch29_incident_evidence import (
    Context, Observation, EXPECTED_COMMIT, assess, collect, demo, demo_context,
    fixture, scoped_observations,
)


class IncidentEvidenceTests(unittest.TestCase):
    def test_ready_does_not_prove_command_outcome(self):
        r = demo()['only_ready']
        self.assertEqual(r['hypotheses']['command_nonzero'], 'unknown')
        self.assertEqual(r['next_read'], 'read_child_outcome')

    def test_nonzero_does_not_prove_checkout_failure(self):
        r = demo()['child_exit_2']
        self.assertEqual(r['hypotheses']['command_nonzero'], 'supported')
        self.assertEqual(r['hypotheses']['wrong_checkout'], 'unknown')
        self.assertEqual(r['next_read'], 'read_checkout')

    def test_missing_head_is_explicit_failure(self):
        r = demo()['checkout_missing']
        self.assertEqual(r['hypotheses']['wrong_checkout'], 'supported')
        self.assertEqual(r['next_read'], 'inspect_workspace_setup')

    def test_different_commit_also_fails_contract(self):
        r = assess(fixture(worker_assigned=True, runner_ready=True, child_exit=2,
                           checkout='a' * 40), demo_context())
        self.assertEqual(r['next_read'], 'inspect_workspace_setup')

    def test_none_is_not_no_head(self):
        r = assess(fixture(worker_assigned=True, runner_ready=True, child_exit=2,
                           checkout=None), demo_context())
        self.assertEqual(r['hypotheses']['wrong_checkout'], 'unknown')
        self.assertEqual(r['next_read'], 'read_checkout')

    def test_current_context_all_dimensions_must_match(self):
        target = demo_context()
        for field in ('task', 'actor_uid', 'activation', 'attempt', 'snapshot'):
            other = replace(target, **{field: 'other'})
            r = assess([Observation(other, 150, 'child_exit', 2)], target)
            self.assertEqual(r['ignored_observations'], 1)
            self.assertEqual(r['hypotheses']['command_nonzero'], 'unknown')

    def test_stale_and_future_observations_ignored(self):
        for at in (99, 201):
            r = assess([Observation(demo_context(), at, 'checkout', 'NO_HEAD')], demo_context())
            self.assertEqual(r['ignored_observations'], 1)
            self.assertEqual(r['hypotheses']['wrong_checkout'], 'unknown')

    def test_window_boundaries_inclusive(self):
        obs = [Observation(demo_context(), at, 'runner_ready', True) for at in (100, 200)]
        self.assertEqual(len(scoped_observations(obs, demo_context(), 100, 200)), 2)

    def test_conflicts_never_use_last_value(self):
        obs = fixture(child_exit=2) + fixture(child_exit=0)
        self.assertEqual(assess(obs, demo_context()), assess(list(reversed(obs)), demo_context()))
        self.assertEqual(assess(obs, demo_context())['next_read'], 'reconcile_evidence')

    def test_conflicting_fact_not_claimed(self):
        r = demo()['conflicting_exit']
        self.assertIsNone(r['facts']['child_exit'])
        self.assertEqual(r['hypotheses']['command_nonzero'], 'conflicting')

    def test_repeated_agreeing_value_is_not_conflict(self):
        r = assess(fixture(child_exit=2) + fixture(child_exit=2), demo_context())
        self.assertEqual(r['conflicting'], [])
        self.assertEqual(r['facts']['child_exit'], 2)

    def test_unknown_value_does_not_erase_known_value(self):
        facts, conflicts = collect(fixture(child_exit=2) + fixture(child_exit=None))
        self.assertEqual(facts['child_exit'], 2)
        self.assertEqual(conflicts, [])

    def test_empty_bundle_asks_for_assignment(self):
        self.assertEqual(assess([], demo_context())['next_read'], 'read_assignment')

    def test_no_worker_asks_about_eligibility(self):
        self.assertEqual(demo()['no_worker']['next_read'], 'inspect_eligibility')

    def test_unready_asks_about_setup(self):
        self.assertEqual(demo()['runner_unready']['next_read'], 'inspect_bootstrap')

    def test_assigned_without_runner_observation(self):
        self.assertEqual(assess(fixture(worker_assigned=True), demo_context())['next_read'], 'read_runner')

    def test_wrong_result_is_not_success(self):
        self.assertEqual(demo()['result_invalid']['next_read'], 'inspect_result_contract')

    def test_absent_result_after_exit_zero(self):
        r = assess(fixture(worker_assigned=True, runner_ready=True, child_exit=0,
                           checkout=EXPECTED_COMMIT, result='absent'), demo_context())
        self.assertEqual(r['next_read'], 'inspect_result_contract')

    def test_missing_result_observation(self):
        r = assess(fixture(worker_assigned=True, runner_ready=True, child_exit=0,
                           checkout=EXPECTED_COMMIT), demo_context())
        self.assertEqual(r['next_read'], 'read_result')

    def test_correct_inputs_nonzero_exit(self):
        r = assess(fixture(worker_assigned=True, runner_ready=True, child_exit=2,
                           checkout=EXPECTED_COMMIT), demo_context())
        self.assertEqual(r['next_read'], 'inspect_child_exit')

    def test_passed_checks_still_need_user_delivery(self):
        self.assertEqual(demo()['all_checks_pass']['next_read'], 'verify_user_delivery')

    def test_no_diagnostic_result_claims_root_cause_or_action(self):
        for r in demo().values():
            self.assertFalse(r['actions_executed'])
            self.assertFalse(r['real_system_inspected'])
            self.assertFalse(r['root_cause_proven'])
            self.assertIsNone(r['root_cause'])

    def test_false_differs_from_missing(self):
        self.assertEqual(assess(fixture(worker_assigned=False), demo_context())['hypotheses']['no_worker'], 'supported')
        self.assertEqual(assess([], demo_context())['hypotheses']['no_worker'], 'unknown')

    def test_zero_exit_is_not_missing(self):
        self.assertEqual(assess(fixture(child_exit=0), demo_context())['hypotheses']['command_nonzero'], 'refuted')

    def test_signal_exit_is_nonzero(self):
        self.assertEqual(assess(fixture(child_exit=-1), demo_context())['hypotheses']['command_nonzero'], 'supported')

    def test_invalid_context(self):
        for value in ('', ' ', None, 1):
            with self.subTest(value=value), self.assertRaises(ValueError):
                replace(demo_context(), actor_uid=value)

    def test_invalid_capture_time(self):
        for value in (-1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Observation(demo_context(), value, 'runner_ready', True)

    def test_invalid_observation_values(self):
        cases = [('worker_assigned', 1), ('runner_ready', 'true'), ('child_exit', False),
                 ('child_exit', -2), ('child_exit', 256), ('checkout', 'short'),
                 ('checkout', False), ('result', 'success'), ('result', 0)]
        for name, value in cases:
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                Observation(demo_context(), 150, name, value)

    def test_unknown_name(self):
        with self.assertRaises(ValueError):
            Observation(demo_context(), 150, 'model_wait', True)

    def test_invalid_context_type(self):
        with self.assertRaises(ValueError):
            Observation({}, 150, 'runner_ready', True)
        with self.assertRaises(ValueError):
            assess([], {})

    def test_invalid_bundle_entry(self):
        with self.assertRaises(ValueError):
            assess([{}], demo_context())

    def test_invalid_window(self):
        for kwargs in ({'start_ms': -1}, {'as_of_ms': True}, {'start_ms': 201}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                assess([], demo_context(), **kwargs)

    def test_invalid_expected_commit(self):
        for value in ('main', None, 'A' * 40):
            with self.subTest(value=value), self.assertRaises(ValueError):
                assess([], demo_context(), expected_commit=value)

    def test_input_list_is_not_modified(self):
        obs = fixture(child_exit=2)
        before = list(obs)
        assess(obs, demo_context())
        self.assertEqual(obs, before)


if __name__ == '__main__':
    unittest.main()
