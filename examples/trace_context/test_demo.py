"""Separate SDK tests. Real loopback HTTP, no mocked OTel IDs or spans."""
from copy import deepcopy
from http.client import HTTPConnection
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from opentelemetry.context import Context
from opentelemetry.trace import (NonRecordingSpan, get_current_span,
                                 get_tracer_provider, set_span_in_context)

from examples.trace_context import demo as d


class TraceContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packets = {name: d.run_scenario(name) for name in d.SCENARIOS}

    def span(self, packet, name):
        return next(s for s in packet['spans'] if s['name'] == name)

    def test_real_propagated_graph(self):
        packet = self.packets['propagated']
        self.assertEqual(packet['summary']['trace_count'], 1)
        self.assertEqual(len(packet['spans']), 4)
        self.assertTrue(all(packet['summary']['checks'].values()))

    def test_header_carries_client_span_not_attempt_span(self):
        packet = self.packets['propagated']
        client = self.span(packet, 'tool.read.client')
        self.assertEqual(packet['wire'][0]['received_traceparent'],
                         f"00-{client['trace_id']}-{client['span_id']}-{client['trace_flags']}")

    def test_sampled_flag_is_a_bit_not_the_entire_flags_byte(self):
        client = self.span(self.packets['propagated'], 'tool.read.client')
        self.assertEqual(int(client['trace_flags'], 16) & 0x01, 1)
        self.assertEqual(int(client['trace_flags'], 16) & 0x02, 2)

    def test_remote_parent_and_component_kinds(self):
        packet = self.packets['propagated']
        server = self.span(packet, 'tool.read.server')
        self.assertTrue(server['remote_parent'])
        self.assertEqual(server['kind'], 'SERVER')
        self.assertEqual(self.span(packet, 'tool.read.client')['kind'], 'CLIENT')
        self.assertEqual(self.span(packet, 'file.read')['kind'], 'INTERNAL')

    def test_resources_distinguish_components(self):
        packet = self.packets['propagated']
        self.assertEqual(self.span(packet, 'tool.read.server')['service'], 'learning-tool')
        self.assertEqual(self.span(packet, 'tool.read.client')['service'], 'learning-harness')

    def test_dropped_header_starts_a_new_root_but_keeps_application_id(self):
        packet = self.packets['dropped']
        self.assertIsNone(packet['wire'][0]['received_traceparent'])
        self.assertEqual(packet['summary']['trace_count'], 2)
        self.assertIsNone(self.span(packet, 'tool.read.server')['parent_span_id'])
        checks = packet['summary']['checks']
        self.assertFalse(checks['same_client_server_trace'])
        self.assertFalse(checks['server_parent_is_client'])
        self.assertTrue(checks['one_operation_id'])
        self.assertTrue(checks['read_hash_matches_fixture'])

    def test_invalid_all_zero_traceparent_is_rejected_by_real_propagator(self):
        packet = self.packets['invalid']
        self.assertIn('0' * 32, packet['wire'][0]['received_traceparent'])
        self.assertIsNone(self.span(packet, 'tool.read.server')['parent_span_id'])
        self.assertEqual(packet['summary']['trace_count'], 2)

    def test_resume_has_a_new_root_and_one_link_to_previous_attempt(self):
        packet = self.packets['resumed']
        attempts = sorted((s for s in packet['spans'] if s['name'] == 'harness.attempt'),
                          key=lambda s: s['attributes']['learning.attempt.id'])
        first, second = attempts
        self.assertIsNone(second['parent_span_id'])
        self.assertNotEqual(first['trace_id'], second['trace_id'])
        self.assertEqual(second['links'], [{'trace_id': first['trace_id'], 'span_id': first['span_id']}])
        self.assertEqual(first['attributes']['learning.state'], 'waiting')
        self.assertEqual(packet['summary']['trace_count'], 2)

    def test_operation_and_attempt_id_have_different_scopes(self):
        packet = self.packets['resumed']
        self.assertEqual(len({s['attributes']['learning.operation.id'] for s in packet['spans']}), 1)
        self.assertEqual({s['attributes']['learning.attempt.id'] for s in packet['spans']}, {1, 2})

    def test_file_hash_is_from_the_real_read(self):
        record = self.span(self.packets['propagated'], 'file.read')
        raw = d.BROKEN_SOURCE.encode()
        self.assertEqual(record['attributes']['learning.file.sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(record['attributes']['learning.file.bytes'], len(raw))

    def test_only_selected_metadata_is_exported(self):
        allowed = {'learning.operation.id', 'learning.attempt.id', 'learning.state',
                   'learning.file.sha256', 'learning.file.bytes'}
        for packet in self.packets.values():
            for span in packet['spans']:
                self.assertLessEqual(set(span['attributes']), allowed)
            self.assertNotIn(d.BROKEN_SOURCE, json.dumps(packet))
            self.assertNotIn('Authorization', json.dumps(packet))

    def test_ids_are_nonzero_and_spans_are_distinct(self):
        for packet in self.packets.values():
            ids = [span['span_id'] for span in packet['spans']]
            self.assertEqual(len(ids), len(set(ids)))
            for span in packet['spans']:
                self.assertNotEqual(int(span['trace_id'], 16), 0)
                self.assertNotEqual(int(span['span_id'], 16), 0)

    def test_timestamps_are_actual_sdk_fields(self):
        for span in self.packets['propagated']['spans']:
            self.assertIsInstance(span['start_time_unix_nano'], int)
            self.assertGreaterEqual(span['end_time_unix_nano'], span['start_time_unix_nano'])

    def test_analysis_does_not_infer_causality_from_timestamps(self):
        packet = deepcopy(self.packets['dropped'])
        for span in packet['spans']:
            span['start_time_unix_nano'] = span['end_time_unix_nano'] = 1
        summary = d.analyse(packet['spans'], packet['wire'], 'dropped', hashlib.sha256(d.BROKEN_SOURCE.encode()).hexdigest())
        self.assertFalse(summary['checks']['server_parent_is_client'])

    def test_export_order_does_not_define_the_graph(self):
        packet = deepcopy(self.packets['resumed'])
        summary = d.analyse(list(reversed(packet['spans'])), packet['wire'], 'resumed',
                            hashlib.sha256(d.BROKEN_SOURCE.encode()).hexdigest())
        self.assertEqual(summary, packet['summary'])

    def test_corrupt_parent_cannot_be_hidden_by_same_operation_id(self):
        packet = deepcopy(self.packets['propagated'])
        self.span(packet, 'tool.read.server')['parent_span_id'] = '0' * 16
        with self.assertRaises(d.DemoError):
            d.analyse(packet['spans'], packet['wire'], 'propagated', hashlib.sha256(d.BROKEN_SOURCE.encode()).hexdigest())

    def test_loopback_call_ignores_proxy_environment(self):
        with patch.dict(os.environ, {'HTTP_PROXY': 'http://127.0.0.1:1', 'http_proxy': 'http://127.0.0.1:1'}):
            self.assertEqual(d.run_scenario('propagated')['summary']['trace_count'], 1)

    def test_resource_environment_cannot_add_secret_fields(self):
        with patch.dict(os.environ, {'OTEL_RESOURCE_ATTRIBUTES': 'secret=must-not-export'}):
            packet = d.run_scenario('propagated')
        self.assertNotIn('must-not-export', json.dumps(packet))

    def test_disabled_sdk_is_reported_before_network(self):
        with patch.dict(os.environ, {'OTEL_SDK_DISABLED': 'true'}), patch.object(d, 'local_tool') as server:
            with self.assertRaises(d.DemoError):
                d.run_scenario('propagated')
            server.assert_not_called()

    def test_unexpected_sdk_version_is_rejected(self):
        with patch.object(d, 'version', return_value='0.0'):
            with self.assertRaises(d.DemoError):
                d.check_profile()

    def test_no_global_provider_replacement_or_lingering_server(self):
        previous = get_tracer_provider()
        d.run_scenario('propagated')
        self.assertIs(get_tracer_provider(), previous)
        self.assertFalse(any(t.name == 'l04-local-tool' for t in threading.enumerate()))

    def test_explicit_empty_root_does_not_inherit_ambient_span(self):
        outer = d.Telemetry('outer-test')
        try:
            with outer.tracer.start_as_current_span('ambient') as span:
                packet = d.run_scenario('propagated')
                old_trace = f'{span.get_span_context().trace_id:032x}'
            self.assertNotEqual(self.span(packet, 'harness.attempt')['trace_id'], old_trace)
            self.assertIsNone(self.span(packet, 'harness.attempt')['parent_span_id'])
        finally:
            outer.close()

    def test_unknown_scenario_is_rejected(self):
        with self.assertRaises(d.DemoError):
            d.run_scenario('anything')

    def test_failed_graph_check_retains_actual_diagnostic_evidence(self):
        inject = d.PROPAGATOR.inject

        def inject_attempt(carrier):
            parent = get_current_span().parent
            context = set_span_in_context(NonRecordingSpan(parent), Context())
            inject(carrier, context=context)

        with patch.object(d.PROPAGATOR, 'inject', side_effect=inject_attempt):
            with self.assertRaises(d.DemoError) as caught:
                d.run_scenario('propagated')
        packet = caught.exception.evidence
        self.assertEqual(packet['graph_check'], 'FAILED')
        self.assertEqual(len(packet['spans']), 4)
        self.assertEqual(len(packet['wire']), 1)
        attempt = self.span(packet, 'harness.attempt')
        server = self.span(packet, 'tool.read.server')
        self.assertEqual(server['trace_id'], attempt['trace_id'])
        self.assertEqual(server['parent_span_id'], attempt['span_id'])
        self.assertNotEqual(server['parent_span_id'], self.span(packet, 'tool.read.client')['span_id'])

    def test_cli_prints_actual_spans_and_labels_intentional_break(self):
        root = Path(__file__).resolve().parents[2]
        result = subprocess.run([sys.executable, '-m', 'examples.trace_context.demo', '--scenario', 'dropped'],
                                cwd=root, text=True, capture_output=True, check=True,
                                env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
        packet = json.loads(result.stdout)[0]
        self.assertEqual(len(packet['spans']), 4)
        self.assertIn('deliberate propagation break', packet['summary']['meaning'])


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / 'batching.py').write_text(d.BROKEN_SOURCE)
        self.telemetry = d.Telemetry('boundary-test')
        self.addCleanup(self.telemetry.close)

    def request(self, port, body=b'{"path":"batching.py"}', **overrides):
        headers = {'x-learning-operation-id': 'a' * 32, 'x-learning-attempt-id': '1'}
        headers.update(overrides)
        connection = HTTPConnection('127.0.0.1', port, timeout=d.TIMEOUT)
        try:
            connection.request('POST', '/read_file', body=body, headers=headers)
            response = connection.getresponse()
            return response.status, json.loads(response.read(16384))
        finally:
            connection.close()

    def test_unknown_path_is_denied_and_spans_marked_error(self):
        with d.local_tool(self.root, self.telemetry) as (port, _):
            status, result = self.request(port, b'{"path":"../secret"}')
        self.assertEqual(status, 403)
        self.assertEqual(result, {'error': 'read denied'})
        self.assertTrue(all(s['status'] == 'ERROR' for s in self.telemetry.records()))

    def test_file_symlink_is_denied(self):
        path = self.root / 'batching.py'
        path.unlink()
        path.symlink_to(self.root / 'missing')
        with d.local_tool(self.root, self.telemetry) as (port, _):
            self.assertEqual(self.request(port)[0], 403)

    def test_large_body_is_rejected_before_trace_and_file_read(self):
        with d.local_tool(self.root, self.telemetry) as (port, wire):
            self.assertEqual(self.request(port, b'x' * 513)[0], 400)
        self.assertEqual(wire, [])
        self.assertEqual(self.telemetry.records(), [])

    def test_bad_application_id_is_rejected_without_trace(self):
        with d.local_tool(self.root, self.telemetry) as (port, _):
            self.assertEqual(self.request(port, **{'x-learning-operation-id': 'bad'})[0], 400)
        self.assertEqual(self.telemetry.records(), [])

    def test_duplicate_json_key_is_rejected(self):
        with d.local_tool(self.root, self.telemetry) as (port, _):
            self.assertEqual(self.request(port, b'{"path":"batching.py","path":"batching.py"}')[0], 403)


if __name__ == '__main__':
    unittest.main()
