"""Replay prepared MCP message transcripts; no protocol peer or capture runs.

Only this teaching contract is checked, not general MCP/JSON Schema compliance.
Times are virtual. Persistence, authentication and token integrity are not tested.
"""
import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path

PREFIX = 'io.modelcontextprotocol/'
EXTENSION = PREFIX + 'tasks'
VERSION = '2026-07-28'
MAX_FILE = 131072
MAX_MESSAGE = 16384
INTERFACE_SCHEMA = {
    'type': 'object', 'properties': {
        'interface': {'type': 'string', 'enum': ['eth0', 'eth1']}},
    'required': ['interface'],
}
FORMAT_SCHEMA = {
    'type': 'object', 'properties': {
        'format': {'type': 'string', 'enum': ['compact']}},
    'required': ['format'],
}


class TraceError(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise TraceError(reason)


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value, 'duplicate_json_key')
            value[key] = item
        return value

    def constant(_):
        raise TraceError('non_json_constant')

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def load_traces():
    with Path(__file__).with_name('traces.json').open('rb') as stream:
        raw = stream.read(MAX_FILE + 1)
    require(len(raw) <= MAX_FILE, 'fixture_file_limit')
    return strict_json(raw.decode('utf-8'))


@dataclass(frozen=True)
class Policy:
    """Trusted exercise configuration, NOT identity claimed by wire messages."""
    capture_allowed: bool = True
    interfaces: tuple = ('eth1',)
    max_duration_ms: int = 120000
    max_packets: int = 12
    max_bytes: int = 4096
    deadline_ms: int = 130000
    max_exchanges: int = 8
    max_input_rounds: int = 2


def envelope(row, used_ids):
    request, response = row['request'], row['response']
    for packet in (request, response):
        require(type(packet) is dict, 'invalid_packet')
        size = len(json.dumps(packet, allow_nan=False).encode('utf-8'))
        require(size <= MAX_MESSAGE, 'message_limit')
        require(packet.get('jsonrpc') == '2.0', 'jsonrpc_version')
    identifier = request.get('id')
    require(type(identifier) in (int, str), 'invalid_request_id')
    marker = (type(identifier), identifier)
    require(marker not in used_ids, 'reused_request_id')
    used_ids.add(marker)
    require(type(response.get('id')) is type(identifier)
            and response['id'] == identifier, 'response_id_mismatch')
    require(('result' in response) != ('error' in response), 'ambiguous_response')
    params = request.get('params')
    require(type(params) is dict, 'invalid_params')
    meta = params.get('_meta', {})
    require(type(meta) is dict and meta.get(PREFIX + 'protocolVersion') == VERSION,
            'protocol_metadata')
    capabilities = meta.get(PREFIX + 'clientCapabilities')
    require(type(capabilities) is dict, 'capability_metadata')
    if 'error' in response:
        error = response['error']
        require(type(error) is dict and type(error.get('code')) is int
                and type(error.get('message')) is str, 'invalid_rpc_error')
    else:
        require(type(response['result']) is dict, 'invalid_result')
    return request['method'], params, capabilities


def supports_form(capabilities):
    value = capabilities.get('elicitation')
    return type(value) is dict and (value == {} or type(value.get('form')) is dict)


def supports_tasks(capabilities):
    value = capabilities.get('extensions')
    return type(value) is dict and type(value.get(EXTENSION)) is dict


def validate_args(args, policy):
    require(type(args) is dict, 'invalid_arguments')
    expected = {'operation_id', 'device', 'duration_ms', 'max_packets', 'max_bytes'}
    require(set(args) in (expected, expected | {'interface'}), 'argument_keys')
    require(type(args['operation_id']) is str and 0 < len(args['operation_id']) <= 64,
            'operation_identity')
    require(policy.capture_allowed is True, 'capture_not_authorized_by_host_policy')
    require(args['device'] == 'lab-edge-1', 'device_outside_host_scope')
    for key, upper in (('duration_ms', policy.max_duration_ms),
                       ('max_packets', policy.max_packets),
                       ('max_bytes', policy.max_bytes)):
        require(type(args[key]) is int and 0 < args[key] <= upper, 'capture_budget')
    if 'interface' in args:
        require(args['interface'] in policy.interfaces, 'interface_outside_host_scope')


