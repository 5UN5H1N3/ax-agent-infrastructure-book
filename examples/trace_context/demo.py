"""Real OTel spans across one loopback HTTP tool, no model or collector.

Run from the book root in the separate requirements.txt environment:
python -m examples.trace_context.demo --scenario propagated
Scenarios: propagated, dropped, invalid, resumed, all (default).
The mini-controller makes one read-only call using L01's unchanged read_file.
Two explicit providers model two components in ONE process / two threads.
"""
from contextlib import contextmanager
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.metadata import version
import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import uuid

from opentelemetry.context import Context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import Link, SpanKind, Status, StatusCode, get_current_span
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

from examples.ch20_inference_contract import strict_json
from examples.ch32_batching_exercise import BROKEN_SOURCE
from examples.learning_harness.harness import Denied, read_file

PROPAGATOR = TraceContextTextMapPropagator()
TIMEOUT = 2.0
SCENARIOS = ('propagated', 'dropped', 'invalid', 'resumed')


class DemoError(ValueError):
    def __init__(self, message, evidence=None):
        super().__init__(message)
        self.evidence = evidence


def check_profile():
    for package in ('opentelemetry-api', 'opentelemetry-sdk'):
        if version(package) != '1.45.1':
            raise DemoError('Use the separate pinned L04 requirements.txt environment')
    if os.environ.get('OTEL_SDK_DISABLED', '').strip().lower() == 'true':
        raise DemoError('OTEL_SDK_DISABLED disables this experiment; use a clean environment')


def span_record(span):
    """Format actual ReadableSpan fields; never construct trace/span IDs."""
    return json.dumps({
        'name': span.name, 'service': span.resource.attributes['service.name'],
        'trace_id': f'{span.context.trace_id:032x}',
        'span_id': f'{span.context.span_id:016x}',
        'trace_flags': f'{int(span.context.trace_flags):02x}',
        'parent_span_id': f'{span.parent.span_id:016x}' if span.parent else None,
        'remote_parent': bool(span.parent and span.parent.is_remote),
        'kind': span.kind.name, 'status': span.status.status_code.name,
        'attributes': dict(span.attributes),
        'links': [{'trace_id': f'{link.context.trace_id:032x}',
                   'span_id': f'{link.context.span_id:016x}'} for link in span.links],
        'start_time_unix_nano': span.start_time,
        'end_time_unix_nano': span.end_time,
    }, ensure_ascii=False) + '\n'


class Telemetry:
    def __init__(self, service):
        self.output = io.StringIO()
        self.provider = TracerProvider(
            sampler=ALWAYS_ON, resource=Resource({'service.name': service}),
            shutdown_on_exit=False,
            span_limits=SpanLimits(max_attributes=16, max_events=4, max_links=4))
        self.provider.add_span_processor(SimpleSpanProcessor(
            ConsoleSpanExporter(out=self.output, formatter=span_record)))
        self.tracer = self.provider.get_tracer('book.l04', '1')

    def records(self):
        return [json.loads(line) for line in self.output.getvalue().splitlines()]

    def close(self):
        self.provider.shutdown()


def attributes(operation, attempt):
    return {'learning.operation.id': operation, 'learning.attempt.id': attempt}


