"""Contract tests for the fixed fan-in example; no model or network calls."""
from copy import deepcopy
import ast
from html import unescape
from pathlib import Path
import re
import unittest

from ch07_coordination_demo import CONTRACTS, collect, fixture_outputs


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.outputs = fixture_outputs()

    def rejected(self, mutate):
        mutate(self.outputs)
        with self.assertRaises(ValueError):
            collect(CONTRACTS, self.outputs)

    def test_complete_is_only_ready_for_synthesis(self):
        result = collect(CONTRACTS, self.outputs)
        self.assertEqual(result["status"], "ready_for_synthesis")
        self.assertEqual(result["missing"], [])
        self.assertEqual(len(result["claims"]["empty_input"]["raises_IndexError"]), 3)

    def test_missing_required_branch(self):
        result = collect(CONTRACTS, self.outputs[:1])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["missing"], ["review-demo/tests"])

    def test_no_outputs(self):
        result = collect(CONTRACTS, [])
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["missing"], sorted(CONTRACTS))

    def test_duplicate_does_not_cover_missing_branch(self):
        self.rejected(lambda rows: rows.__setitem__(1, deepcopy(rows[0])))

    def test_stale_revision(self):
        self.rejected(lambda rows: rows[1].__setitem__("revision", "old"))

    def test_old_attempt(self):
        contracts = deepcopy(CONTRACTS)
        contracts["review-demo/tests"]["attempt"] = 2
        with self.assertRaises(ValueError):
            collect(contracts, self.outputs)

    def test_boolean_is_not_attempt_one(self):
        self.rejected(lambda rows: rows[1].__setitem__("attempt", True))

    def test_unknown_child(self):
        self.rejected(lambda rows: rows[1].__setitem__("task_id", "other"))

    def test_missing_identity(self):
        self.rejected(lambda rows: rows[1].pop("task_id"))

    def test_unstructured_output(self):
        self.rejected(lambda rows: rows.__setitem__(1, "done"))

    def test_unhashable_child_id(self):
        self.rejected(lambda rows: rows[1].__setitem__("task_id", []))

    def test_extra_fields(self):
        self.rejected(lambda rows: rows[1].__setitem__("publish", True))

    def test_missing_findings(self):
        self.rejected(lambda rows: rows[1].pop("findings"))

    def test_unknown_status(self):
        self.rejected(lambda rows: rows[1].__setitem__("status", "passed"))

    def test_partial_is_not_complete(self):
        self.outputs[1]["status"] = "partial"
        result = collect(CONTRACTS, self.outputs)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["partial"], ["review-demo/tests"])

    def test_conflict_preserves_both_observations(self):
        self.outputs[1]["findings"][0]["value"] = "returns_zero"
        result = collect(CONTRACTS, self.outputs)
        self.assertEqual(result["status"], "unresolved")
        self.assertEqual(result["conflicts"], ["empty_input"])
        self.assertEqual(set(result["claims"]["empty_input"]),
                         {"raises_IndexError", "returns_zero"})

    def test_partial_does_not_hide_conflict(self):
        self.outputs[1]["status"] = "partial"
        self.outputs[1]["findings"][0]["value"] = "returns_zero"
        result = collect(CONTRACTS, self.outputs)
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["conflicts"], ["empty_input"])

    def test_evidence_outside_scope(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "evidence_refs", ["fixture://checks/job-42"]))

    def test_no_evidence(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "evidence_refs", []))

    def test_bad_evidence_type(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "evidence_refs", [{}]))

    def test_duplicate_evidence_is_not_counted_twice(self):
        self.outputs[0]["findings"][0]["evidence_refs"] *= 2
        result = collect(CONTRACTS, self.outputs)
        self.assertEqual(len(result["claims"]["empty_input"]["raises_IndexError"]), 3)

    def test_unknown_question(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "key", "expected_behavior"))

    def test_unknown_observation(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "value", "probably_safe"))

    def test_findings_bounds(self):
        for count in (0, 9):
            with self.subTest(count=count):
                outputs = fixture_outputs()
                outputs[0]["findings"] *= count
                with self.assertRaises(ValueError):
                    collect(CONTRACTS, outputs)

    def test_malformed_finding(self):
        self.rejected(lambda rows: rows[0].__setitem__("findings", [None]))

    def test_extra_finding_fields(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__("confidence", 1))

    def test_evidence_bounds(self):
        self.rejected(lambda rows: rows[0]["findings"][0].__setitem__(
            "evidence_refs", ["fixture://files/src/stats.py"] * 9))

    def test_order_independent(self):
        self.assertEqual(collect(CONTRACTS, self.outputs),
                         collect(CONTRACTS, list(reversed(self.outputs))))

    def test_inputs_not_mutated(self):
        original = deepcopy(self.outputs)
        collect(CONTRACTS, self.outputs)
        self.assertEqual(self.outputs, original)

    def test_result_does_not_alias_inputs(self):
        result = collect(CONTRACTS, self.outputs)
        result["claims"]["empty_input"]["raises_IndexError"].clear()
        self.assertEqual(collect(CONTRACTS, self.outputs)["status"], "ready_for_synthesis")
        self.assertTrue(self.outputs[0]["findings"][0]["evidence_refs"])

    def test_chapter_excerpts_match_executable_functions(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        chapter = re.search(r'<section\b[^>]*\bid="ch-7"[^>]*>.*?</section>',
                            source, re.S).group()
        blocks = re.findall(r'<code class="language-python">(.*?)</code>', chapter, re.S)
        self.assertEqual(len(blocks), 2)
        module = ast.parse((root / "examples/ch07_coordination_demo.py").read_text())
        functions = {node.name: ast.dump(node) for node in module.body
                     if isinstance(node, ast.FunctionDef)}
        for block in blocks:
            node = ast.parse(unescape(block)).body[0]
            self.assertEqual(ast.dump(node), functions[node.name])


if __name__ == "__main__":
    unittest.main()
