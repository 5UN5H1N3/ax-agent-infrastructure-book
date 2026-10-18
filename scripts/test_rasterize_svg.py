"""Regression guards: a non-empty PNG can still lose nodes and arrows."""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from PIL import Image
import rasterize_svg as raster

ROOT=Path(__file__).resolve().parents[1]


class RasterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.svg=(ROOT/'docs/.gitbook/assets/diagrams/36-32.svg').read_bytes()
        # The published composition may be reflowed for print readability.
        # Exercise its actual aspect ratio; 282 px described an obsolete SVG.
        _,_,width,height=map(float,ET.fromstring(cls.svg).attrib['viewBox'].split())
        cls.width=1800
        cls.height=max(240,round(cls.width*height/width))
        cls.png=raster.render_svg(cls.svg,cls.width,cls.height)

    def changed(self, remove):
        root=ET.fromstring(self.svg)
        for parent in root.iter():
            for child in list(parent):
                if remove(parent,child):parent.remove(child)
        return raster.render_svg(ET.tostring(root),self.width,self.height)

    def test_complete_geometry(self):
        self.assertEqual(raster.validate_graphviz_png(self.svg,self.png),{'nodes':6,'edge_paths':6,'arrowheads':6})

    def test_deterministic_repeat(self):
        self.assertEqual(self.png,raster.render_svg(self.svg,self.width,self.height))

    def test_white_background_is_opaque(self):
        png=raster.render_svg(self.svg,self.width,self.height,background='white')
        image=Image.open(BytesIO(png)).convert('RGBA')
        self.assertEqual(image.getchannel('A').getextrema(),(255,255))
        self.assertEqual(image.getpixel((0,0)),(255,255,255,255))

    def test_missing_node_even_when_text_remains(self):
        png=self.changed(lambda p,c:p.get('class')=='node' and raster.tag(c)=='path')
        with self.assertRaisesRegex(ValueError,'Missing node fill'):
            raster.validate_graphviz_png(self.svg,png)

    def test_one_missing_node(self):
        seen=[]
        def remove(p,c):
            if not seen and p.get('class')=='node' and raster.tag(c)=='path':
                seen.append(True);return True
            return False
        with self.assertRaisesRegex(ValueError,'Missing node fill'):
            raster.validate_graphviz_png(self.svg,self.changed(remove))

    def test_missing_edge_paths(self):
        png=self.changed(lambda p,c:p.get('class')=='edge' and raster.tag(c)=='path')
        with self.assertRaisesRegex(ValueError,'Missing edge stroke'):
            raster.validate_graphviz_png(self.svg,png)

    def test_text_only_not_success(self):
        png=self.changed(lambda p,c:raster.tag(c) in {'path','polygon'})
        with self.assertRaises(ValueError):raster.validate_graphviz_png(self.svg,png)

    def test_missing_arrowheads(self):
        png=self.changed(lambda p,c:p.get('class')=='edge' and raster.tag(c)=='polygon')
        with self.assertRaisesRegex(ValueError,'Missing arrowhead'):
            raster.validate_graphviz_png(self.svg,png)

    def test_unsupported_path_is_not_silently_ignored(self):
        root=ET.fromstring(self.svg)
        next(e for e in root.iter() if raster.tag(e)=='path').set('d','m0,0 l1,1')
        with self.assertRaisesRegex(ValueError,'Unsupported geometry path'):
            raster.validate_graphviz_png(ET.tostring(root),self.png)

    def test_wrong_dimensions(self):
        png=raster.render_svg(self.svg,900,141)
        with self.assertRaisesRegex(ValueError,'dimensions'):raster.validate_graphviz_png(self.svg,png)

    def test_empty_image_rejected(self):
        empty=b'<svg xmlns="http://www.w3.org/2000/svg" width="20" height="20"/>'
        with self.assertRaisesRegex(ValueError,'Empty raster'):raster.render_svg(empty,20,20)

    def test_missing_renderer_does_not_silently_fallback(self):
        with patch.dict(raster.os.environ,{},clear=True),patch.object(raster.shutil,'which',return_value=None):
            with self.assertRaisesRegex(RuntimeError,'librsvg2-bin'):raster.render_svg(self.svg,1800,282)

    def test_renderer_failure_propagates(self):
        with patch.object(raster.subprocess,'run',side_effect=subprocess.CalledProcessError(1,'renderer')):
            with self.assertRaises(subprocess.CalledProcessError):raster.render_svg(self.svg,1800,282)

    def test_timeout_propagates(self):
        with patch.object(raster.subprocess,'run',side_effect=subprocess.TimeoutExpired('renderer',30)):
            with self.assertRaises(subprocess.TimeoutExpired):raster.render_svg(self.svg,1800,282)

    def test_unsupported_transform_rejected(self):
        root=ET.fromstring(self.svg)
        next(g for g in root.iter() if g.get('class')=='graph').set('transform','rotate(45)')
        with self.assertRaisesRegex(ValueError,'transform'):raster.validate_graphviz_png(ET.tostring(root),self.png)

    def test_empty_directory_is_failure(self):
        import tempfile
        with tempfile.TemporaryDirectory() as path:
            with self.assertRaisesRegex(ValueError,'No diagrams'):raster.check_directory(path)


if __name__=='__main__':unittest.main()