@contextmanager
def local_tool(root, telemetry):
    """One trusted fixture server, random loopback port, bounded socket waits.

    No user-selected bind address, upstream URL, proxy, redirects or retries.
    This transport is for the local lesson, not an authenticated tool service.
    """
    wire = []

    class Server(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(TIMEOUT)
            return connection, address

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, body):
            raw = json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != '/read_file':
                self.reply(404, {'error': 'unknown route'})
                return
            size = self.headers.get('Content-Length', '')
            if not size.isdecimal() or not 0 < int(size) <= 512:
                self.reply(400, {'error': 'bounded JSON body required'})
                return
            operation = self.headers.get('x-learning-operation-id', '')
            attempt = self.headers.get('x-learning-attempt-id', '')
            if re.fullmatch('[0-9a-f]{32}', operation) is None or attempt not in ('1', '2'):
                self.reply(400, {'error': 'bounded learning IDs required'})
                return
            carrier = {key.lower(): value for key, value in self.headers.items()
                       if key.lower() in ('traceparent', 'tracestate')}
            # Empty base Context prevents an ambient in-process span from hiding
            # a missing HTTP header. The received IDs are correlation, not auth.
            parent = PROPAGATOR.extract(carrier, context=Context())
            wire.append({'received_traceparent': carrier.get('traceparent'),
                         'received_operation_id': operation, 'attempt': int(attempt)})
            with telemetry.tracer.start_as_current_span(
                    'tool.read.server', context=parent, kind=SpanKind.SERVER,
                    attributes=attributes(operation, int(attempt)),
                    record_exception=False, set_status_on_exception=False) as server_span:
                with telemetry.tracer.start_as_current_span(
                        'file.read', attributes=attributes(operation, int(attempt)),
                        record_exception=False, set_status_on_exception=False) as file_span:
                    try:
                        args = self.rfile.read(int(size)).decode('utf-8')
                        result = read_file(root, args)
                    except (Denied, OSError, UnicodeError, ValueError):
                        # Do not export an exception message containing file data.
                        for span in (server_span, file_span):
                            span.set_status(Status(StatusCode.ERROR, 'read denied'))
                        self.reply(403, {'error': 'read denied'})
                        return
                    file_span.set_attribute('learning.file.sha256', result['sha256'])
                    file_span.set_attribute('learning.file.bytes', len(result['text'].encode()))
                self.reply(200, result)

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02),
                              name='l04-local-tool')
    thread.start()
    try:
        yield server.server_address[1], wire
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=TIMEOUT)
        if thread.is_alive():
            raise DemoError('local tool did not stop')


def call_tool(port, tracer, operation, attempt, scenario):
    with tracer.start_as_current_span(
            'tool.read.client', kind=SpanKind.CLIENT,
            attributes=attributes(operation, attempt)) as span:
        carrier = {}
        PROPAGATOR.inject(carrier)
        if scenario == 'dropped':
            carrier.clear()  # The deliberate transport defect.
        elif scenario == 'invalid':
            carrier['traceparent'] = '00-' + '0' * 32 + '-' + '0' * 16 + '-01'
        carrier.update({'x-learning-operation-id': operation,
                        'x-learning-attempt-id': str(attempt),
                        'Content-Type': 'application/json'})
        connection = HTTPConnection('127.0.0.1', port, timeout=TIMEOUT)
        try:
            connection.request('POST', '/read_file', body=b'{"path":"batching.py"}', headers=carrier)
            response = connection.getresponse()
            raw = response.read(16385)
            if response.status != 200 or len(raw) > 16384:
                span.set_status(Status(StatusCode.ERROR, 'tool response rejected'))
                raise DemoError('tool response rejected')
            result = strict_json(raw.decode())
        finally:
            connection.close()
    return result


