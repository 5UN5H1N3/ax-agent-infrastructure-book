"""Build an intentionally faulty, offline Python exercise for chapter 32.

Only writes three fixed files into a NEW ax-lab32-batching child directory.
Does not run Git, tools, agents, a model, a server or any infrastructure.
The generated suite is intentionally red: 10 tests, 3 failing tests.
"""

from pathlib import Path
import sys


BROKEN_SOURCE = '''"""Split a list into ordered batches without modifying the input."""


def batches(items, size):
    if type(size) is not int or size <= 0:
        raise ValueError("size must be a positive integer")
    return [items[i:i + size]
            for i in range(0, len(items) - size + 1, size)]
'''

TEST_SOURCE = '''import unittest
from batching import batches


class BatchingTests(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(batches([], 2), [])

    def test_exact(self):
        self.assertEqual(batches([0, 1, 2, 3], 2), [[0, 1], [2, 3]])

    def test_tail(self):
        self.assertEqual(batches([0, 1, 2, 3, 4], 2), [[0, 1], [2, 3], [4]])

    def test_short(self):
        self.assertEqual(batches([0, 1], 3), [[0, 1]])

    def test_one(self):
        self.assertEqual(batches([0, 1], 1), [[0], [1]])

    def test_duplicates(self):
        self.assertEqual(batches([1, 1, 2, 2], 2), [[1, 1], [2, 2]])

    def test_input_unchanged(self):
        items = [0, 1, 2]
        batches(items, 2)
        self.assertEqual(items, [0, 1, 2])

    def test_invalid_size(self):
        for size in (0, -1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                batches([0], size)

    def test_size_types(self):
        for size in (True, False, 1.0, "2", None):
            with self.subTest(size=size), self.assertRaises(ValueError):
                batches([0], size)

    def test_coverage(self):
        for length in range(8):
            for size in range(1, 5):
                items = list(range(length))
                parts = batches(items, size)
                self.assertTrue(all(0 < len(part) <= size for part in parts))
                self.assertEqual([item for part in parts for item in part], items)


if __name__ == "__main__":
    unittest.main()
'''

README = '''# Chapter 32: missing records

Python 3, standard library only. No network, credentials or dependencies.

The batches(items, size) contract takes a list and a positive integer size.
It returns non-empty ordered lists, each no larger than size. Every input
element occurs exactly once, including duplicates; the input is unchanged.
The last batch may be smaller. Empty input returns []. Bool and non-integer
sizes, zero and negative sizes raise ValueError.

Run: python3 -m unittest -v
Expected baseline: 10 tests, 3 failures (tail, short, coverage).
Fix batching.py without changing this contract or the supplied tests.
Add independent tests if needed. Do not install dependencies or use network.
Submit the diff, exact input commit, test command, exit code and output.

Ten green tests do not prove correctness for every input. The exercise is
deliberately small: coordination overhead may exceed any multi-agent benefit.
'''


def create_exercise(parent):
    """Create a fresh child; refuse existing paths, including symlinks.

    Parent is a caller-selected trusted local directory. There is no rollback
    on disk errors and no protection against a hostile parent-directory owner.
    """
    parent = Path(parent)
    if not parent.is_dir():
        raise ValueError("parent must be an existing directory")
    target = parent / "ax-lab32-batching"
    target.mkdir()  # exist_ok=False: never replace a previous exercise
    for name, content in (
        ("batching.py", BROKEN_SOURCE),
        ("test_batching.py", TEST_SOURCE),
        ("README.md", README),
    ):
        with (target / name).open("x", encoding="utf-8", newline="\n") as output:
            output.write(content)
    return target


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        raise SystemExit("usage: ch32_batching_exercise.py EXISTING_PARENT_DIRECTORY")
    print(create_exercise(args[0]))


if __name__ == "__main__":
    main()
