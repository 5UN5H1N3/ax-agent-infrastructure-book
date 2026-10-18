"""Regression tests for print-to-web links and known coherence guards."""
import ast
import re
from pathlib import Path
import tempfile
import unittest

from bs4 import BeautifulSoup
from check_book_consistency import check_source, check_docs, check_artifact_readme
from test_readiness_register import ReadinessRegisterTests

ROOT = Path(__file__).resolve().parents[1]


class BookConsistencyTests(unittest.TestCase):
    def errors_for_text(self, text):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "book.html"
            fixture.write_text(text)
            return check_source(fixture)

    def text_with_paragraph(self, number, body):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        index = text.index(f'id="ch-{number}"')
        position = text.index("</h1>", index) + len("</h1>")
        return text[:position] + f"<p>{body}</p>" + text[position:]

    def test_current_source(self):
        self.assertEqual(check_source(ROOT / "book/AX_Agent_Infrastructure_Book_RU.html"), [])

    def test_first_pass_entry_precedes_example_catalog(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        chapter = soup.find(id="ch-0")
        entry = chapter.select_one(".reader-entry")
        self.assertIsNotNone(entry)
        self.assertEqual([h.get_text() for h in entry.find_all("h3")], [
            "Что нужно знать на входе", "Первый проход: от запроса к проверенному ответу"])
        self.assertIn("JSON", entry.get_text())
        self.assertIn("HTTP", entry.get_text())
        self.assertEqual({a["href"] for a in entry.find_all("a")},
                         {"#ch-1", "#ch-2", "#ch-3", "#ch-4"})
        self.assertLess(str(chapter).index('class="reader-entry"'),
                        str(chapter).index('id="examples-guide"'))

    def test_five_roles_and_answer_key_precede_full_stack(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        chapter = soup.find(id="ch-1")
        walkthrough = chapter.select_one(".request-walkthrough")
        self.assertIsNotNone(walkthrough)
        roles = walkthrough.select("ol > li > strong")
        self.assertEqual(len(roles), 5)
        for role, name in zip(roles, ("Пользователь", "Harness", "Модель", "Инструмент", "Внешняя система")):
            self.assertTrue(role.get_text().startswith(name))
        self.assertEqual(walkthrough.h3.get_text(), "Проверьте себя на одном ответе")
        self.assertIn("Разбор.", walkthrough.get_text())
        self.assertLess(str(chapter).index('class="request-walkthrough"'),
                        str(chapter).index("<figure>"))
        self.assertLess(str(chapter).index('class="request-walkthrough"'),
                        str(chapter).index("<table>"))

    def test_later_vocabulary_links_to_relevant_chapters(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        route = soup.find(id="ch-1").select_one(".vocabulary-route")
        self.assertIsNotNone(route)
        for terms, destinations in ((["Retry", "idempotency", "Checkpoint", "Fan-out/fan-in"],
                                     {"#ch-10", "#ch-5", "#ch-7"}),
                                    (["Lifecycle", "Quota"], {"#ch-14", "#ch-13", "#ch-34"})):
            paragraph = next(p for p in route.find_all("p") if terms[0] in p.get_text())
            for term in terms:
                self.assertIn(term, paragraph.get_text())
            self.assertEqual({a["href"] for a in paragraph.find_all("a")}, destinations)
        for link in route.find_all("a"):
            self.assertIsNotNone(soup.select_one(link["href"]).find("h1"))

    def test_intro_keeps_reference_map_and_term_contract(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        tables = soup.find(id="ch-1").find_all("table")
        self.assertEqual(len(tables[0].select("tbody tr")), 7)
        terms = [row.td.get_text() for row in tables[1].select("tbody tr")]
        self.assertEqual(terms, ["задача", "run", "Task", "execution", "coordinator",
                                 "orchestrator", "validation", "verification"])

    def test_isolated_trace_sdk_profile_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / 'book.html'
            text = (ROOT / 'book/AX_Agent_Infrastructure_Book_RU.html').read_text()
            fixture.write_text(text.replace('OpenTelemetry Python: локальный учебный профиль', 'Missing SDK profile'))
            self.assertIn('Missing isolated OpenTelemetry learning profile', check_source(fixture))

    def test_missing_version_registry_is_reported(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "book.html"
            fixture.write_text(text.replace('id="version-profiles"', 'id="missing-registry"'))
            self.assertTrue(any("version profile" in error for error in check_source(fixture)))

    def test_removed_controller_regression_is_reported(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        index = text.index('id="ch-28"')
        position = text.index("</h1>", index) + len("</h1>")
        text = text[:position] + "<p>controller создаёт один Worker loop</p>" + text[position:]
        with tempfile.TemporaryDirectory() as directory:
            fixture = Path(directory) / "book.html"
            fixture.write_text(text)
            self.assertTrue(any("Chapter 28" in error for error in check_source(fixture)))

    def test_print_anchor_rewriting(self):
        # Extract the pure helper without executing the generator's file writes.
        tree = ast.parse((ROOT / "scripts/build_webbook.py").read_text())
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                        and node.name == "rewrite_internal_links")
        namespace = {}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "link-helper", "exec"), namespace)
        soup = BeautifulSoup('<a href="#ch-39">restore</a><a href="#version-profiles">versions</a>'
                             '<a href="https://example.org/#ch-39">external</a>'
                             '<a href="#local">local</a>', "html.parser")
        namespace["rewrite_internal_links"](soup, {
            "ch-39": "39-substrate-snapshot-ownership.md",
            "version-profiles": "00-reading-guide.md",
        })
        self.assertEqual([a["href"] for a in soup.find_all("a")], [
            "39-substrate-snapshot-ownership.md", "00-reading-guide.md",
            "https://example.org/#ch-39", "#local",
        ])

    def test_model_binding_claim_is_reported(self):
        errors = self.errors_for_text(self.text_with_paragraph(22, "AX использует Workspace/Model bindings"))
        self.assertTrue(any("Workspace/Model bindings" in error for error in errors))

    def test_data_cold_boot_generalization_is_reported(self):
        errors = self.errors_for_text(self.text_with_paragraph(14, "Data + fresh boot"))
        self.assertTrue(any("Chapter 14" in error for error in errors))

    def test_task_uid_and_mcp_profile_regressions_are_reported(self):
        for body in ("Task/Actor name и UID", "для MCP over HTTP - initialize и read"):
            with self.subTest(body=body):
                errors = self.errors_for_text(self.text_with_paragraph(40, body))
                self.assertTrue(any("Chapter 40" in error for error in errors))

    def test_floating_substrate_source_is_reported(self):
        text = self.text_with_paragraph(13, '<a href="https://github.com/agent-substrate/substrate/blob/main/docs/api-guide.md">API</a>')
        self.assertTrue(any("floating implementation source" in error for error in self.errors_for_text(text)))

    def test_table_of_contents_mismatch_is_reported(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        soup = BeautifulSoup(text, "html.parser")
        link = soup.find("a", href="#ch-20")
        link.string = "20. obsolete title"
        self.assertTrue(any("Chapter 20: table of contents" in error for error in self.errors_for_text(str(soup))))

    def test_direct_path_resume_order_is_reported(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        chapter = soup.find(id="ch-26")
        block = next(block for block in chapter.find_all("pre") if "ax apply" in block.get_text())
        block.code.string = block.get_text().replace("ax resume task", "ax get task", 1)
        self.assertTrue(any("resumed before first ssh" in error for error in self.errors_for_text(str(soup))))

    def test_lab_compatibility_gate_is_reported(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text().replace("672533541dbf", "missing-sdk")
        self.assertTrue(any("release/compatibility contract" in error for error in self.errors_for_text(text)))

    def test_unsupported_ax_manifest_is_reported(self):
        text = self.text_with_paragraph(17, "fixture")
        index = text.index('id="ch-17"')
        position = text.index("</h1>", index) + len("</h1>")
        for extra, expected in (("  modelRef: default-model", "modelRef"),
                                ("  uid: invented", "Kubernetes metadata")):
            with self.subTest(extra=extra):
                fixture = '<pre><code>apiVersion: ax.io/v1alpha1\nkind: Task\nmetadata:\n' + extra + '</code></pre>'
                errors = self.errors_for_text(text[:position] + fixture + text[position:])
                self.assertTrue(any(expected in error for error in errors))

    def test_cleanup_success_generalization_is_reported(self):
        text = self.text_with_paragraph(19, "Только после этого AX удаляет Task record")
        self.assertTrue(any("Chapter 19" in error for error in self.errors_for_text(text)))

    def test_edition_passport_count_mismatch_is_reported(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        errors = self.errors_for_text(text.replace("42 технические схемы", "33 технические схемы"))
        self.assertTrue(any("Edition passport" in error for error in errors))

    def test_ambiguous_verification_badge_is_reported(self):
        text = self.text_with_paragraph(0, '<span class="badge">CURRENT MAIN VERIFIED</span>')
        self.assertTrue(any("Ambiguous verification" in error for error in self.errors_for_text(text)))

    def test_short_version_signature_is_reported(self):
        text = self.text_with_paragraph(0, "AX e70162a")
        self.assertTrue(any("eight-character" in error for error in self.errors_for_text(text)))

    def test_missing_appendix_in_print_contents_is_reported(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        soup.select_one('.toc a[href="#appendix-b"]').parent.decompose()
        self.assertTrue(any("three appendices" in error for error in self.errors_for_text(str(soup))))

    def test_web_contents_mismatch_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            docs = Path(directory)
            for page in (ROOT / "docs").glob("*.md"):
                text = page.read_text()
                if page.name == "SUMMARY.md":
                    text = text.replace("* [20. ", "* [99. ")
                (docs / page.name).write_text(text)
            self.assertTrue(any("Web contents titles" in error for error in check_docs(docs)))

    def test_approval_is_not_a_terminal_result(self):
        errors = self.errors_for_text(self.text_with_paragraph(3,
            '<ul><li>terminal result: WaitingForApproval</li></ul>'))
        self.assertTrue(any("pause is not a terminal" in error for error in errors))

    def test_terminal_run_cannot_silently_retry(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        text = text.replace('<td>терминальное</td>', '<td>Running</td>')
        self.assertTrue(any("explicit terminal contract" in error for error in self.errors_for_text(text)))

    def test_approval_state_paths_in_printed_table(self):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        states = {}
        for row in soup.find(id="ch-3").select("table tbody tr"):
            cells = row.find_all("td")
            if len(cells) == 3:
                states[cells[0].get_text(strip=True)] = cells[2].get_text(" ", strip=True)
        # Check the declared teaching contract, not a running harness:
        # restart is not a new logical state; approval resumes the same run.
        for before, after in zip(["Created", "Running", "WaitingForApproval", "Running", "Verifying"],
                                 ["Running", "WaitingForApproval", "Running", "Verifying", "Succeeded"]):
            self.assertIn(after, states[before].split(", "))
        self.assertIn("Failed", states["WaitingForApproval"].split(", "))
        self.assertEqual(states["Failed"], "терминальное")
        self.assertEqual(states["Blocked"], "терминальное")

    def test_trace_scope_regression(self):
        errors = self.errors_for_text(self.text_with_paragraph(2, "Сохраняйте полный trace"))
        self.assertTrue(any("trace advice" in error for error in errors))

    def test_no_kvm_platform_regression(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        start, end = text.index('id="ch-32"'), text.index('id="ch-33"')
        changed = text[:start] + text[start:end].replace("Systrap", "unspecified") + text[end:]
        self.assertTrue(any("gVisor platform" in error for error in self.errors_for_text(changed)))

    def test_budget_reserve_regression(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        self.assertTrue(any("budget/reserve" in error for error in self.errors_for_text(
            text.replace("root reserve=20", "root reserve=0"))))

    def test_fallback_cover_date_regression(self):
        text = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()
        text = re.sub(r"рабочая редакция \d{2}\.\d{2}\.\d{4}", "рабочая редакция 00.00.0000", text)
        self.assertTrue(any("cover date" in error for error in self.errors_for_text(text)))

    def test_artifact_readme_single_source(self):
        text = (ROOT / "book/README.md").read_text()
        self.assertEqual(check_artifact_readme(text), [])
        self.assertTrue(check_artifact_readme(text + "\n- Google AX: v0.3.1 / e70162a\n"))
        self.assertTrue(check_artifact_readme(text.replace("../docs/README.md#паспорт-издания", "stale")))


if __name__ == "__main__":
    unittest.main()