def analyse(spans, wire, scenario, expected_hash):
    """Check explicit IDs, not timestamps or export ordering."""
    def one(name):
        matches = [span for span in spans if span['name'] == name]
        if len(matches) != 1:
            raise DemoError('unexpected span inventory: ' + name)
        return matches[0]

    client, server, file = (one(name) for name in ('tool.read.client', 'tool.read.server', 'file.read'))
    attempts = sorted((s for s in spans if s['name'] == 'harness.attempt'),
                      key=lambda s: s['attributes']['learning.attempt.id'])
    if (len(attempts) != (2 if scenario == 'resumed' else 1) or len(wire) != 1
            or len(spans) != (5 if scenario == 'resumed' else 4)):
        raise DemoError('incomplete experiment')
    active = attempts[-1]
    checks = {
        'client_parent_is_attempt': (client['trace_id'], client['parent_span_id']) == (active['trace_id'], active['span_id']),
        'server_parent_is_client': server['parent_span_id'] == client['span_id'],
        'server_parent_is_remote': server['remote_parent'],
        'same_client_server_trace': client['trace_id'] == server['trace_id'],
        'file_parent_is_server': (file['trace_id'], file['parent_span_id']) == (server['trace_id'], server['span_id']),
        'active_attempt_ids_match': all(s['attributes']['learning.attempt.id'] == active['attributes']['learning.attempt.id']
                                        for s in (client, server, file)),
        'one_operation_id': len({s['attributes']['learning.operation.id'] for s in spans}) == 1,
        'read_hash_matches_fixture': file['attributes']['learning.file.sha256'] == expected_hash,
    }
    intact = scenario in ('propagated', 'resumed')
    expected = {key: True for key in checks}
    for key in ('server_parent_is_client', 'server_parent_is_remote', 'same_client_server_trace'):
        expected[key] = intact
    if checks != expected or any(s['status'] == 'ERROR' for s in spans):
        raise DemoError('observed graph differs from the intended experiment')
    if (not intact and server['parent_span_id'] is not None
            or len({s['trace_id'] for s in spans}) != (1 if scenario == 'propagated' else 2)):
        raise DemoError('unexpected root or trace inventory')
    if scenario == 'resumed':
        previous = attempts[0]
        if (active['parent_span_id'] is not None or active['trace_id'] == previous['trace_id']
                or active['links'] != [{'trace_id': previous['trace_id'], 'span_id': previous['span_id']}]):
            raise DemoError('resume must be a new root with an explicit previous-attempt link')
    return {'scenario': scenario, 'checks': checks,
            'trace_count': len({s['trace_id'] for s in spans}),
            'meaning': 'deliberate propagation break reproduced' if not intact else 'explicit causal graph verified',
            'application_result': 'synthetic file read; no model or application tests ran'}


def run_scenario(scenario):
    check_profile()
    if scenario not in SCENARIOS:
        raise DemoError('unknown scenario')
    operation = uuid.uuid4().hex
    harness_telemetry, tool_telemetry = Telemetry('learning-harness'), Telemetry('learning-tool')
    expected_hash = hashlib.sha256(BROKEN_SOURCE.encode()).hexdigest()
    try:
        with tempfile.TemporaryDirectory(prefix='ax-trace-') as directory:
            (Path(directory) / 'batching.py').write_text(BROKEN_SOURCE, encoding='utf-8')
            with local_tool(directory, tool_telemetry) as (port, wire):
                links = []
                if scenario == 'resumed':
                    with harness_telemetry.tracer.start_as_current_span(
                            'harness.attempt', context=Context(), attributes=attributes(operation, 1)) as previous:
                        previous.set_attribute('learning.state', 'waiting')
                        saved = {}
                        PROPAGATOR.inject(saved)
                    # In-memory checkpoint variation, not disk durability or AX restore.
                    context = PROPAGATOR.extract(saved, context=Context())
                    links = [Link(get_current_span(context).get_span_context())]
                attempt = 2 if scenario == 'resumed' else 1
                with harness_telemetry.tracer.start_as_current_span(
                        'harness.attempt', context=Context(), links=links,
                        attributes=attributes(operation, attempt)):
                    result = call_tool(port, harness_telemetry.tracer, operation, attempt, scenario)
                if result['sha256'] != expected_hash or result['text'] != BROKEN_SOURCE:
                    raise DemoError('HTTP result differs from the known fixture')
        spans = harness_telemetry.records() + tool_telemetry.records()
        try:
            summary = analyse(spans, wire, scenario, expected_hash)
        except DemoError as error:
            # Preserve evidence when the reader changes the instrumentation.
            error.evidence = {'scenario': scenario, 'graph_check': 'FAILED',
                              'wire': wire, 'spans': spans}
            raise
        return {'summary': summary, 'wire': wire, 'spans': spans,
                'scope': 'Actual SDK and loopback HTTP; one process/two threads; no OTLP or durable resume'}
    finally:
        harness_telemetry.close()
        tool_telemetry.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario', choices=(*SCENARIOS, 'all'), default='all')
    args = parser.parse_args()
    try:
        selected = SCENARIOS if args.scenario == 'all' else (args.scenario,)
        print(json.dumps([run_scenario(s) for s in selected], ensure_ascii=False, indent=2))
    except (DemoError, OSError, ValueError) as error:
        if getattr(error, 'evidence', None) is not None:
            print(json.dumps(error.evidence, ensure_ascii=False, indent=2))
        parser.exit(2, 'Trace experiment stopped: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