def read_input(result, capabilities, schema, key):
    require(supports_form(capabilities), 'unsolicited_form_capability')
    requests = result.get('inputRequests')
    require(type(requests) is dict and set(requests) == {key}, 'input_request_keys')
    question = requests[key]
    require(type(question) is dict, 'invalid_input_request')
    require(question.get('method') == 'elicitation/create', 'unsupported_input_method')
    params = question.get('params', {})
    require(type(params) is dict, 'invalid_input_params')
    require(params.get('mode') == 'form', 'unsupported_input_mode')
    require(type(params.get('message')) is str and 0 < len(params['message']) <= 256,
            'input_message_limit')
    require(params.get('requestedSchema') == schema, 'unsupported_input_schema')


def read_answer(params, key, field, policy):
    answers = params.get('inputResponses')
    require(type(answers) is dict and set(answers) == {key}, 'input_response_keys')
    answer = answers[key]
    require(type(answer) is dict, 'invalid_input_answer')
    action = answer.get('action')
    require(action in ('accept', 'decline', 'cancel'), 'invalid_input_action')
    if action != 'accept':
        require(set(answer) == {'action'}, 'declined_input_has_content')
        return action, None
    require(set(answer) == {'action', 'content'} and type(answer['content']) is dict
            and set(answer['content']) == {field}, 'invalid_input_content')
    value = answer['content'][field]
    if field == 'interface':
        require(value in policy.interfaces, 'interface_outside_host_scope')
    else:
        require(value == 'compact', 'unsupported_summary_format')
    return action, value


def validate_capture(result, args, interface):
    require(result.get('resultType') == 'complete', 'incomplete_tool_result')
    require(type(result.get('isError')) is bool, 'missing_tool_status')
    if result['isError']:
        require('structuredContent' not in result, 'error_with_fixture_evidence')
        return False
    data = result.get('structuredContent')
    require(type(data) is dict, 'missing_capture_summary')
    require(set(data) == {'operation_id', 'device', 'interface', 'packet_count',
                          'captured_bytes', 'truncated', 'source'}, 'summary_keys')
    require(data['operation_id'] == args['operation_id'] and data['device'] == args['device']
            and data['interface'] == interface, 'summary_object_mismatch')
    require(data['source'] == 'paper-fixture', 'fixture_provenance')
    require(data['truncated'] is False, 'truncated_summary')
    for key, limit in (('packet_count', args['max_packets']),
                       ('captured_bytes', args['max_bytes'])):
        require(type(data[key]) is int and 0 <= data[key] <= limit, 'summary_budget')
    require(type(result.get('content')) is list and len(result['content']) == 1
            and result['content'][0].get('type') == 'text'
            and type(result['content'][0].get('text')) is str, 'summary_content')
    return True


def task_clock(task, at_ms):
    require(type(task.get('taskId')) is str and 0 < len(task['taskId']) <= 128,
            'task_identity')
    try:
        created = datetime.fromisoformat(task['createdAt'].replace('Z', '+00:00'))
        updated = datetime.fromisoformat(task['lastUpdatedAt'].replace('Z', '+00:00'))
        origin = datetime(2026, 10, 18, 10, tzinfo=timezone.utc)
        require(created == origin and updated.tzinfo is not None, 'task_clock')
        elapsed = (updated - created).total_seconds() * 1000
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise TraceError('invalid_task_timestamp') from exc
    require(0 <= elapsed <= at_ms, 'task_timestamp_outside_trace')
    ttl, interval = task.get('ttlMs'), task.get('pollIntervalMs')
    require(type(ttl) is int and at_ms < ttl <= 300000, 'task_expired_or_unbounded')
    require(type(interval) is int and 0 < interval <= 10000, 'poll_interval')
    require('task' not in task and 'requestState' not in task, 'wrong_task_shape')
    return interval


