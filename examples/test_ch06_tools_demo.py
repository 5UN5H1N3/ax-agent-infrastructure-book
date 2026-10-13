import ast
import copy
from pathlib import Path
import unittest

import ch06_tools_demo as demo


class ToolBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.args = {"revision": demo.REVISION, "check_id": "job-42"}

    def test_successful_read_of_failed_test(self):
        result = demo.execute("review.get_check", self.args)
        data = demo.validate_result("review.get_check", self.args, result)
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(data["status"], "failed")

    def test_read_both_allowed_files(self):
        for path in demo.FILES:
            args = {"revision": demo.REVISION, "path": path}
            result = demo.execute("review.read_file", args)
            self.assertEqual(demo.validate_result("review.read_file", args, result)["text"], demo.FILES[path])

    def test_extra_or_missing_field_rejected(self):
        for args in ({**self.args, "command": "publish"}, {"revision": demo.REVISION}):
            self.assertEqual(demo.execute("review.get_check", args)["status"], "invalid")

    def test_invalid_argument_types(self):
        for value in (None, [], "request", 2):
            self.assertEqual(demo.execute("review.get_check", value)["status"], "invalid")
        for value in (None, 3, True, [], "", "x" * 257):
            args = {**self.args, "check_id": value}
            self.assertEqual(demo.execute("review.get_check", args)["status"], "invalid")

    def test_string_boundaries(self):
        result = demo.execute("review.get_check", {**self.args, "check_id": "x" * 256})
        self.assertEqual(result["reason"], "unknown_check")

    def test_other_revision_is_not_substituted(self):
        self.assertEqual(demo.execute("review.get_check", {**self.args, "revision": "old"})["status"], "conflict")

    def test_unavailable_tool_denied(self):
        for name in ("review.publish_comment", "execute", [], None):
            self.assertEqual(demo.execute(name, {})["status"], "denied")

    def test_unallowed_paths_never_opened(self):
        for path in ("../secret", "/etc/passwd", "unlisted.py"):
            result = demo.execute("review.read_file", {"revision": demo.REVISION, "path": path})
            self.assertEqual(result["status"], "denied")

    def test_unknown_check_not_success(self):
        self.assertEqual(demo.execute("review.get_check", {**self.args, "check_id": "job-99"})["status"], "invalid")

    def test_returned_check_is_a_copy(self):
        result = demo.execute("review.get_check", self.args)
        result["data"]["status"] = "passed"
        self.assertEqual(demo.CHECK["status"], "failed")

    def test_mismatched_result_rejected(self):
        for key in self.args:
            result = demo.execute("review.get_check", self.args)
            result["data"][key] = "another-object"
            with self.assertRaises(ValueError):
                demo.validate_result("review.get_check", self.args, result)

    def test_malformed_result_rejected(self):
        baseline = demo.execute("review.get_check", self.args)
        cases = [None, [], {"status": "denied"}]
        for field, value in (("data", []), ("evidence", "raw")):
            changed = copy.deepcopy(baseline)
            changed[field] = value
            cases.append(changed)
        for field, value in (("source", "remote-server"), ("raw_ref", ""), ("raw_ref", None)):
            changed = copy.deepcopy(baseline)
            changed["evidence"][field] = value
            cases.append(changed)
        changed = copy.deepcopy(baseline)
        changed["data"]["status"] = "accepted"
        cases.append(changed)
        for result in cases:
            with self.assertRaises(ValueError):
                demo.validate_result("review.get_check", self.args, result)

    def test_file_text_type_checked(self):
        args = {"revision": demo.REVISION, "path": "src/stats.py"}
        result = demo.execute("review.read_file", args)
        result["data"]["text"] = 4
        with self.assertRaises(ValueError):
            demo.validate_result("review.read_file", args, result)

    def test_chapter_excerpts_match_executable_functions(self):
        import re
        import html
        source = Path(__file__).resolve().parents[1] / "book/AX_Agent_Infrastructure_Book_RU.html"
        chapter = re.search(r'<section\b[^>]*\bid="ch-6"[^>]*>.*?</section>', source.read_text(), re.S)[0]
        snippets = re.findall(r'<code class="language-python">(.*?)</code>', chapter, re.S)
        actual = {node.name: ast.dump(node) for node in ast.parse(Path(demo.__file__).read_text()).body
                  if isinstance(node, ast.FunctionDef)}
        self.assertEqual(len(snippets), 2)
        for snippet in snippets:
            node = ast.parse(html.unescape(snippet)).body[0]
            self.assertEqual(ast.dump(node), actual[node.name])


if __name__ == "__main__":
    unittest.main()
