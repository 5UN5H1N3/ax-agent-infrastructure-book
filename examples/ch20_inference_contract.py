"""Two-turn, non-streaming tool contract; offline fixtures by default.

--base-url explicitly enables two HTTP calls containing synthetic messages only.
No files are read, no model is loaded, and no application side effects occur.
This deliberately narrow probe is not a quality evaluation or load test.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from typing import Callable
import urllib.parse
import urllib.request

SNIPPETS = {'billing.py': 'def total(amount):\n    return amount * 1.2\n'}
TOOL = {
    'type': 'function',
    'function': {
        'name': 'read_snippet',
        'description': 'Read a synthetic source snippet by its allowed name.',
        'parameters': {
            'type': 'object',
            'properties': {'path': {'type': 'string', 'enum': ['billing.py']}},
            'required': ['path'],
            'additionalProperties': False,
        },
    },
}


class ContractError(ValueError):
    """The reply does not satisfy this exercise's application contract."""


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ContractError('non-standard JSON constant: ' + value)


def strict_json(value: str):
    try:
        return json.loads(value, object_pairs_hook=unique_object,
                          parse_constant=reject_constant)
    except (ValueError, TypeError) as exc:
        raise ContractError('invalid JSON') from exc


def choice(reply: dict, finish_reason: str) -> dict:
    if not isinstance(reply, dict):
        raise ContractError('response must be an object')
    choices = reply.get('choices')
    if not isinstance(choices, list) or len(choices) != 1:
        raise ContractError('expected exactly one choice')
    item = choices[0]
    if not isinstance(item, dict) or item.get('finish_reason') != finish_reason:
        raise ContractError('unexpected finish_reason')
    message = item.get('message')
    if not isinstance(message, dict) or message.get('role') != 'assistant':
        raise ContractError('expected assistant message')
    return deepcopy(message)


def validate_call(message: dict) -> tuple[str, str]:
    calls = message.get('tool_calls')
    if not isinstance(calls, list) or len(calls) != 1:
        raise ContractError('expected one tool call')
    call = calls[0]
    if not isinstance(call, dict) or call.get('type') != 'function':
        raise ContractError('expected function call')
    call_id = call.get('id')
    if not isinstance(call_id, str) or not call_id.strip():
        raise ContractError('missing tool call id')
    function = call.get('function')
    if not isinstance(function, dict) or function.get('name') != 'read_snippet':
        raise ContractError('unknown tool')
    arguments = function.get('arguments')
    if not isinstance(arguments, str):
        raise ContractError('arguments must be a JSON string')
    args = strict_json(arguments)
    if not isinstance(args, dict) or set(args) != {'path'}:
        raise ContractError('expected only path')
    path = args['path']
    if not isinstance(path, str) or path not in SNIPPETS:
        raise ContractError('path is not allowed')
    return call_id, path


def run_probe(complete: Callable[[dict], dict], model='review-code') -> dict:
    messages = [
        {'role': 'system', 'content': 'Use the provided tool before answering.'},
        {'role': 'user', 'content': 'Read billing.py with read_snippet, then explain total.'},
    ]
    request = {'model': model, 'messages': messages, 'tools': [deepcopy(TOOL)],
               'tool_choice': 'auto', 'parallel_tool_calls': False,
               'stream': False, 'max_tokens': 256}
    assistant = choice(complete(deepcopy(request)), 'tool_calls')
    call_id, path = validate_call(assistant)
    messages.extend([assistant, {'role': 'tool', 'tool_call_id': call_id,
                                'content': SNIPPETS[path]}])
    request['tool_choice'] = 'none'
    final = choice(complete(deepcopy(request)), 'stop')
    if final.get('tool_calls'):
        raise ContractError('unexpected tool call in final response')
    text = final.get('content')
    if not isinstance(text, str) or not text.strip():
        raise ContractError('missing final text')
    return {'tool': 'read_snippet', 'path': path,
            'tool_result_linked': True, 'final_text': text}


def fixtures() -> list[dict]:
    return [
        {'choices': [{'finish_reason': 'tool_calls', 'message': {
            'role': 'assistant', 'content': None, 'tool_calls': [{
                'id': 'call-1', 'type': 'function', 'function': {
                    'name': 'read_snippet', 'arguments': '{"path":"billing.py"}'}}]}}]},
        {'choices': [{'finish_reason': 'stop', 'message': {
            'role': 'assistant', 'content': 'total multiplies amount by 1.2.'}}]},
    ]


def demo() -> dict:
    replies = iter(fixtures())
    return run_probe(lambda request: next(replies))


def http_transport(base_url: str, timeout: float):
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('base URL must be http(s)')
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('credentials, query and fragment are not allowed')
    if parsed.path.rstrip('/') != '/v1':
        raise ValueError('base URL must end with /v1')
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout must be finite and positive')
    # The exercise uses no credentials and never follows redirects.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(NoRedirect())

    def complete(payload):
        request = urllib.request.Request(
            base_url.rstrip('/') + '/chat/completions',
            data=json.dumps(payload).encode(),
            headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ContractError('response exceeds 1 MiB')
        return strict_json(raw.decode('utf-8'))
    return complete


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', help='Opt in to HTTP, e.g. http://127.0.0.1:8080/v1')
    parser.add_argument('--model', default='review-code')
    parser.add_argument('--timeout', type=float, default=30)
    args = parser.parse_args()
    result = (run_probe(http_transport(args.base_url, args.timeout), args.model)
              if args.base_url else demo())
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == '__main__':
    main()
