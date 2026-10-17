"""Render publication SVGs with librsvg and validate the Graphviz geometry.

The palette/bounding-box checks below are specific to our Graphviz export:
absolute M/L/C node paths and a scale(1 1) rotate(0) translate(x y) graph.
They detect missing shapes, not semantic correctness or every possible defect.
"""
from io import BytesIO
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

from PIL import Image, ImageColor

NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"


def render_svg(data, width, height=None, background=None):
    executable = os.environ.get("AX_RSVG_CONVERT") or shutil.which("rsvg-convert")
    if not executable:
        raise RuntimeError("rsvg-convert is required; install librsvg2-bin")
    command = [executable, "--format=png", f"--width={width}"]
    if height is not None:
        command.append(f"--height={height}")
    if background is not None:
        command.append(f"--background-color={background}")
    result = subprocess.run(command, input=data, capture_output=True, timeout=30, check=True)
    image = Image.open(BytesIO(result.stdout)); image.load()
    if image.width != width or (height is not None and image.height != height):
        raise ValueError("Unexpected raster dimensions")
    if image.convert("RGBA").getchannel("A").getbbox() is None:
        raise ValueError("Empty raster")
    if background == "white" and image.convert("RGBA").getchannel("A").getextrema() != (255,255):
        raise ValueError("Publication diagram must have an opaque background")
    return result.stdout


def tag(element):
    return element.tag.rsplit("}", 1)[-1]


def validate_graphviz_png(svg, png):
    root = ET.fromstring(svg)
    groups = [g for g in root.iter() if tag(g) == "g" and g.get("class") == "graph"]
    if len(groups) != 1:
        raise ValueError("Expected one Graphviz graph")
    graph = groups[0]
    transform = re.fullmatch(r"scale\(1 1\) rotate\(0\) translate\((" + NUMBER + r") (" + NUMBER + r")\)", graph.get("transform", ""))
    if transform is None:
        raise ValueError("Unsupported graph transform")
    tx, ty = map(float, transform.groups())
    vx, vy, vw, vh = map(float, root.attrib["viewBox"].split())
    image = Image.open(BytesIO(png)).convert("RGBA"); image.load()
    sx, sy = image.width / vw, image.height / vh
    expected_height = max(240, round(1800 * vh / vw))
    if image.size != (1800, expected_height):
        raise ValueError("Unexpected diagram dimensions")

    def points(element):
        key = "d" if tag(element) == "path" else "points"
        value = element.get(key, "")
        if key == "d" and re.sub(NUMBER + r"|[ ,\sMLCZ]", "", value):
            raise ValueError("Unsupported geometry path")
        numbers = list(map(float, re.findall(NUMBER, value)))
        if len(numbers) < 4 or len(numbers) % 2:
            raise ValueError("Invalid geometry coordinates")
        return [((numbers[i] + tx - vx) * sx, (numbers[i+1] + ty - vy) * sy)
                for i in range(0, len(numbers), 2)]

    def coverage(element, color):
        pairs = points(element); xs, ys = zip(*pairs)
        x0, y0 = max(0, math.floor(min(xs))-3), max(0, math.floor(min(ys))-3)
        x1, y1 = min(image.width, math.ceil(max(xs))+4), min(image.height, math.ceil(max(ys))+4)
        if x1 <= x0 or y1 <= y0:
            raise ValueError("Geometry outside raster")
        wanted = ImageColor.getrgb(color)
        count = sum(1 for pixel in image.crop((x0,y0,x1,y1)).getdata()
                    if pixel[3] >= 200 and max(abs(pixel[i]-wanted[i]) for i in range(3)) <= 10)
        return count, max(xs)-min(xs), max(ys)-min(ys)

    nodes = edges = arrowheads = 0
    for group in graph:
        kind = group.get("class")
        if kind == "node":
            shapes = [el for el in group if tag(el) in {"path", "polygon"} and el.get("fill", "none") != "none"]
            if not shapes:
                raise ValueError("Node has no filled shape")
            for shape in shapes:
                count, w, h = coverage(shape, shape.attrib["fill"])
                if count < max(4, w*h*.25):
                    raise ValueError("Missing node fill: " + (group.findtext("{*}title") or "unknown"))
            nodes += 1
        elif kind == "edge":
            for shape in group:
                if tag(shape) == "path":
                    count, w, h = coverage(shape, shape.attrib["stroke"])
                    if count < max(3, math.hypot(w,h)*.4):
                        raise ValueError("Missing edge stroke")
                    edges += 1
                elif tag(shape) == "polygon":
                    count, w, h = coverage(shape, shape.attrib["fill"])
                    if count < max(3,w*h*.2):
                        raise ValueError("Missing arrowhead")
                    arrowheads += 1
    if not nodes or not edges or not arrowheads:
        raise ValueError("Graph must contain nodes, edges and arrowheads")
    return {"nodes":nodes,"edge_paths":edges,"arrowheads":arrowheads}


def check_directory(directory):
    result = {}
    for source in sorted(Path(directory).glob("*.svg")):
        png=source.with_suffix(".png").read_bytes()
        if Image.open(BytesIO(png)).convert("RGBA").getchannel("A").getextrema() != (255,255):
            raise ValueError("Diagram must be opaque: " + source.name)
        result[source.stem] = validate_graphviz_png(source.read_bytes(), png)
    if not result:
        raise ValueError("No diagrams")
    return result


if __name__ == "__main__":
    import json, sys
    result = check_directory(sys.argv[1])
    print(json.dumps({"validated_diagrams":len(result),"geometry":result}))
