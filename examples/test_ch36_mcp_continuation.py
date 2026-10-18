"""Prepared continuation transcripts, not a real MCP server integration test."""
from copy import deepcopy
from dataclasses import replace
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from examples.mcp_continuation import trace as demo
from examples import ch36_mcp_stdio as old

ROOT = Path(__file__).resolve().parents[1]


class ContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces = demo.load_traces()

    def case(self, name='mrtr'):
        return deepcopy(self.traces[name])

    def changed(self, path, value, reason, case='mrtr'):
        trace = self.case(case)
        target = trace
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        with self.assertRaisesRegex(demo.TraceError, reason):
            demo.replay(trace)

    def test_all_prepared_scenarios_have_explicit_outcomes(self):
        expected = dict(mrtr='fixture_summary', decline='decline', cancel='cancel',
                        unsupported_form='unsupported', unsupported_tasks='unsupported',
                        tasks='fixture_summary', task_input='fixture_summary',
                        cancel_race='fixture_summary')
        self.assertEqual(set(self.traces), set(expected))
        for name, outcome in expected.items():
            with self.subTest(name=name):
                result = demo.replay(self.case(name))
                self.assertEqual(result['outcome'], outcome)
                self.assertFalse(result['real_peer_used'])
                self.assertFalse(result['durability_tested'])

    def test_mrtr_retains_state_then_uses_new_rpc_id_without_task(self):
        ledger = demo.replay(self.case())['ledger']
        self.assertEqual([r['request_id'] for r in ledger], [40, 41, 42])
        self.assertTrue(ledger[1]['opaque_state_retained'])
        self.assertFalse(ledger[2]['opaque_state_retained'])
        self.assertEqual(ledger[1]['operation_id'], ledger[2]['operation_id'])
        self.assertTrue(all(r['task_id'] is None for r in ledger))

    def test_full_discovery_has_an_explicit_cache_scope(self):
        trace = self.case()
        del trace['exchanges'][0]['response']['result']['cacheScope']
        with self.assertRaisesRegex(demo.TraceError, 'fixture_discovery_cache_profile'):
            demo.replay(trace)

    def test_discovery_cache_ttl_is_not_task_lifetime(self):
        self.changed(('exchanges', 0, 'response', 'result', 'ttlMs'),
                     300000, 'fixture_discovery_cache_profile')

    def test_reused_rpc_id_is_rejected(self):
        trace = self.case()
        trace['exchanges'][2]['request']['id'] = 41
        trace['exchanges'][2]['response']['id'] = 41
        with self.assertRaisesRegex(demo.TraceError, 'reused_request_id'):
            demo.replay(trace)

    def test_wrong_response_id_is_rejected(self):
        self.changed(('exchanges', 2, 'response', 'id'), 99, 'response_id_mismatch')

    def test_boolean_request_id_is_not_an_integer_id(self):
        self.changed(('exchanges', 1, 'request', 'id'), True, 'invalid_request_id')

    def test_opaque_state_cannot_be_changed(self):
        self.changed(('exchanges', 2, 'request', 'params', 'requestState'),
                     'fixture-state-02', 'opaque_state_not_echoed_exactly')

    def test_opaque_state_cannot_be_dropped(self):
        trace = self.case()
        del trace['exchanges'][2]['request']['params']['requestState']
        with self.assertRaisesRegex(demo.TraceError, 'opaque_state_not_echoed_exactly'):
            demo.replay(trace)

    def test_continuation_cannot_change_the_original_arguments(self):
        self.changed(('exchanges', 2, 'request', 'params', 'arguments', 'duration_ms'),
                     1001, 'changed_original_call')

    def test_input_response_key_is_not_the_rpc_id(self):
        answer = {'41': {'action': 'accept', 'content': {'interface': 'eth1'}}}
        self.changed(('exchanges', 2, 'request', 'params', 'inputResponses'),
                     answer, 'input_response_keys')

    def test_valid_form_choice_can_still_be_outside_host_policy(self):
        self.changed(('exchanges', 2, 'request', 'params', 'inputResponses',
                      'pick_interface', 'content', 'interface'), 'eth0',
                     'interface_outside_host_scope')

    def test_form_submission_does_not_grant_capture_permission(self):
        with self.assertRaisesRegex(demo.TraceError, 'capture_not_authorized_by_host_policy'):
            demo.replay(self.case(), replace(demo.Policy(), capture_allowed=False))

    def test_client_info_admin_cannot_override_policy(self):
        trace = self.case()
        for row in trace['exchanges']:
            row['request']['params']['_meta'][demo.PREFIX+'clientInfo']['name'] = 'admin'
        with self.assertRaisesRegex(demo.TraceError, 'capture_not_authorized_by_host_policy'):
            demo.replay(trace, replace(demo.Policy(), capture_allowed=False))

    def test_unknown_schema_is_not_rendered_as_a_known_form(self):
        self.changed(('exchanges', 1, 'response', 'result', 'inputRequests',
                      'pick_interface', 'params', 'requestedSchema', 'properties', 'interface'),
                     {'type': 'string'}, 'unsupported_input_schema')

    def test_sampling_request_is_not_silently_serviced(self):
        self.changed(('exchanges', 1, 'response', 'result', 'inputRequests',
                      'pick_interface', 'method'), 'sampling/createMessage', 'unsupported_input_method')

    def test_url_mode_is_not_supported_by_this_fixture(self):
        self.changed(('exchanges', 1, 'response', 'result', 'inputRequests',
                      'pick_interface', 'params', 'mode'), 'url', 'unsupported_input_mode')

    def test_server_must_not_send_form_to_non_declaring_client(self):
        self.changed(('exchanges', 1, 'request', 'params', '_meta',
                      demo.PREFIX+'clientCapabilities'), {}, 'unsolicited_form_capability')

    def test_each_request_needs_its_own_metadata(self):
        self.changed(('exchanges', 2, 'request', 'params', '_meta'), {}, 'protocol_metadata')

    def test_decline_and_form_cancel_do_not_start_capture(self):
        for name in ('decline', 'cancel'):
            result = demo.replay(self.case(name))
            self.assertEqual(result['ledger'][-1]['host_state'], 'STOPPED_WITHOUT_CAPTURE')
            self.assertFalse(result['ledger'][-1]['cancel_requested'])

    def test_capture_after_decline_is_rejected(self):
        self.changed(('exchanges', 2, 'response'), self.case()['exchanges'][2]['response'],
                     'capture_after_refusal', case='decline')

    def test_summary_of_another_interface_is_rejected(self):
        self.changed(('exchanges', 2, 'response', 'result', 'structuredContent', 'interface'),
                     'eth0', 'summary_object_mismatch')

    def test_summary_above_output_budget_is_rejected(self):
        self.changed(('exchanges', 2, 'response', 'result', 'structuredContent', 'captured_bytes'),
                     4097, 'summary_budget')

    def test_boolean_packet_count_is_rejected(self):
        self.changed(('exchanges', 2, 'response', 'result', 'structuredContent', 'packet_count'),
                     True, 'summary_budget')

    def test_virtual_deadline_is_not_reset_on_retry(self):
        self.changed(('exchanges', 2, 'at_ms'), 130001, 'trace_deadline')

    def test_exchange_budget_is_checked_before_replay(self):
        with self.assertRaisesRegex(demo.TraceError, 'exchange_budget'):
            demo.replay(self.case(), replace(demo.Policy(), max_exchanges=2))

    def test_input_round_budget_is_checked(self):
        with self.assertRaisesRegex(demo.TraceError, 'input_round_budget'):
            demo.replay(self.case(), replace(demo.Policy(), max_input_rounds=0))

    def test_message_limit_is_enforced(self):
        self.changed(('exchanges', 1, 'response', 'result', 'requestState'),
                     'x'*17000, 'message_limit')

    def test_small_opaque_state_limit_is_also_enforced(self):
        self.changed(('exchanges', 1, 'response', 'result', 'requestState'),
                     'x'*257, 'opaque_state_limit')

    def test_tasks_require_both_sides_to_declare_the_extension(self):
        self.changed(('exchanges', 0, 'response', 'result', 'capabilities'),
                     {'tools': {}}, 'unnegotiated_task', case='tasks')

    def test_task_result_fields_are_flat_not_nested(self):
        value = self.case('tasks')['exchanges'][1]['response']['result']
        self.changed(('exchanges', 1, 'response', 'result'),
                     {'resultType': 'task', 'task': value},
                     'task_created_before_arguments_ready', case='tasks')

    def test_working_get_is_complete_rpc_but_not_a_capture_result(self):
        ledger = demo.replay(self.case('tasks'))['ledger']
        self.assertEqual(ledger[2]['host_state'], 'WORKING')
        self.assertEqual(ledger[-1]['host_state'], 'COMPLETED')
        self.assertEqual(ledger[1]['task_id'], ledger[-1]['task_id'])

    def test_poll_interval_can_change_and_must_be_respected(self):
        self.changed(('exchanges', 3, 'at_ms'), 5999, 'poll_too_early', case='tasks')

    def test_task_identity_cannot_change_in_reply(self):
        self.changed(('exchanges', 2, 'response', 'result', 'taskId'),
                     'another-task', 'task_identity_changed', case='tasks')

    def test_task_id_cannot_be_used_at_another_lookup(self):
        self.changed(('exchanges', 2, 'request', 'params', 'taskId'),
                     'another-task', 'task_scope_or_capability', case='tasks')

    def test_expired_handle_is_not_reported_as_success(self):
        self.changed(('exchanges', 3, 'response', 'result', 'ttlMs'),
                     6000, 'task_expired_or_unbounded', case='tasks')

    def test_unavailable_task_lookup_retains_unknown_outcome_and_handle(self):
        trace = self.case('tasks')
        trace['exchanges'] = trace['exchanges'][:3]
        trace['exchanges'][2]['response'] = {
            'jsonrpc': '2.0', 'id': 52,
            'error': {'code': -32602, 'message': 'Task is unavailable'}}
        result = demo.replay(trace)
        self.assertEqual(result['outcome'], 'unresolved')
        self.assertEqual(result['ledger'][-1]['task_id'], 'task-fixture-01')

    def test_task_update_ack_is_not_completion(self):
        ledger = demo.replay(self.case('task_input'))['ledger']
        self.assertEqual(ledger[-2]['host_state'], 'INPUT_SENT_NOT_COMPLETED')
        self.assertEqual(ledger[-2]['task_status'], 'input_required')
        self.assertEqual(ledger[-1]['task_status'], 'completed')

    def test_task_update_does_not_echo_mrtr_request_state(self):
        self.changed(('exchanges', 4, 'request', 'params', 'requestState'),
                     'fixture-state-01', 'mrtr_state_on_task_method', case='task_input')

    def test_repeated_pending_task_input_is_not_presented_twice(self):
        trace = self.case('task_input')
        repeated = deepcopy(trace['exchanges'][3])
        repeated['at_ms'] = 11000
        repeated['request']['id'] = repeated['response']['id'] = 60
        trace['exchanges'].insert(4, repeated)
        trace['exchanges'][5]['at_ms'] = 11000
        trace['exchanges'][6]['at_ms'] = 16000
        result = demo.replay(trace)
        self.assertEqual(result['ledger'][4]['host_state'], 'INPUT_ALREADY_PRESENTED')

    def test_stale_input_after_ack_is_not_reanswered(self):
        trace = self.case('task_input')
        repeated = deepcopy(trace['exchanges'][3])
        repeated['at_ms'] = 11000
        repeated['request']['id'] = repeated['response']['id'] = 60
        trace['exchanges'].insert(5, repeated)
        trace['exchanges'][6]['at_ms'] = 16000
        result = demo.replay(trace)
        self.assertEqual(result['ledger'][5]['host_state'], 'INPUT_ACK_PENDING_OBSERVATION')

    def test_task_input_is_matched_by_its_own_key(self):
        self.changed(('exchanges', 4, 'request', 'params', 'inputResponses'),
                     {'pick_interface': {'action': 'accept', 'content': {'format': 'compact'}}},
                     'input_response_keys', case='task_input')

    def test_cancel_ack_does_not_guarantee_task_cancelled(self):
        result = demo.replay(self.case('cancel_race'))
        self.assertTrue(result['ledger'][-2]['cancel_requested'])
        self.assertEqual(result['ledger'][-2]['task_status'], 'working')
        self.assertEqual(result['outcome'], 'fixture_summary')

    def test_completed_tool_error_is_not_a_failed_task(self):
        trace = self.case('tasks')
        trace['exchanges'][-1]['response']['result']['result'] = {
            'resultType': 'complete', 'isError': True,
            'content': [{'type': 'text', 'text': 'Fixture tool error'}]}
        result = demo.replay(trace)
        self.assertEqual(result['outcome'], 'tool_error')
        self.assertEqual(result['ledger'][-1]['task_status'], 'completed')

    def test_failed_task_carries_rpc_error(self):
        trace = self.case('tasks')
        result = trace['exchanges'][-1]['response']['result']
        result['status'] = 'failed'
        del result['result']
        result['error'] = {'code': -32603, 'message': 'Fixture failure'}
        self.assertEqual(demo.replay(trace)['outcome'], 'rpc_failure')

    def test_old_stdio_client_still_rejects_new_result_variants(self):
        for name in ('mrtr', 'tasks'):
            row = self.case(name)['exchanges'][1]
            with self.assertRaisesRegex(ValueError, 'unsupported_result_type'):
                old.validate_response(row['response'], row['request']['id'])

    def test_duplicate_json_keys_and_nan_are_rejected(self):
        for raw in ('{"id":1,"id":2}', '{"value":NaN}'):
            with self.assertRaises(demo.TraceError):
                demo.strict_json(raw)

    def test_cli_prints_prepared_messages_without_starting_a_peer(self):
        result = subprocess.run([sys.executable, '-m', 'examples.mcp_continuation.trace',
                                 '--case', 'mrtr', '--messages'], cwd=ROOT,
                                capture_output=True, text=True, timeout=3, check=True)
        data = json.loads(result.stdout)[0]
        self.assertFalse(data['real_peer_used'])
        self.assertEqual(len(data['prepared_exchanges']), 3)

    def test_cli_failure_is_nonzero_and_explicit(self):
        with patch.object(demo, 'load_traces', return_value={'mrtr': {'exchanges': []}}):
            with patch('sys.stdout', new_callable=io.StringIO) as output:
                self.assertEqual(demo.main(['--case', 'mrtr']), 2)
                self.assertEqual(json.loads(output.getvalue())['fixture_check'], 'FAILED')


class PrintedPackets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = None
        self.packets = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.active = dict(attrs).get('data-mcp-message')
            if self.active:
                if self.active in self.packets:
                    raise ValueError('Duplicate printed packet')
                self.packets[self.active] = ''

    def handle_data(self, text):
        if self.active:
            self.packets[self.active] += text

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = None


class BookTranscriptTests(unittest.TestCase):
    def test_printed_packets_are_the_same_as_checked_transcripts(self):
        parser = PrintedPackets()
        parser.feed((ROOT/'book/AX_Agent_Infrastructure_Book_RU.html').read_text())
        self.assertGreaterEqual(len(parser.packets), 14)
        traces = demo.load_traces()
        for label, text in parser.packets.items():
            name, index, side = label.split('.')
            with self.subTest(packet=label):
                self.assertEqual(json.loads(text), traces[name]['exchanges'][int(index)][side])


if __name__ == '__main__':
    unittest.main()
