"""POSIX teaching fixture: local HTTP probes, owned subprocesses, shutdown.

Not the Go AX runner; not PID 1, a namespace, a snapshot or a deployment test.
Only binds 127.0.0.1 on an ephemeral port. Signals target this fixture's own
freshly spawned session/process group, never a caller-supplied PID.
Checkpoint files live in demo's TemporaryDirectory. Grace values here are
test settings, not AX's fixed 10 seconds. Children self-expire after 8 seconds.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
from tempfile import TemporaryDirectory
from threading import Thread
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, build_opener


def classify(health: int, ready: int, child_running: bool, exit_code: int | None) -> str:
    """Operator interpretation of observations, NOT an AX status field."""
    if health != 200:
        return 'RUNNER_UNREACHABLE'
    if exit_code is not None:
        return 'VERIFY_RESULT' if exit_code == 0 else 'COMMAND_FAILED'
    if not child_running:
        return 'NO_RUNNING_COMMAND'
    if ready != 200:
        return 'ENVIRONMENT_NOT_READY'
    return 'RUNNING_RESULT_UNKNOWN'


class LocalRunner:
    def __init__(self, workspace_ready: bool):
        if os.name != 'posix':
            raise RuntimeError('this teaching fixture requires POSIX')
        self.workspace_ready = workspace_ready
        self.child = None
        self.sigkill_sent = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == '/healthz':
                    code = 200
                elif self.path == '/readyz':
                    code = 200 if owner.workspace_ready else 503
                else:
                    code = 404
                self.send_response(code)
                self.end_headers()
                self.wfile.write(b'ok\n' if code == 200 else b'not ready\n')

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = Thread(target=lambda: self.server.serve_forever(poll_interval=.01), daemon=True)
        self.thread.start()

    def launch(self, mode: str, checkpoint: Path):
        if mode not in {'report', 'exit-two', 'exit-zero', 'cooperative', 'stubborn'}:
            raise ValueError('unsupported fixture child mode')
        if self.child is not None:
            raise RuntimeError('fixture supports one launch only')
        self.child = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), '--child', mode, str(checkpoint)],
            cwd=checkpoint.parent,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        # Handshake: signal handlers are installed before the child reports ready.
        with selectors.DefaultSelector() as selector:
            selector.register(self.child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=3):
                raise TimeoutError('fixture child startup timed out')
            if self.child.stdout.readline() != b'READY\n':
                raise RuntimeError('fixture child failed startup')

    def probe(self, path: str) -> int:
        if path not in {'/healthz', '/readyz'}:
            raise ValueError('fixture only probes its own health and readiness')
        url = f'http://127.0.0.1:{self.server.server_port}{path}'
        try:
            with build_opener(ProxyHandler({})).open(url, timeout=2) as response:
                return response.status
        except HTTPError as error:
            error.close()
            return error.code

    def observation(self) -> dict:
        exit_code = self.child.poll() if self.child is not None else None
        running = self.child is not None and exit_code is None
        health, ready = self.probe('/healthz'), self.probe('/readyz')
        return {'health': health, 'ready': ready, 'child_running': running,
                'decision': classify(health, ready, running, exit_code)}

    def _signal_owned_group(self, sig):
        pid = self.child.pid
        try:
            pgid = os.getpgid(pid)
            if pid <= 1 or pgid != pid or pgid == os.getpgrp():
                raise RuntimeError('refusing to signal an unowned process group')
            os.killpg(pgid, sig)
            return True
        except ProcessLookupError:
            return False

    def stop(self, grace: float = 1.0):
        if not 0 < grace <= 3:
            raise ValueError('fixture grace must be between 0 and 3 seconds')
        if self.child is None:
            return None
        if self.child.poll() is None:
            self._signal_owned_group(signal.SIGTERM)
            try:
                self.child.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                self.sigkill_sent = self._signal_owned_group(signal.SIGKILL)
                # Bounded cleanup in this FIXTURE; upstream Go waits after kill.
                self.child.wait(timeout=3)
        return self.child.returncode

    def close(self):
        try:
            self.stop(grace=.05)
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            if self.child is not None:
                self.child.stdout.close()
                self.child.stderr.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def build_report(workspace: Path) -> dict:
    lines = (workspace / 'service.txt').read_text(encoding='utf-8').splitlines()
    findings = [f'{number}: {line.strip()}'
                for number, line in enumerate(lines, 1) if 'TODO:' in line]
    (workspace / 'report.txt').write_text('\n'.join(findings) + '\n', encoding='utf-8')
    result = {'stage': 'completed', 'report': 'report.txt', 'items': len(findings)}
    temporary = workspace / 'result.tmp'
    temporary.write_text(json.dumps(result), encoding='utf-8')
    os.replace(temporary, workspace / 'result.json')
    return result


def child_main(mode: str, checkpoint: Path):
    stopped = False

    def on_term(*args):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, signal.SIG_IGN if mode == 'stubborn' else on_term)
    print('READY', flush=True)
    if mode == 'report':
        build_report(Path.cwd())
        return 0
    if mode in {'exit-two', 'exit-zero'}:
        return 2 if mode == 'exit-two' else 0
    expires = time.monotonic() + 8
    while not stopped and time.monotonic() < expires:
        time.sleep(.01)
    if not stopped:
        return 5
    temporary = checkpoint.with_suffix('.tmp')
    temporary.write_text(json.dumps({'logical_step': 'paused', 'business_complete': False}), encoding='utf-8')
    os.replace(temporary, checkpoint)
    return 0


def failure_demo() -> dict:
    with TemporaryDirectory(prefix='ax-ch18-') as tmp:
        root = Path(tmp)
        with LocalRunner(True) as runner:
            runner.launch('exit-two', root / 'exit.json')
            runner.child.wait(timeout=3)
            after_exit = runner.observation()
        with LocalRunner(False) as runner:
            runner.launch('cooperative', root / 'setup.json')
            setup_error = runner.observation()
            runner.stop()
        with LocalRunner(True) as runner:
            runner.launch('cooperative', root / 'graceful.json')
            runner.stop()
            graceful = {'sigkill': runner.sigkill_sent, 'checkpoint_saved': (root / 'graceful.json').is_file()}
        with LocalRunner(True) as runner:
            runner.launch('stubborn', root / 'forced.json')
            runner.stop(grace=.05)
            forced = {'sigkill': runner.sigkill_sent, 'checkpoint_saved': (root / 'forced.json').is_file()}
        return {'source': 'local_http_and_subprocess_fixture', 'ax_and_substrate_contacted': False,
                'after_exit_2': after_exit, 'setup_error': setup_error,
                'cooperative_stop': graceful, 'stubborn_stop': forced}


def demo() -> dict:
    with TemporaryDirectory(prefix='ax-ch18-report-') as tmp:
        workspace = Path(tmp)
        with LocalRunner(False) as runner:
            before = {'health': runner.probe('/healthz'), 'ready': runner.probe('/readyz')}
            (workspace / 'service.txt').write_text(
                'service: billing\nTODO: add retry test\nTODO: document timeout\n', encoding='utf-8')
            runner.workspace_ready = True
            runner.launch('report', workspace / 'result.json')
            exit_code = runner.child.wait(timeout=3)
            result = json.loads((workspace / 'result.json').read_text(encoding='utf-8'))
            report = (workspace / result['report']).read_text(encoding='utf-8').splitlines()
            after = {'health': runner.probe('/healthz'), 'ready': runner.probe('/readyz'),
                     'exit_code': exit_code}
        return {'before_setup': before, 'after_command': after,
                'result': result, 'report_lines': report, 'runner_closed': not runner.thread.is_alive()}


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == '--child':
        raise SystemExit(child_main(sys.argv[2], Path(sys.argv[3])))
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
