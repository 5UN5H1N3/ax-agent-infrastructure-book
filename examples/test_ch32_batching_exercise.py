"""The faulty exercise is intentional; these author-side tests must be green."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import ch32_batching_exercise as exercise


def run_suite(source):
    module = type(__import__("sys"))("batching")
    exec(compile(source, "batching.py", "exec"), module.__dict__)
    namespace = {"__name__": "exercise_tests"}
    with patch.dict("sys.modules", {"batching": module}):
        exec(compile(exercise.TEST_SOURCE, "test_batching.py", "exec"), namespace)
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(namespace["BatchingTests"])
    stream = io.StringIO()
    result = unittest.TextTestRunner(stream=stream).run(suite)
    return result, stream.getvalue()


class BatchingExerciseTests(unittest.TestCase):
    def test_baseline_runs_ten_checks(self):
        result, _ = run_suite(exercise.BROKEN_SOURCE)
        self.assertEqual(result.testsRun, 10)

    def test_baseline_has_exactly_three_expected_failures(self):
        result, _ = run_suite(exercise.BROKEN_SOURCE)
        self.assertEqual([case._testMethodName for case, _ in result.failures],
                         ["test_coverage", "test_short", "test_tail"])
        self.assertEqual(result.errors, [])

    def test_reference_change_satisfies_fixture(self):
        fixed = exercise.BROKEN_SOURCE.replace("len(items) - size + 1", "len(items)")
        result, _ = run_suite(fixed)
        self.assertTrue(result.wasSuccessful())

    def test_returning_empty_is_detected(self):
        result, _ = run_suite("def batches(items, size): return []")
        self.assertFalse(result.wasSuccessful())

    def test_ignoring_size_is_detected(self):
        result, _ = run_suite("def batches(items, size): return [items]")
        self.assertFalse(result.wasSuccessful())

    def test_reordering_is_detected(self):
        source = exercise.BROKEN_SOURCE.replace("len(items) - size + 1", "len(items)")
        source = source.replace("items[i:i + size]", "list(reversed(items[i:i + size]))")
        result, _ = run_suite(source)
        self.assertFalse(result.wasSuccessful())

    def test_exercise_files_are_exact(self):
        with tempfile.TemporaryDirectory() as directory:
            target = exercise.create_exercise(directory)
            self.assertEqual(target.name, "ax-lab32-batching")
            self.assertEqual(sorted(p.name for p in target.iterdir()),
                             ["README.md", "batching.py", "test_batching.py"])
            self.assertEqual((target / "batching.py").read_text(), exercise.BROKEN_SOURCE)
            self.assertEqual((target / "test_batching.py").read_text(), exercise.TEST_SOURCE)
            self.assertEqual((target / "README.md").read_text(), exercise.README)

    def test_existing_exercise_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            target = exercise.create_exercise(directory)
            before = {p.name: p.read_bytes() for p in target.iterdir()}
            with self.assertRaises(FileExistsError):
                exercise.create_exercise(directory)
            self.assertEqual({p.name: p.read_bytes() for p in target.iterdir()}, before)

    def test_symlink_is_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            original = parent / "original"
            original.mkdir()
            (parent / "ax-lab32-batching").symlink_to(original, target_is_directory=True)
            with self.assertRaises(FileExistsError):
                exercise.create_exercise(parent)
            self.assertEqual(list(original.iterdir()), [])

    def test_missing_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                exercise.create_exercise(Path(directory) / "missing")

    def test_file_parent_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory) / "file"
            parent.touch()
            with self.assertRaises(ValueError):
                exercise.create_exercise(parent)

    def test_cli_requires_one_argument(self):
        for args in ([], ["one", "two"]):
            with self.subTest(args=args), self.assertRaises(SystemExit):
                exercise.main(args)

    def test_cli_prints_created_target(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()) as out:
            exercise.main([directory])
            self.assertEqual(out.getvalue().strip(), str(Path(directory) / "ax-lab32-batching"))

    def test_generator_has_no_git_or_agent_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            target = exercise.create_exercise(directory)
            self.assertFalse((target / ".git").exists())
            self.assertFalse((target / ".agents").exists())


if __name__ == "__main__":
    unittest.main()
