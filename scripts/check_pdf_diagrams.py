"""Check actual diagram text sizes in the final PDF, not just SVG dimensions.

Match each rebuilt SVG's complete text sequence beside its caption in PDF
reading order (captions may precede or follow the image). Fail closed on missing/mismatched text. This is a typography
guard, not a replacement for visual checks of arrows, overlaps or semantics.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import unicodedata

from bs4 import BeautifulSoup
from pypdf import PdfReader
from pypdf.generic import ContentStream, DictionaryObject, NameObject
from diagram_readability import MIN_FONT_PT


def compact(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def locate_diagram(text, caption, expected, offset=0):
    cap_start = text.find(caption, offset)
    if cap_start < 0:
        return None
    start = text.find(expected, cap_start + len(caption))
    if start < 0:
        start = text.rfind(expected, offset, cap_start)
    if not expected or start < 0:
        raise ValueError("Complete SVG text not found beside caption")
    end = start + len(expected)
    return start, end, max(end, cap_start + len(caption))


def inline_text_forms(page, reader):
    """Expand Form XObjects in memory so visitor matrices are page-relative.

    WeasyPrint emits some edge labels as forms. pypdf's normal visitor emits
    their inner text once at an identity matrix, then again as aggregate form
    text with the caller's stale font state. Neither is a physical font size.
    Expand only for measurement; the PDF on disk is never rewritten.
    """
    fonts, objects = DictionaryObject(), DictionaryObject()
    operations = []

    def expand(stream, resources, depth=0):
        if depth > 20:
            raise ValueError("Recursive PDF Form resources")
        font_names = {}
        for name, reference in resources.get("/Font", {}).items():
            renamed = NameObject(f"/AuditFont{len(fonts)}")
            fonts[renamed] = reference
            font_names[name] = renamed
        # Preserve encoded glyph bytes for the resource's ToUnicode map.
        for args, operator in ContentStream(stream, reader, "bytes").operations:
            if operator == b"Tf":
                operations.append(([font_names[args[0]], args[1]], operator))
            elif operator == b"Do":
                reference = resources["/XObject"][args[0]]
                obj = reference.get_object()
                if obj.get("/Subtype") == "/Form":
                    operations.append(([], b"q"))
                    if "/Matrix" in obj:
                        operations.append((list(obj["/Matrix"]), b"cm"))
                    expand(obj, obj.get("/Resources", resources), depth+1)
                    operations.append(([], b"Q"))
                else:
                    renamed = NameObject(f"/AuditObject{len(objects)}")
                    objects[renamed] = reference
                    operations.append(([renamed], operator))
            else:
                operations.append((args, operator))

    resources = page["/Resources"]
    expand(page.get_contents(), resources)
    flattened = ContentStream(None, reader, "bytes")
    flattened.operations = operations
    # Assign a new in-memory stream; replace_contents is a writer API and can
    # invalidate shared reader objects when used on an unattached page.
    page[NameObject("/Contents")] = flattened
    copied = DictionaryObject(dict(resources))
    copied[NameObject("/Font")] = fonts
    copied[NameObject("/XObject")] = objects
    page[NameObject("/Resources")] = copied
    return page


def check_pdf(pdf: Path, rebuilt_html: Path, minimum=MIN_FONT_PT):
    soup = BeautifulSoup(rebuilt_html.read_text(encoding="utf-8"), "html.parser")
    figures = soup.select("section.chapter figure")
    pages = []
    reader = PdfReader(pdf)
    for page in reader.pages:
        page = inline_text_forms(page, reader)
        spans = []
        def visitor(text, cm, tm, font, size):
            value = compact(text)
            if value:
                # Height of the text-space unit vector under Tm then CTM.
                yx = tm[2] * cm[0] + tm[3] * cm[2]
                yy = tm[2] * cm[1] + tm[3] * cm[3]
                spans.append((value, size * math.hypot(yx, yy)))
        page.extract_text(visitor_text=visitor)
        pages.append(spans)
    result = []
    cursor = 0
    cursor_offset = 0
    for number, fig in enumerate(figures, 1):
        svg = fig.find("svg")
        if svg is None:
            continue
        caption = fig.figcaption.get_text(" ", strip=True)
        expected = compact("".join(t.get_text() for t in svg.find_all("text")))
        found = None
        for index in range(cursor, len(pages)):
            spans = pages[index]
            text = "".join(value for value, _ in spans)
            try:
                location = locate_diagram(text, compact(caption), expected,
                                          cursor_offset if index == cursor else 0)
            except ValueError as error:
                raise ValueError(f"Diagram {number}, PDF page {index+1}: {error}") from error
            if location is None:
                continue
            start, end, next_offset = location
            offset = 0
            sizes = []
            for value, size in spans:
                if offset < end and offset + len(value) > start:
                    sizes.append(size)
                offset += len(value)
            found = {"diagram": number, "caption": caption, "page": index+1,
                     "min_font_pt": round(min(sizes), 4), "matched_characters": len(expected)}
            cursor = index
            cursor_offset = next_offset
            break
        if found is None:
            raise ValueError(f"Diagram {number}: caption not found in PDF")
        result.append(found)
    if len(result) != 42:
        raise ValueError(f"Expected 42 chapter diagrams, found {len(result)}")
    failures = [row for row in result if row["min_font_pt"] < minimum - .01]
    if failures:
        raise ValueError("Unreadable diagram font size: " + json.dumps(failures, ensure_ascii=False))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("rebuilt_html", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    result = check_pdf(args.pdf, args.rebuilt_html)
    record = {"minimum_required_pt": MIN_FONT_PT, "checked_diagrams": len(result),
              "minimum_observed_pt": min(row["min_font_pt"] for row in result), "diagrams": result}
    if args.json:
        args.json.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(record, ensure_ascii=False))
