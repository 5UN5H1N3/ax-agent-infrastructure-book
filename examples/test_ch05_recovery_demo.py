"""Local recovery tests; not integration tests for AX or a remote service."""
import ast
from contextlib import closing
import html
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import ch05_recovery_demo as demo


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="ax-ch05-test-")
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.local = demo.connect(self.root / "harness.sqlite")
        self.service = demo.connect(self.root / "service.sqlite")
        self.addCleanup(self.local.close)
        self.addCleanup(self.service.close)
        demo.initialize(self.local, self.service)

    def operation(self):
        return dict(self.local.execute("SELECT * FROM operations").fetchone())

    def test_clean_run_observes_failed_simulation(self):
        result = demo.recover(self.local, self.service)
        self.assertEqual(result["test_outcome"], "failed")
        self.assertTrue(result["simulated"])
        self.assertEqual(self.operation()["status"], "observed")
        self.assertEqual(result["service_jobs"], 1)

    def test_repeating_recovery_keeps_one_job(self):
        first = demo.recover(self.local, self.service)
        for _ in range(4):
            self.assertEqual(demo.recover(self.local, self.service), first)
        self.assertEqual(demo.job_count(self.service), 1)

    def test_process_exit_leaves_intent_without_local_result(self):
        with self.assertRaises(SystemExit) as stopped:
            demo.recover(self.local, self.service, crash=True)
        self.assertEqual(stopped.exception.code, 23)
        self.assertEqual(self.operation()["status"], "prepared")
        self.assertIsNone(self.operation()["job_id"])
        self.assertEqual(demo.job_count(self.service), 1)
        # Fresh connections cannot see process-local Python objects.
        with closing(demo.connect(self.root / "harness.sqlite")) as local:
            with closing(demo.connect(self.root / "service.sqlite")) as service:
                result = demo.recover(local, service)
        self.assertEqual(result["job_id"], 1)
        self.assertEqual(result["service_jobs"], 1)

    def test_prepare_is_idempotent_and_does_not_clear_observation(self):
        demo.recover(self.local, self.service)
        with self.local:
            demo.prepare(self.local)
        self.assertEqual(self.operation()["status"], "observed")
        self.assertEqual(self.operation()["job_id"], 1)
        self.assertEqual(
            self.local.execute("SELECT COUNT(*) FROM operations").fetchone()[0], 1
        )

    def test_local_revision_conflict_is_rejected_before_service_action(self):
        with self.local:
            self.local.execute(
                "INSERT INTO operations VALUES (?, ?, ?, NULL)",
                (demo.OPERATION, "another-fixture", "prepared"),
            )
        with self.assertRaisesRegex(ValueError, "another revision"):
            demo.recover(self.local, self.service)
        self.assertEqual(demo.job_count(self.service), 0)
        self.assertEqual(self.operation()["revision"], "another-fixture")

    def test_wrong_service_revision_is_not_recorded_as_observed(self):
        demo.create_or_get(self.service, demo.OPERATION, "another-fixture")
        with self.assertRaisesRegex(ValueError, "another revision"):
            demo.recover(self.local, self.service)
        self.assertEqual(self.operation()["status"], "prepared")
        self.assertIsNone(self.operation()["job_id"])
        self.assertEqual(demo.job_count(self.service), 1)

    def test_service_key_reuse_with_different_revision_is_rejected(self):
        first = demo.create_or_get(self.service, demo.OPERATION, demo.REVISION)
        with self.assertRaisesRegex(ValueError, "another revision"):
            demo.create_or_get(self.service, demo.OPERATION, "another-fixture")
        self.assertEqual(demo.lookup(self.service, demo.OPERATION), first)
        self.assertEqual(demo.job_count(self.service), 1)

    def test_service_key_reuse_with_same_revision_returns_same_job(self):
        first = demo.create_or_get(self.service, demo.OPERATION, demo.REVISION)
        self.assertEqual(
            demo.create_or_get(self.service, demo.OPERATION, demo.REVISION), first
        )
        self.assertEqual(demo.job_count(self.service), 1)

    def test_negative_lookup_does_not_create_job(self):
        self.assertIsNone(demo.lookup(self.service, demo.OPERATION))
        self.assertEqual(demo.job_count(self.service), 0)

    def test_saved_intent_without_service_job_can_continue_in_this_demo(self):
        with self.local:
            demo.prepare(self.local)
        self.assertEqual(demo.job_count(self.service), 0)
        result = demo.recover(self.local, self.service)
        self.assertEqual(result["operation_id"], demo.OPERATION)
        self.assertEqual(result["service_jobs"], 1)
        self.assertEqual(self.operation()["status"], "observed")

    def test_both_printed_functions_match_executable_example(self):
        repo = Path(demo.__file__).resolve().parents[1]
        raw = (repo / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        chapter = re.search(
            r'<section\b[^>]*\bid="ch-5"[^>]*>.*?</section>', raw, re.S
        ).group()
        snippets = re.findall(
            r'<pre><code class="language-python">(.*?)</code></pre>', chapter, re.S
        )
        self.assertEqual(len(snippets), 2)
        executable = {
            node.name: ast.dump(node)
            for node in ast.parse(Path(demo.__file__).read_text()).body
            if isinstance(node, ast.FunctionDef)
        }
        for snippet in snippets:
            parsed = ast.parse(html.unescape(snippet))
            self.assertEqual(len(parsed.body), 1)
            node = parsed.body[0]
            self.assertEqual(ast.dump(node), executable[node.name])

    def test_crash_and_resume_are_separate_os_processes(self):
        script = Path(demo.__file__).resolve()
        command = [sys.executable, str(script), str(self.root)]
        stopped = subprocess.run(
            command + ["--crash"], capture_output=True, text=True, timeout=10
        )
        self.assertEqual(stopped.returncode, 23, stopped.stderr)
        self.assertIn("Stopped after service commit", stopped.stdout)
        self.assertEqual(self.operation()["status"], "prepared")
        self.assertIsNone(self.operation()["job_id"])
        self.assertEqual(demo.job_count(self.service), 1)
        for _ in range(2):
            resumed = subprocess.run(
                command, capture_output=True, text=True, timeout=10
            )
            self.assertEqual(resumed.returncode, 0, resumed.stderr)
            result = json.loads(resumed.stdout)
            self.assertEqual(result["job_id"], 1)
            self.assertEqual(result["service_jobs"], 1)
            self.assertEqual(result["test_outcome"], "failed")
            self.assertTrue(result["simulated"])
        self.assertEqual(self.operation()["status"], "observed")


if __name__ == "__main__":
    unittest.main()
