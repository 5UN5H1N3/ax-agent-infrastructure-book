"""Verify physical font measurement through scaled and nested PDF forms."""
from io import BytesIO
import math
import unittest

from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject
from check_pdf_diagrams import inline_text_forms, locate_diagram


class PdfDiagramMeasurementTests(unittest.TestCase):
    def measure(self, nested=False, unicode_label=False):
        writer = PdfWriter()
        page = writer.add_blank_page(600, 800)
        font = writer._add_object(DictionaryObject({
            NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica")}))
        if unicode_label:
            mapping = DecodedStreamObject()
            mapping.set_data(b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap "
                             b"/CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange "
                             b"1 beginbfchar <80> <042F> endbfchar endcmap end end")
            font.get_object()[NameObject("/ToUnicode")] = writer._add_object(mapping)
        resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F"): font})})
        form = DecodedStreamObject()
        label = b"<80>" if unicode_label else b"(edge)"
        form.set_data(b"BT /F 14 Tf 1 0 0 1 10 10 Tm " + label + b" Tj ET")
        form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                     NameObject("/BBox"): ArrayObject([NumberObject(x) for x in (0, 0, 100, 100)]),
                     NameObject("/Resources"): resources})
        reference = writer._add_object(form)
        if nested:
            outer = DecodedStreamObject()
            outer.set_data(b"q .8 0 0 .8 0 0 cm /Child Do Q")
            outer.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                          NameObject("/BBox"): form["/BBox"],
                          NameObject("/Resources"): DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Child"): reference})})})
            reference = writer._add_object(outer)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): resources["/Font"],
            NameObject("/XObject"): DictionaryObject({NameObject("/Diagram"): reference})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F 10 Tf (caption) Tj ET q .5 0 0 .5 0 0 cm /Diagram Do Q")
        page[NameObject("/Contents")] = writer._add_object(stream)
        buffer = BytesIO(); writer.write(buffer); buffer.seek(0)
        reader = PdfReader(buffer)
        page = inline_text_forms(reader.pages[0], reader)
        spans = []
        def visitor(text, cm, tm, font, size):
            if text.strip():
                spans.append((text.strip(), size * math.hypot(cm[2], cm[3])))
        page.extract_text(visitor_text=visitor)
        return spans

    def test_scaled_form_uses_its_font_and_parent_transform_once(self):
        self.assertEqual(self.measure(), [("caption", 10), ("edge", 7)])

    def test_nested_form_scales_are_composed(self):
        spans = self.measure(nested=True)
        self.assertEqual([text for text, _ in spans], ["caption", "edge"])
        self.assertAlmostEqual(spans[1][1], 5.6)

    def test_encoded_glyphs_keep_their_to_unicode_mapping(self):
        self.assertEqual(self.measure(unicode_label=True), [("caption", 10), ("Я", 7)])

    def test_caption_can_follow_image(self):
        self.assertEqual(locate_diagram("graphcaptionbody", "caption", "graph"), (0, 5, 12))

    def test_identical_next_figure_cannot_reuse_previous_measurement(self):
        text = "captiongraphbodycaptiongraph"
        first = locate_diagram(text, "caption", "graph")
        second = locate_diagram(text, "caption", "graph", first[2])
        self.assertEqual(first, (7, 12, 12))
        self.assertEqual(second, (23, 28, 28))
        self.assertIsNone(locate_diagram(text, "caption", "graph", second[2]))


if __name__ == "__main__":
    unittest.main()
