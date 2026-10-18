"""Reader-entry, coverage and route regression checks (not stand tests)."""
from pathlib import Path
import tempfile
import unittest

from bs4 import BeautifulSoup
from example_catalog import check_example_catalog, examples_readme

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


if __name__ == "__main__":
    unittest.main()
