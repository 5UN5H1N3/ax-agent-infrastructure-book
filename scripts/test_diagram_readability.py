"""Typography and semantic-preservation guards for the A4 diagram reflow."""
from collections import Counter
from pathlib import Path
import re
import unittest

from bs4 import BeautifulSoup
from diagram_readability import MIN_FONT_PT, PRINT_WIDTH_PT, PRINT_HEIGHT_PT, svg_metrics
from rebuild_diagrams import extract_graph, readable_svg, render_svg, build_dot

ROOT = Path(__file__).resolve().parents[1]


def compact(text):
    return re.sub(r"\s+", "", text)


class DiagramReadabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        soup = BeautifulSoup((ROOT / "book/AX_Agent_Infrastructure_Book_RU.html").read_text(), "html.parser")
        cls.cases = []
        for figure in soup.select("section.chapter figure"):
            rects, edges, notes = extract_graph(figure.svg)
            caption = figure.figcaption.get_text(" ", strip=True)
            markup = readable_svg(caption, rects, edges, notes)
            cls.cases.append((rects, edges, caption, markup))

    def test_every_diagram_meets_physical_size_budget(self):
        self.assertEqual(len(self.cases), 42)
        for _, _, caption, markup in self.cases:
            with self.subTest(caption=caption):
                svg = BeautifulSoup(markup, "xml").svg
                metrics = svg_metrics(markup)
                if svg.get("data-print-min-pt"):
                    width = float(re.search(r"--diagram-width:([\d.]+)pt", svg["style"])[1])
                    scale = min(width / metrics["width"], PRINT_HEIGHT_PT / metrics["height"])
                    self.assertGreaterEqual(metrics["min_font"] * scale, MIN_FONT_PT - .001)
                    self.assertLessEqual(width, PRINT_WIDTH_PT)
                else:
                    self.assertGreaterEqual(metrics["min_print_pt"], MIN_FONT_PT)

    def test_node_labels_and_edge_topology_are_preserved(self):
        for rects, edges, caption, markup in self.cases:
            with self.subTest(caption=caption):
                svg = BeautifulSoup(markup, "xml").svg
                nodes = svg.select('g.node')
                self.assertEqual(len(nodes), len(rects))
                for node, rect in zip(nodes, rects):
                    original = ''.join(t["text"] for t in rect["texts"])
                    actual = ''.join(t.get_text() for t in node.find_all("text"))
                    self.assertEqual(compact(original), compact(actual))
                actual_edges = Counter(g.title.get_text() for g in svg.select('g.edge'))
                expected_edges = Counter(f'n{e["src"]}->n{e["dst"]}' for e in edges)
                self.assertEqual(actual_edges, expected_edges)

    def test_original_wide_ax_graph_is_detected(self):
        rects, edges, caption, _ = next(case for case in self.cases if "AX Task: фактический путь" in case[2])
        original = render_svg(build_dot(caption, rects, edges, []), caption)
        self.assertLess(svg_metrics(original)["min_print_pt"], MIN_FONT_PT)


if __name__ == "__main__":
    unittest.main()
