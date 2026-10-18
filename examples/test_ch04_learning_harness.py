"""Controller, real loopback exchange and annotated eval regression tests."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import time
import unittest
from unittest.mock import patch

from examples.ch32_batching_exercise import BROKEN_SOURCE
from examples.learning_harness import harness as h
from examples.learning_harness import evals


class LearningHarnessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'batching.py').write_text(BROKEN_SOURCE, encoding='utf-8')

    def replies(self, *replies, **kwargs):
        sequence = iter(replies)
        return h.run(self.root, lambda request, remaining: next(sequence), **kwargs)

    def test_real_http_loop_and_linked_history(self):
        with h.mock_endpoint() as (url, requests):
            result = h.run(self.root, h.transport(url))
        self.assertEqual(result['status'], 'answered')
        self.assertEqual((result['model_calls'], result['file_reads']), (2, 1))
        self.assertEqual(requests[0]['messages'][-1]['role'], 'user')
        history = requests[1]['messages']
        self.assertEqual([m['role'] for m in history], ['system', 'user', 'assistant', 'tool'])
        self.assertEqual(history[2]['tool_calls'][0]['id'], history[3]['tool_call_id'])
        evidence = json.loads(history[3]['content'])
        self.assertEqual(evidence['text'], BROKEN_SOURCE)
        self.assertEqual(evidence['sha256'], hashlib.sha256(BROKEN_SOURCE.encode()).hexdigest())
        self.assertFalse(evidence['tests_run'])
        self.assertIn(evidence['sha256'], result['answer'])

    def test_path_denial_precedes_file_open(self):
        for path in ('../secret', '/etc/passwd', 'other.py', 'sub/batching.py'):
            with self.subTest(path=path), patch.object(h.os, 'open') as opened:
                result = self.replies(h.tool_reply(path))
                self.assertEqual(result['status'], 'blocked')
                self.assertEqual(result['file_reads'], 0)
                opened.assert_not_called()

    def test_no_unknown_tool(self):
        self.assertEqual(self.replies(h.tool_reply(name='shell'))['status'], 'blocked')

    def test_duplicated_keys_and_extra_arguments(self):
        for arguments in ('{"path":"batching.py","path":"batching.py"}',
                          '{"path":"batching.py","write":true}', '[]', '{'):
            reply = h.tool_reply()
            reply['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = arguments
            with self.subTest(arguments=arguments):
                result = self.replies(reply)
                self.assertIn(result['status'], ('invalid_response', 'blocked'))
                self.assertEqual(result['file_reads'], 0)

    def test_symlink_file_and_root_rejected(self):
        (self.root / 'outside.py').write_text('SECRET', encoding='utf-8')
        (self.root / 'batching.py').unlink()
        (self.root / 'batching.py').symlink_to(self.root / 'outside.py')
        self.assertEqual(self.replies(h.tool_reply())['status'], 'io_error')
        link = self.root / 'root-link'
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(OSError):
            h.read_file(link, '{"path":"batching.py"}')

    def test_special_file_not_opened_as_stream(self):
        (self.root / 'batching.py').unlink()
        os.mkfifo(self.root / 'batching.py')
        self.assertEqual(self.replies(h.tool_reply())['status'], 'blocked')

    def test_file_size_and_encoding(self):
        for content in (b'x' * 4097, b'\xff'):
            (self.root / 'batching.py').write_bytes(content)
            self.assertIn(self.replies(h.tool_reply())['status'], ('blocked', 'invalid_response'))

    def test_model_budget_is_not_extended_for_more_reads(self):
        result = self.replies(h.tool_reply(), calls=1)
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertEqual(result['model_calls'], 1)
        self.assertEqual(result['file_reads'], 1)

    def test_read_budget_and_reused_call_id(self):
        repeated = self.replies(h.tool_reply(), h.tool_reply())
        self.assertEqual(repeated['status'], 'invalid_response')
        exhausted = self.replies(*(h.tool_reply(call_id=f'r-{n}') for n in range(3)))
        self.assertEqual(exhausted['status'], 'budget_exhausted')
        self.assertEqual(exhausted['file_reads'], 2)

    def test_no_final_without_read(self):
        self.assertEqual(self.replies(h.final_reply('Guess'))['status'], 'invalid_response')

    def test_format_acceptance_does_not_certify_truth(self):
        result = self.replies(h.tool_reply(), h.final_reply('I ran all tests: passed'))
        self.assertEqual(result['status'], 'answered')
        self.assertEqual(result['answer'], 'I ran all tests: passed')
        self.assertFalse(result['evidence'][0]['tests_run'])

    def test_bad_finish_multiple_choices_calls_and_empty_final(self):
        bad = []
        reply = h.tool_reply()
        reply['choices'][0]['finish_reason'] = 'length'
        bad.append(reply)
        reply = h.tool_reply()
        reply['choices'] *= 2
        bad.append(reply)
        reply = h.tool_reply()
        reply['choices'][0]['message']['tool_calls'] *= 2
        bad.append(reply)
        for reply in bad:
            with self.subTest(reply=reply):
                self.assertEqual(self.replies(reply)['status'], 'invalid_response')
        for answer in ('', 'x' * 4097):
            self.assertEqual(self.replies(h.tool_reply(), h.final_reply(answer))['status'],
                             'invalid_response')

    def test_oversize_response_and_invalid_json(self):
        self.assertEqual(self.replies(h.final_reply('x' * 17000))['status'], 'invalid_response')
        with h.mock_endpoint(lambda request: {'padding': 'x' * 17000}) as (url, _):
            self.assertEqual(h.run(self.root, h.transport(url))['status'], 'invalid_response')

    def test_http_errors_have_no_retry(self):
        for code in (401, 429, 500, 302):
            with self.subTest(code=code), h.mock_endpoint(http_status=code,
                    headers={'Location': '/v1/chat/completions'} if code == 302 else {}) as (url, requests):
                result = h.run(self.root, h.transport(url))
                self.assertEqual(result['status'], 'transport_error')
                self.assertEqual(result['trace'][-1]['code'], code)
                self.assertEqual(len(requests), 1)

    def test_mock_does_not_explain_arbitrary_new_source(self):
        (self.root / 'batching.py').write_text(evals.SOURCES['fixed'], encoding='utf-8')
        with h.mock_endpoint() as (url, _):
            result = h.run(self.root, h.transport(url))
        self.assertEqual(result['status'], 'answered')
        self.assertIn('нет подготовленного объяснения', result['answer'])
        self.assertNotIn('пропущено', result['answer'])

    def test_deadline_interrupts_wait_and_restores_signal(self):
        original = signal.getsignal(signal.SIGALRM)
        start = time.monotonic()
        with h.mock_endpoint(delay=0.3) as (url, _):
            result = h.run(self.root, h.transport(url), seconds=0.05)
        self.assertEqual(result['status'], 'deadline')
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(signal.getsignal(signal.SIGALRM), original)
        self.assertEqual(signal.getitimer(signal.ITIMER_REAL), (0.0, 0.0))

    def test_endpoint_and_budget_configuration(self):
        for url in ('file:///v1', 'http://user:pass@localhost/v1',
                    'http://localhost/v1?secret=1', 'http://localhost/not-v1'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                h.transport(url)
        for calls in (0, 4, True):
            with self.assertRaises(ValueError):
                h.run(self.root, lambda *_: None, calls=calls)


class PreparedEvalTests(unittest.TestCase):
    def test_all_prepared_transcripts_and_scores(self):
        result = evals.evaluate()
        self.assertEqual(len(result['rows']), 15)
        self.assertEqual({r['case'] for r in result['rows']}, {'C1','C2','C3','C4','C5'})
        expected = {'careful': (30, True), 'cheap': (17, False), 'fluent': (24, False)}
        for candidate, (points, accepted) in expected.items():
            self.assertEqual(result['candidates'][candidate]['points'], points)
            self.assertEqual(result['candidates'][candidate]['accepted_for_next_trial'], accepted)
            self.assertEqual(result['candidates'][candidate]['model_calls'], 10)
        self.assertEqual(result['candidates']['cheap']['unsafe_proposals'], 1)

    def test_oracles_execute_only_authored_fixture_not_model_code(self):
        for variant, source in evals.SOURCES.items():
            namespace = {}
            exec(source, namespace)
            function = namespace['batches']
            self.assertEqual(function([0,1,2,3,4], 2),
                             [[0,1],[2,3],[4]] if variant == 'fixed' else [[0,1],[2,3]])
            self.assertEqual(function([0,1], 3), [[0,1]] if variant == 'fixed' else [])

    def test_annotation_cannot_silently_attach_to_different_replay(self):
        case = json.loads(evals.DATA.read_text())['cases'][0]
        sample = deepcopy(case['samples'][0])
        sample['status'] = 'blocked'
        with self.assertRaisesRegex(ValueError, 'transcript'):
            evals.replay(case, sample)


if __name__ == '__main__':
    unittest.main()
