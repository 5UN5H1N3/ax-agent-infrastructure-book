import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from ch18_runner_observation import LocalRunner, build_report, classify, demo, failure_demo


class ObservationPolicyTests(unittest.TestCase):
    def test_unreachable(self):
        self.assertEqual(classify(503, 200, True, None), 'RUNNER_UNREACHABLE')

    def test_failed_command_with_green_probes(self):
        self.assertEqual(classify(200, 200, False, 2), 'COMMAND_FAILED')

    def test_exit_zero_needs_result_verification(self):
        self.assertEqual(classify(200, 200, False, 0), 'VERIFY_RESULT')

    def test_no_command_is_not_liveness(self):
        self.assertEqual(classify(200, 200, False, None), 'NO_RUNNING_COMMAND')

    def test_running_without_ready(self):
        self.assertEqual(classify(200, 503, True, None), 'ENVIRONMENT_NOT_READY')

    def test_running_is_not_business_success(self):
        self.assertEqual(classify(200, 200, True, None), 'RUNNING_RESULT_UNKNOWN')

    def test_signal_exit_is_failure(self):
        self.assertEqual(classify(200, 200, False, -9), 'COMMAND_FAILED')


@unittest.skipUnless(os.name == 'posix', 'POSIX owned-process-group fixture')
class LocalRunnerTests(unittest.TestCase):
    def test_health_before_setup(self):
        with LocalRunner(False) as runner:
            self.assertEqual(runner.probe('/healthz'), 200)
            self.assertEqual(runner.probe('/readyz'), 503)

    def test_ready_can_be_green_without_command(self):
        with LocalRunner(True) as runner:
            self.assertEqual(runner.observation()['decision'], 'NO_RUNNING_COMMAND')

    def test_child_exit_leaves_http_alive(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            runner.launch('exit-two', Path(tmp) / 'checkpoint.json')
            self.assertEqual(runner.child.wait(timeout=3), 2)
            self.assertEqual(runner.observation(), {'health': 200, 'ready': 200, 'child_running': False, 'decision': 'COMMAND_FAILED'})

    def test_setup_failure_does_not_gate_command_in_fixture(self):
        with TemporaryDirectory() as tmp, LocalRunner(False) as runner:
            runner.launch('cooperative', Path(tmp) / 'checkpoint.json')
            self.assertEqual(runner.observation()['decision'], 'ENVIRONMENT_NOT_READY')
            self.assertTrue(runner.observation()['child_running'])
            runner.stop()

    def test_graceful_stop_saves_pause_not_completion(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            file = Path(tmp) / 'checkpoint.json'
            runner.launch('cooperative', file)
            self.assertEqual(runner.stop(), 0)
            self.assertFalse(runner.sigkill_sent)
            self.assertEqual(json.loads(file.read_text()), {'logical_step': 'paused', 'business_complete': False})

    def test_stubborn_child_escalates(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            file = Path(tmp) / 'checkpoint.json'
            runner.launch('stubborn', file)
            self.assertEqual(runner.stop(.05), -9)
            self.assertTrue(runner.sigkill_sent)
            self.assertFalse(file.exists())

    def test_shutdown_after_exit_does_not_signal(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            runner.launch('exit-zero', Path(tmp) / 'checkpoint.json')
            runner.child.wait(timeout=3)
            with patch.object(runner, '_signal_owned_group', side_effect=AssertionError('must not signal exited child')):
                self.assertEqual(runner.stop(), 0)

    def test_context_cleanup_reaps_owned_child(self):
        with TemporaryDirectory() as tmp:
            with LocalRunner(True) as runner:
                runner.launch('stubborn', Path(tmp) / 'checkpoint.json')
                child = runner.child
            self.assertIsNotNone(child.poll())
            self.assertFalse(runner.thread.is_alive())

    def test_group_is_separate_from_parent(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            runner.launch('cooperative', Path(tmp) / 'checkpoint.json')
            self.assertEqual(os.getpgid(runner.child.pid), runner.child.pid)
            self.assertNotEqual(os.getpgid(runner.child.pid), os.getpgrp())
            runner.stop()

    def test_refuses_wrong_group(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            runner.launch('cooperative', Path(tmp) / 'checkpoint.json')
            with patch('ch18_runner_observation.os.getpgid', return_value=os.getpgrp()), patch('ch18_runner_observation.os.killpg') as kill:
                with self.assertRaises(RuntimeError):
                    runner._signal_owned_group(15)
                kill.assert_not_called()
            runner.stop()

    def test_no_external_probe(self):
        with LocalRunner(True) as runner:
            with self.assertRaises(ValueError):
                runner.probe('https://example.com')

    def test_invalid_mode_rejected(self):
        with LocalRunner(True) as runner:
            with self.assertRaises(ValueError):
                runner.launch('unknown', Path('/unused'))

    def test_invalid_grace_rejected(self):
        with LocalRunner(True) as runner:
            for grace in [0, -1, 4]:
                with self.subTest(grace=grace), self.assertRaises(ValueError):
                    runner.stop(grace)

    def test_one_launch_only(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            runner.launch('cooperative', Path(tmp) / 'checkpoint.json')
            with self.assertRaises(RuntimeError):
                runner.launch('cooperative', Path(tmp) / 'other.json')
            runner.stop()

    def test_demo_real_observations(self):
        result = failure_demo()
        self.assertFalse(result['ax_and_substrate_contacted'])
        self.assertEqual(result['after_exit_2']['decision'], 'COMMAND_FAILED')
        self.assertEqual(result['setup_error']['ready'], 503)
        self.assertEqual(result['cooperative_stop'], {'sigkill': False, 'checkpoint_saved': True})
        self.assertEqual(result['stubborn_stop'], {'sigkill': True, 'checkpoint_saved': False})

    def test_successful_demo(self):
        result = demo()
        self.assertEqual(result['before_setup'], {'health': 200, 'ready': 503})
        self.assertEqual(result['after_command'], {'health': 200, 'ready': 200, 'exit_code': 0})
        self.assertEqual(result['result'], {'stage': 'completed', 'report': 'report.txt', 'items': 2})
        self.assertEqual(result['report_lines'], ['2: TODO: add retry test', '3: TODO: document timeout'])
        self.assertTrue(result['runner_closed'])

    def test_report_empty_input(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'service.txt').write_text('service: billing\n')
            self.assertEqual(build_report(root)['items'], 0)
            self.assertEqual(json.loads((root / 'result.json').read_text())['stage'], 'completed')
            self.assertFalse((root / 'result.tmp').exists())

    def test_missing_input_leaves_no_completion(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                build_report(root)
            self.assertFalse((root / 'result.json').exists())

    def test_report_uses_child_working_directory(self):
        with TemporaryDirectory() as tmp, LocalRunner(True) as runner:
            root = Path(tmp)
            (root / 'service.txt').write_text('TODO: check cwd\n')
            runner.launch('report', root / 'result.json')
            self.assertEqual(runner.child.wait(timeout=3), 0)
            self.assertEqual((root / 'report.txt').read_text(), '1: TODO: check cwd\n')


if __name__ == '__main__':
    unittest.main()
