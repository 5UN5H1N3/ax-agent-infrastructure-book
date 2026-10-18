"""Physical-size budget for technical diagrams in the book's A4 layout.

491.7 pt is the rounded-down inner figure width: 180 mm page content, less
two 3 mm paddings and borders. 504 pt is the 178 mm SVG height limit.
The 8 pt minimum is this edition's editorial criterion, not a standard.
"""
from __future__ import annotations

from bs4 import BeautifulSoup

PRINT_WIDTH_PT = 491.7
PRINT_HEIGHT_PT = 504.0
MIN_FONT_PT = 8.0


def svg_metrics(markup: str) -> dict:
    svg = BeautifulSoup(markup, "xml").find("svg")
    _, _, width, height = map(float, svg["viewBox"].split())
    sizes = [float(text["font-size"]) for text in svg.find_all("text")]
    if not sizes or min(width, height, *sizes) <= 0:
        raise ValueError("Diagram needs positive geometry and explicit font sizes")
    scale = min(PRINT_WIDTH_PT / width, PRINT_HEIGHT_PT / height)
    return {"width": width, "height": height, "min_font": min(sizes),
            "min_print_pt": min(sizes) * scale}
