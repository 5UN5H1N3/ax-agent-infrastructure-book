from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from ch20_inference_contract import (
    ContractError, SNIPPETS, choice, demo, fixtures, http_transport,
    run_probe, strict_json, validate_call,
)


class InferenceContractTests(unittest.TestCase):
    def assistant(self):
        return fixtures()[0]['choices'][0]['message']

    def test_demo(self):
        self.assertEqual(demo(), {'tool': 'read_snippet', 'path': 'billing.py',
                                 'tool_result_linked': True,
                                 'final_text': 'total multiplies amount by 1.2.'})

    def test_messages_and_payload(self):
        replies = iter(fixtures())
        seen = []
        def complete(request):
            seen.append(request)
            return next(replies)
        run_probe(complete, 'candidate-b')
        self.assertEqual(len(seen), 2)
        self.assertEqual(len(seen[0]['messages']), 2)
        self.assertEqual(len(seen[1]['messages']), 4)
        self.assertEqual(seen[0]['tool_choice'], 'auto')
        self.assertEqual(seen[1]['tool_choice'], 'none')
        self.assertFalse(seen[0]['stream'])
        self.assertFalse(seen[0]['parallel_tool_calls'])
        self.assertEqual(seen[0]['model'], 'candidate-b')
        self.assertEqual(seen[1]['messages'][2], self.assistant())
        self.assertEqual(seen[1]['messages'][3], {
            'role': 'tool', 'tool_call_id': 'call-1', 'content': SNIPPETS['billing.py']})

    def test_valid_call(self):
        self.assertEqual(validate_call(self.assistant()), ('call-1', 'billing.py'))

    def test_call_count(self):
        for calls in (None, [], [self.assistant()['tool_calls'][0]] * 2, 'wrong'):
            with self.subTest(calls=calls), self.assertRaises(ContractError):
                validate_call({'tool_calls': calls})

    def test_call_envelope(self):
        for key, value in [('id', ''), ('id', 2), ('type', 'shell'), ('function', None)]:
            message = self.assistant()
            message['tool_calls'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ContractError):
                validate_call(message)

    def test_unknown_tool(self):
        message = self.assistant()
        message['tool_calls'][0]['function']['name'] = 'run_shell'
        with self.assertRaises(ContractError):validate_call(message)

    def test_argument_string_required(self):
        message = self.assistant()
        message['tool_calls'][0]['function']['arguments'] = {'path': 'billing.py'}
        with self.assertRaises(ContractError):validate_call(message)

    def test_argument_validation(self):
        invalid = ['{', 'null', '[]', '{}', '{"path":true}', '{"path":"../secret"}',
                   '{"path":"billing.py","shell":"x"}',
                   '{"path":"billing.py","path":"../secret"}', '{"path":NaN}']
        for value in invalid:
            message = self.assistant()
            message['tool_calls'][0]['function']['arguments'] = value
            with self.subTest(value=value), self.assertRaises(ContractError):
                validate_call(message)

    def test_choice_envelope(self):
        for reply in [None, [], {}, {'choices': []}, {'choices': [None]},
                      {'choices': fixtures()[0]['choices'] * 2}]:
            with self.subTest(reply=reply), self.assertRaises(ContractError):
                choice(reply, 'tool_calls')

    def test_finish_reason(self):
        for reason in ['length', 'stop', None]:
            reply = fixtures()[0]
            reply['choices'][0]['finish_reason'] = reason
            with self.subTest(reason=reason), self.assertRaises(ContractError):
                choice(reply, 'tool_calls')

    def test_assistant_role(self):
        reply = fixtures()[0]
        reply['choices'][0]['message']['role'] = 'user'
        with self.assertRaises(ContractError):choice(reply, 'tool_calls')

    def test_invalid_call_prevents_second_request(self):
        reply = fixtures()[0]
        reply['choices'][0]['message']['tool_calls'][0]['function']['arguments'] = '{}'
        seen = []
        def complete(request):
            seen.append(request)
            return reply
        with self.assertRaises(ContractError):run_probe(complete)
        self.assertEqual(len(seen), 1)

    def test_final_text_required(self):
        for content in ['', ' ', None, 1]:
            replies = fixtures()
            replies[1]['choices'][0]['message']['content'] = content
            stream = iter(replies)
            with self.subTest(content=content), self.assertRaises(ContractError):
                run_probe(lambda request: next(stream))

    def test_final_tools_rejected(self):
        replies = fixtures()
        replies[1]['choices'][0]['message']['tool_calls'] = self.assistant()['tool_calls']
        stream = iter(replies)
        with self.assertRaises(ContractError):run_probe(lambda request: next(stream))

    def test_strict_json(self):
        for text in ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}']:
            with self.subTest(text=text), self.assertRaises(ContractError):strict_json(text)
        self.assertEqual(strict_json('{"x":1}'), {'x': 1})

    def test_response_not_mutated(self):
        replies = fixtures()
        expected = deepcopy(replies)
        stream = iter(replies)
        run_probe(lambda request: next(stream))
        self.assertEqual(replies, expected)

    def test_bad_http_configuration(self):
        urls = ['file:///tmp/x', 'http:///v1', 'https://user:pass@host/v1',
                'http://host/v1?x=y', 'http://host/v1#f', 'http://host/wrong']
        for url in urls:
            with self.subTest(url=url), self.assertRaises(ValueError):http_transport(url, 1)
        for timeout in [0, -1, float('nan'), float('inf')]:
            with self.subTest(timeout=timeout), self.assertRaises(ValueError):
                http_transport('http://127.0.0.1:8080/v1', timeout)

    def test_http_transport_without_network(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self, *args):return False
            def read(self, limit):
                self.limit = limit
                return json.dumps(fixtures()[0]).encode()
        response = Response()
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value = response
            complete = http_transport('http://127.0.0.1:8080/v1/', 3)
            self.assertEqual(complete({'model': 'x'}), fixtures()[0])
            args, kwargs = build.return_value.open.call_args
            self.assertEqual(args[0].full_url, 'http://127.0.0.1:8080/v1/chat/completions')
            self.assertEqual(args[0].method, 'POST')
            self.assertEqual(json.loads(args[0].data), {'model': 'x'})
            self.assertEqual(kwargs['timeout'], 3)
            self.assertEqual(response.limit, 1_048_577)
            redirect_handler = build.call_args.args[0]
            self.assertIsNone(redirect_handler.redirect_request(
                None, None, 302, 'Found', {}, 'http://elsewhere/v1'))

    def test_http_response_size_limit(self):
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'x' * 1_048_577
            with self.assertRaises(ContractError):
                http_transport('http://127.0.0.1:8080/v1', 3)({})

    def test_no_retry(self):
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect = TimeoutError('late')
            with self.assertRaises(TimeoutError):
                http_transport('http://127.0.0.1:8080/v1', 3)({})
            self.assertEqual(build.return_value.open.call_count, 1)


if __name__ == '__main__':
    unittest.main()