def replay(trace, policy=Policy()):
    """Check these finite transcripts, retaining the client-side learning ledger."""
    rows = trace['exchanges']
    require(2 <= len(rows) <= policy.max_exchanges, 'exchange_budget')
    used, ledger = set(), []
    server_caps = None
    original = None
    state = None
    handle = None
    interface = None
    next_poll = 0
    pending_task_input = False
    task_question = None
    task_input_answered = False
    rounds = 0
    cancelled = False
    outcome = None
    previous_time = 0
    task_status = None
    last_updated = None
    for index, row in enumerate(rows):
        at = row.get('at_ms')
        require(type(at) is int and previous_time <= at <= policy.deadline_ms,
                'trace_deadline_or_time_order')
        previous_time = at
        method, params, caps = envelope(row, used)
        response = row['response']
        result = response.get('result')
        require(outcome is None, 'transcript_continues_after_terminal')
        host_state = 'READY'
        if index == 0:
            require(method == 'server/discover' and result is not None
                    and result.get('resultType') == 'complete'
                    and VERSION in result.get('supportedVersions', []), 'discovery')
            server_caps = result.get('capabilities', {})
            require(type(server_caps) is dict, 'server_capabilities')
            require(result.get('cacheScope') == 'private'
                    and type(result.get('ttlMs')) is int and result['ttlMs'] == 60000,
                    'fixture_discovery_cache_profile')
        elif 'error' in response:
            if handle is not None:
                require(method == 'tasks/get' and params.get('taskId') == handle,
                        'unexpected_task_lookup')
                outcome, host_state = 'unresolved', 'OUTCOME_UNKNOWN'
            else:
                require(method == 'tools/call' and response['error']['code'] == -32021,
                        'unexpected_rpc_error')
                required = response['error'].get('data', {}).get('requiredCapabilities')
                require(required in ({'elicitation': {'form': {}}},
                                     {'extensions': {EXTENSION: {}}}), 'required_capability_shape')
                missing = (not supports_form(caps) if 'elicitation' in required
                           else not supports_tasks(caps))
                require(missing, 'capability_error_without_missing_capability')
                outcome, host_state = 'unsupported', 'BLOCKED_CAPABILITY'
        elif method == 'tools/call' and original is None:
            require(params.get('name') == 'network.start_capture', 'unknown_fixture_tool')
            validate_args(params.get('arguments'), policy)
            require('requestState' not in params and 'inputResponses' not in params,
                    'continuation_without_question')
            original = deepcopy(params)
            interface = params['arguments'].get('interface')
            variant = result.get('resultType')
            if variant == 'input_required':
                read_input(result, caps, INTERFACE_SCHEMA, 'pick_interface')
                require(interface is None, 'unexpected_interface_question')
                state = result.get('requestState')
                require(type(state) is str and 0 < len(state.encode()) <= 256,
                        'opaque_state_limit')
                rounds += 1
                host_state = 'WAITING_INPUT'
            elif variant == 'task':
                require(supports_tasks(caps) and supports_tasks(server_caps),
                        'unnegotiated_task')
                require(interface is not None and result.get('status') == 'working',
                        'task_created_before_arguments_ready')
                next_poll = at + task_clock(result, at)
                handle, task_status = result['taskId'], 'working'
                last_updated = result['lastUpdatedAt']
                host_state = 'WORKING'
            else:
                raise TraceError('unexpected_first_result_variant')
        elif method == 'tools/call' and state is not None:
            require(params.get('name') == original['name']
                    and params.get('arguments') == original['arguments'], 'changed_original_call')
            require(params.get('_meta') == original['_meta'], 'changed_continuation_metadata')
            require(params.get('requestState') == state, 'opaque_state_not_echoed_exactly')
            action, interface = read_answer(params, 'pick_interface', 'interface', policy)
            if result.get('resultType') == 'input_required':
                read_input(result, caps, INTERFACE_SCHEMA, 'pick_interface')
                rounds += 1
                require(action == 'accept', 'repeated_prompt_after_refusal')
                require(type(result.get('requestState')) is str
                        and 0 < len(result['requestState'].encode()) <= 256, 'opaque_state_limit')
                state = result['requestState']
                host_state = 'WAITING_INPUT'
            elif action != 'accept':
                require(result.get('resultType') == 'complete' and result.get('isError') is True
                        and 'structuredContent' not in result, 'capture_after_refusal')
                outcome, host_state = action, 'STOPPED_WITHOUT_CAPTURE'
            else:
                validate_args(original['arguments'], policy)
                accepted = validate_capture(result, original['arguments'], interface)
                outcome = 'fixture_summary' if accepted else 'tool_error'
                host_state = 'ACCEPTED_FIXTURE' if accepted else 'TOOL_ERROR'
            state = None if outcome is not None else state
        elif method in ('tasks/get', 'tasks/update', 'tasks/cancel') and handle is not None:
            require(supports_tasks(caps) and params.get('taskId') == handle, 'task_scope_or_capability')
            require('requestState' not in params, 'mrtr_state_on_task_method')
            require(result.get('resultType') == 'complete', 'task_method_result_variant')
            if method == 'tasks/update':
                require(pending_task_input, 'task_update_without_input')
                action, _ = read_answer(params, 'choose_format', 'format', policy)
                require(action == 'accept', 'fixture_format_not_selected')
                require(result == {'resultType': 'complete'}, 'task_update_ack_shape')
                pending_task_input = False
                task_input_answered = True
                host_state = 'INPUT_SENT_NOT_COMPLETED'
            elif method == 'tasks/cancel':
                require(result == {'resultType': 'complete'}, 'task_cancel_ack_shape')
                cancelled = True
                host_state = 'CANCEL_REQUESTED_NOT_CONFIRMED'
            else:
                require(at >= next_poll, 'poll_too_early')
                interval = task_clock(result, at)
                require(result['taskId'] == handle, 'task_identity_changed')
                require(result['lastUpdatedAt'] >= last_updated, 'task_update_time_reversed')
                last_updated = result['lastUpdatedAt']
                next_poll = at + interval
                task_status = result.get('status')
                require(task_status in ('working', 'input_required', 'completed', 'failed', 'cancelled'),
                        'unknown_task_status')
                host_state = task_status.upper()
                if task_status == 'input_required':
                    read_input(result, caps, FORMAT_SCHEMA, 'choose_format')
                    if task_question is None:
                        task_question = deepcopy(result['inputRequests'])
                        pending_task_input = True
                        rounds += 1
                    else:
                        require(result['inputRequests'] == task_question, 'task_input_key_reused')
                        host_state = ('INPUT_ACK_PENDING_OBSERVATION' if task_input_answered
                                      else 'INPUT_ALREADY_PRESENTED')
                elif task_status == 'completed':
                    require(not pending_task_input, 'task_completed_before_answer')
                    accepted = validate_capture(result.get('result', {}), original['arguments'], interface)
                    outcome = 'fixture_summary' if accepted else 'tool_error'
                elif task_status == 'failed':
                    error = result.get('error', {})
                    require(type(error.get('code')) is int and type(error.get('message')) is str
                            and 'result' not in result, 'task_failed_without_rpc_error')
                    outcome = 'rpc_failure'
                elif task_status == 'cancelled':
                    require('result' not in result, 'cancelled_with_result')
                    outcome = 'task_cancelled'
                else:
                    require('result' not in result and 'error' not in result, 'working_has_final_payload')
        else:
            raise TraceError('unexpected_exchange')
        require(rounds <= policy.max_input_rounds, 'input_round_budget')
        ledger.append({'request_id': row['request']['id'], 'method': method,
                       'at_ms': at, 'host_state': host_state,
                       'operation_id': original['arguments']['operation_id'] if original else None,
                       'opaque_state_retained': state is not None, 'task_id': handle,
                       'task_status': task_status, 'cancel_requested': cancelled,
                       'pending_input_keys': (['pick_interface'] if state is not None else
                                              ['choose_format'] if pending_task_input else [])})
    require(outcome is not None, 'transcript_ends_without_resolution')
    return {'outcome': outcome, 'meaning': 'Prepared transcript checked, not a real capture.',
            'ledger': ledger, 'real_peer_used': False, 'durability_tested': False}


def main(argv=None):
    traces = load_traces()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=sorted(traces))
    parser.add_argument('--messages', action='store_true', help='Print full prepared wire messages too')
    args = parser.parse_args(argv)
    names = [args.case] if args.case else list(traces)
    try:
        output = []
        for name in names:
            item = {'case': name, **replay(traces[name])}
            if args.messages:
                item['prepared_exchanges'] = traces[name]['exchanges']
            output.append(item)
    except (TraceError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'fixture_check': 'FAILED', 'reason': str(exc)}))
        return 2
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
