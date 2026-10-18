"""Reader-entry, coverage and route regression checks (not stand tests)."""
from pathlib import Path
import tempfile
import unittest

from bs4 import BeautifulSoup
from example_catalog import (check_example_catalog, examples_readme,
                             WEB_SECTION_TARGETS, WEB_SECTION_HEADINGS,
                             add_web_section_anchors, check_web_section_anchors)

ROOT = Path(__file__).resolve().parents[1]


class ExampleCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text()

    def soup(self):
        return BeautifulSoup(self.source, "html.parser")

    def test_current_catalog_and_inventory(self):
        self.assertEqual(check_example_catalog(self.soup(), ROOT), [])

    def test_missing_program_is_reported(self):
        soup = self.soup()
        soup.select_one("tr[data-example]").decompose()
        self.assertTrue(any("39 unique" in e for e in check_example_catalog(soup)))

    def test_duplicate_program_is_reported(self):
        soup = self.soup()
        rows = soup.select("tr[data-example]")
        rows[1]["data-example"] = rows[0]["data-example"]
        self.assertTrue(any("39 unique" in e for e in check_example_catalog(soup)))

    def test_floating_code_link_is_reported(self):
        soup = self.soup()
        link = soup.select_one("tr[data-example] td").find_all("a")[1]
        link["href"] = link["href"].replace(soup.find(id="examples-guide")["data-code-ref"], "main")
        self.assertTrue(any("links differ" in e for e in check_example_catalog(soup)))

    def test_missing_mode_is_reported(self):
        soup = self.soup()
        soup.select_one("tr[data-example]").find_all("td")[2].clear()
        self.assertTrue(any("incomplete purpose/mode" in e for e in check_example_catalog(soup)))

    def test_missing_physical_test_file_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(any("missing author test" in e
                                for e in check_example_catalog(self.soup(), directory)))

    def test_missing_dependency_map_is_reported(self):
        soup = self.soup()
        soup.find(id="lab-dependencies").decompose()
        self.assertIn("Lab map: missing dependency entry", check_example_catalog(soup))

    def test_partial_route_boundary_is_required(self):
        soup = self.soup()
        lab = soup.find(id="lab-dependencies")
        paragraph = next(p for p in lab.find_all("p") if "частичную работу нельзя отмечать" in p.get_text())
        paragraph.decompose()
        self.assertTrue(any("частичную работу нельзя отмечать" in e for e in check_example_catalog(soup)))

    def test_generated_readme_matches_source(self):
        self.assertEqual((ROOT / "examples/README.md").read_text(), examples_readme(self.soup()))

    def test_repository_chapter_links_are_relative_and_exist(self):
        text = examples_readme(self.soup())
        self.assertNotIn("](#ch-", text)
        self.assertIn("../docs/32-labs.md", text)
        self.assertIn("../docs/04-agent-harness.md", text)
        self.assertIn("51572b91c04609b6a234418896bc43fdb64b5bd5", text)

    def test_gitbook_fragments_have_explicit_github_aliases(self):
        for key, target in WEB_SECTION_TARGETS.items():
            page, fragment = target.split("#")
            marker = "### " + WEB_SECTION_HEADINGS[key] + "\n"
            self.assertTrue(fragment.isascii())
            generated = add_web_section_anchors(marker, page[:-3])
            self.assertIn(f'<a id="{fragment}"></a>', generated)
            self.assertIn(marker, generated)

    def test_missing_heading_cannot_silently_drop_alias(self):
        with self.assertRaisesRegex(ValueError, "Missing or duplicate"):
            add_web_section_anchors("# unrelated\n", "00-reading-guide")

    def test_generated_anchor_aliases_are_present(self):
        self.assertEqual(check_web_section_anchors(ROOT / "docs"), [])

    def test_new_companion_does_not_claim_the_old_pinned_slice(self):
        soup = self.soup()
        self.assertIn('не входит в этот старый срез', soup.find(id='examples-guide').get_text())
        with tempfile.TemporaryDirectory() as directory:
            self.assertTrue(any('Learning companion: missing harness.py' in error
                                for error in check_example_catalog(soup, directory)))

    def test_missing_companion_source_boundary_is_reported(self):
        source = self.source.replace('не входит в этот старый срез', 'входит в срез')
        self.assertIn('Learning companion: missing edition-local source boundary',
                      check_example_catalog(BeautifulSoup(source, 'html.parser')))

    def test_companion_requires_full_revision(self):
        soup = self.soup()
        soup.select_one('p.learning-companion-source')['data-code-ref'] = 'main'
        self.assertIn('Learning companion: source revision must be a full SHA',
                      check_example_catalog(soup))

    def test_companion_cannot_link_to_a_different_revision(self):
        soup = self.soup()
        link = soup.select_one('p.learning-companion-source a')
        link['href'] = link['href'].replace('/tree/', '/blob/')
        self.assertIn('Learning companion: pinned source/corpus/test links differ',
                      check_example_catalog(soup))

    def test_model_profile_has_its_own_source_boundary(self):
        soup = self.soup()
        soup.select_one('p.model-profile-source').decompose()
        self.assertIn('Model profile: missing edition-local source boundary',
                      check_example_catalog(soup))

    def test_missing_model_profile_files_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            errors = check_example_catalog(self.soup(), directory)
        self.assertIn('Model profile: missing profile.py', errors)
        self.assertIn('Model profile: missing qwen3_06b_profile.json', errors)
        self.assertIn('Model profile: missing regression test', errors)

    def test_model_profile_requires_full_revision(self):
        soup = self.soup()
        soup.select_one('p.model-profile-source')['data-code-ref'] = 'main'
        self.assertIn('Model profile: source revision must be a full SHA',
                      check_example_catalog(soup))

    def test_model_profile_links_cannot_drift_from_revision(self):
        soup = self.soup()
        link = soup.select_one('p.model-profile-source a')
        link['href'] = link['href'].replace('/tree/', '/blob/')
        self.assertIn('Model profile: pinned code/passport/test links differ',
                      check_example_catalog(soup))

    def test_trace_context_has_a_separate_dependency_boundary(self):
        soup = self.soup()
        soup.select_one('p.trace-context-source').decompose()
        self.assertIn('Trace context: missing separate dependency/source boundary',
                      check_example_catalog(soup))

    def test_missing_trace_context_files_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            errors = check_example_catalog(self.soup(), directory)
        self.assertIn('Trace context: missing demo.py', errors)
        self.assertIn('Trace context: missing requirements.txt', errors)
        self.assertIn('Trace context: missing test_demo.py', errors)

    def test_trace_context_requires_full_revision(self):
        soup = self.soup()
        soup.select_one('p.trace-context-source')['data-code-ref'] = 'main'
        self.assertIn('Trace context: source revision must be a full SHA',
                      check_example_catalog(soup))

    def test_trace_context_links_cannot_drift_from_revision(self):
        soup = self.soup()
        link = soup.select_one('p.trace-context-source a')
        link['href'] = link['href'].replace('/tree/', '/blob/')
        self.assertIn('Trace context: pinned code/dependency/test links differ',
                      check_example_catalog(soup))


if __name__ == "__main__":
    unittest.main()
