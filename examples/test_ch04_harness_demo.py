"""Contract checks for the deliberately in-memory, simulated chapter 4 demo."""
import json
import unittest

from ch04_harness_demo import Rejected, State, apply, context, parse, run


def proposal(tool, **args):
    return json.dumps({'tool': tool, 'args': args})


READ = proposal('repo.read_file', path='app.py')
TEST = proposal('tests.run', suite='empty-list')
FINISH = proposal('finish')


class HarnessContractTests(unittest.TestCase):
    def test_normal_run_keeps_simulated_evidence_explicit(self):
        result, state = run([READ, TEST, FINISH])
        self.assertEqual(state.events, ['repo.read_file', 'tests.run', 'result_built'])
        self.assertEqual(state.steps, 3)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['evidence']['tests']['source'], 'simulated')
        self.assertFalse(result['evidence']['tests']['passed'])
        self.assertEqual(result['evidence']['tests']['error'], 'IndexError')
        self.assertEqual(result['evidence']['file']['revision'], result['revision'])
        self.assertIn('безопасность изменения не доказана', result['limits'])

    def test_unavailable_tool_never_produces_evidence(self):
        state = State()
        with self.assertRaises(Rejected):
            apply(state, proposal('github.merge'))
        self.assertEqual(state.evidence, {})
        self.assertEqual(state.events, [])
        self.assertEqual(state.steps, 1)

    def test_out_of_scope_path_never_produces_evidence(self):
        state = State()
        with self.assertRaises(Rejected):
            apply(state, proposal('repo.read_file', path='../../secret'))
        self.assertEqual(state.evidence, {})

    def test_early_finish_is_rejected(self):
        with self.assertRaises(Rejected):
            run([FINISH])

    def test_finish_after_read_alone_is_rejected(self):
        with self.assertRaises(Rejected):
            run([READ, FINISH])

    def test_tests_require_read(self):
        with self.assertRaises(Rejected):
            run([TEST])

    def test_arbitrary_command_is_not_a_suite(self):
        state = State()
        apply(state, READ)
        with self.assertRaises(Rejected):
            apply(state, proposal('tests.run', suite='curl example.test'))
        self.assertNotIn('tests', state.evidence)

    def test_four_attempts_then_limit(self):
        state = State()
        for _ in range(4):
            apply(state, READ)
        with self.assertRaises(Rejected):
            apply(state, READ)
        self.assertEqual(state.steps, 4)
        self.assertEqual(len(state.events), 4)

    def test_bad_json_also_uses_an_attempt(self):
        state = State()
        with self.assertRaises(Rejected):
            apply(state, '{')
        self.assertEqual(state.steps, 1)

    def test_extra_top_level_fields_rejected(self):
        with self.assertRaises(Rejected):
            parse('{"tool":"finish","args":{},"safe":true}')

    def test_wrong_types_and_size_rejected(self):
        for raw in (None, '[]', '{"tool":[],"args":{}}',
                    '{"tool":"finish","args":[]}', 'x' * 4097):
            with self.subTest(raw=str(raw)[:60]), self.assertRaises(Rejected):
                parse(raw)

    def test_model_cannot_supply_final_claim(self):
        with self.assertRaises(Rejected):
            run([READ, TEST, proposal('finish', summary='Everything is safe')])

    def test_reply_exhaustion_is_not_success(self):
        with self.assertRaises(Rejected):
            run([READ, TEST])

    def test_context_shows_remaining_budget(self):
        state = State()
        apply(state, READ)
        request = context(state)
        self.assertEqual(request['steps_left'], 3)
        self.assertNotIn('github.merge', request['tools'])

    def test_each_run_starts_with_empty_state(self):
        run([READ, TEST, FINISH])
        with self.assertRaises(Rejected):
            run([FINISH])


if __name__ == '__main__':
    unittest.main()
