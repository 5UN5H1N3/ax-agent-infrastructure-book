#!/usr/bin/env python3
"""Rebuild all inline technical diagrams with Graphviz.

The print source keeps semantic node/edge information as simple SVG
rectangles, lines and text.  This pass extracts that information and lets
Graphviz recalculate spacing, node sizes and edge-label placement so diagrams
remain readable in both A4 PDF and the GitBook web edition.
"""
from __future__ import annotations

import html as htmlmod
import math
import re
import subprocess
import sys
from pathlib import Path

from bs4 import BeautifulSoup


def fnum(v, default=0.0):
    try:
        return float(v)
    except Exception:
        return default


def point_rect_distance(px, py, r):
    x, y, w, h = r["x"], r["y"], r["w"], r["h"]
    dx = max(x - px, 0, px - (x + w))
    dy = max(y - py, 0, py - (y + h))
    return math.hypot(dx, dy)


def point_seg_distance(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    qx, qy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - qx, py - qy)


def esc(s):
    return htmlmod.escape(s, quote=True)


def rankdir_for(caption, rects):
    c = caption.lower()
    tb_words = (
        "карта стека", "multi-agent workflow", "observability", "учебный стенд",
        "разумным кандидатом", "инфраструктурная диагностика", "authority attenuation",
        "readiness hierarchy", "troubleshooting ladder",
    )
    lr_words = (
        "local inference", "mcp trust", "лестница изоляции", "reconciliation loop",
        "actor ≠ worker", "operational data path", "golden snapshot", "resume on demand",
        "ax task", "runner contract", "security:", "controlled action", "production-like",
        "mcp 2026", "safe tool", "control path", "state ownership", "snapshot ownership",
        "gateway validation", "tensor parallelism", "disaster recovery",
    )
    if any(w in c for w in tb_words):
        return "TB"
    if any(w in c for w in lr_words):
        return "LR"
    xs = [r["cx"] for r in rects]
    ys = [r["cy"] for r in rects]
    if not xs:
        return "LR"
    return "LR" if (max(xs)-min(xs)) > 1.25 * (max(ys)-min(ys)) else "TB"


def cluster(values, tol=55):
    groups = []
    for idx, v in sorted(enumerate(values), key=lambda z: z[1]):
        if not groups or abs(v - groups[-1]["mean"]) > tol:
            groups.append({"mean": v, "items": [idx]})
        else:
            g = groups[-1]
            g["items"].append(idx)
            g["mean"] = sum(values[i] for i in g["items"]) / len(g["items"])
    return groups


def extract_graph(svg):
    rects = []
    for i, el in enumerate(svg.find_all("rect")):
        x, y = fnum(el.get("x")), fnum(el.get("y"))
        w, h = fnum(el.get("width")), fnum(el.get("height"))
        if w < 30 or h < 20:
            continue
        rects.append({
            "id": len(rects), "x": x, "y": y, "w": w, "h": h,
            "cx": x + w/2, "cy": y + h/2,
            "fill": el.get("fill", "#f3f5f7"),
            "stroke": el.get("stroke", "#53657a"),
            "texts": [],
        })

    texts = []
    for el in svg.find_all("text"):
        txt = " ".join(el.get_text(" ", strip=True).split())
        if not txt:
            continue
        texts.append({
            "text": txt,
            "x": fnum(el.get("x")), "y": fnum(el.get("y")),
            "weight": str(el.get("font-weight", "")),
            "size": fnum(el.get("font-size"), 11),
            "assigned": False,
        })

    # Assign text to the containing card.  Allow a generous vertical margin:
    # the old diagrams often placed the final subtitle just outside the card.
    for t in texts:
        candidates = []
        for r in rects:
            if r["x"] - 8 <= t["x"] <= r["x"] + r["w"] + 8 and r["y"] - 8 <= t["y"] <= r["y"] + r["h"] + 24:
                candidates.append((abs(t["y"] - r["cy"]), r))
        if candidates:
            r = min(candidates, key=lambda z: z[0])[1]
            r["texts"].append(t)
            t["assigned"] = True

    # Clean each node's label.  Ordering by y preserves title/subtitle.
    for r in rects:
        r["texts"].sort(key=lambda t: (t["y"], t["x"]))
        # Remove accidental edge labels that happen to fall on a card.
        kept = []
        for t in r["texts"]:
            if len(t["text"]) <= 28 or t["weight"] in ("700", "bold") or t["size"] >= 12:
                kept.append(t)
            else:
                # Long subtitle lines are still legitimate when centred.
                if abs(t["x"] - r["cx"]) < r["w"] * 0.25:
                    kept.append(t)
                else:
                    t["assigned"] = False
        r["texts"] = kept

    lines = []
    for el in svg.find_all("line"):
        x1,y1,x2,y2 = map(lambda k: fnum(el.get(k)), ("x1","y1","x2","y2"))
        if not rects:
            continue
        src = min(rects, key=lambda r: point_rect_distance(x1,y1,r))
        dst = min(rects, key=lambda r: point_rect_distance(x2,y2,r))
        if src["id"] == dst["id"]:
            # fall back to centre distance for malformed endpoints
            others = [r for r in rects if r["id"] != src["id"]]
            if not others:
                continue
            dst = min(others, key=lambda r: math.hypot(x2-r["cx"], y2-r["cy"]))
        lines.append({
            "src": src["id"], "dst": dst["id"],
            "x1":x1,"y1":y1,"x2":x2,"y2":y2,
            "label":"",
        })

    leftovers = [t for t in texts if not t["assigned"]]
    notes = []
    for t in leftovers:
        if len(t["text"]) > 34:
            notes.append(t["text"])
            continue
        if lines:
            e = min(lines, key=lambda e: point_seg_distance(t["x"],t["y"],e["x1"],e["y1"],e["x2"],e["y2"]))
            d = point_seg_distance(t["x"],t["y"],e["x1"],e["y1"],e["x2"],e["y2"])
            if d < 90 and not e["label"]:
                e["label"] = t["text"]
                continue
        # Short orphan text is better represented as a note than left floating.
        notes.append(t["text"])

    return rects, lines, notes


def node_label(r):
    ts = r["texts"]
    if not ts:
        return f"Node {r['id']+1}"
    title_idx = 0
    for i,t in enumerate(ts):
        if t["weight"] in ("700","bold") or t["size"] >= 12.5:
            title_idx = i
            break
    title = ts[title_idx]["text"]
    subtitles = [t["text"] for i,t in enumerate(ts) if i != title_idx]
    # Deduplicate fragments that occasionally appear twice after old line wraps.
    clean = []
    for s in subtitles:
        if s and s not in clean and s != title:
            clean.append(s)
    parts = [f'<B>{esc(title)}</B>']
    for s in clean[:3]:
        parts.append(f'<FONT POINT-SIZE="10" COLOR="#465569">{esc(s)}</FONT>')
    return "<" + '<BR/>'.join(parts) + ">"


def build_dot(caption, rects, edges, notes):
    rankdir = rankdir_for(caption, rects)
    lines = [
        "digraph G {",
        f'graph [rankdir={rankdir}, bgcolor="transparent", pad="0.18", margin="0", '
        'nodesep="0.48", ranksep="0.68", splines="polyline", outputorder="edgesfirst"];',
        'node [shape=rect, style="rounded,filled", fontname="DejaVu Sans", fontsize=12, '
        'margin="0.20,0.14", penwidth=1.6, color="#53657a", fontcolor="#17212b"];',
        'edge [color="#60748a", penwidth=1.55, arrowsize=0.72, fontname="DejaVu Sans", '
        'fontsize=9.3, fontcolor="#34495e", labeldistance=1.8];',
    ]
    for r in rects:
        fill = r["fill"] if re.match(r"^#[0-9a-fA-F]{6}$", r["fill"]) else "#f3f5f7"
        stroke = r["stroke"] if re.match(r"^#[0-9a-fA-F]{6}$", r["stroke"]) else "#53657a"
        lines.append(f'n{r["id"]} [label={node_label(r)}, fillcolor="{fill}", color="{stroke}"];')

    # Preserve the author's semantic row/column grouping while allowing dot to
    # increase spacing enough for labels and arrows.
    vals = [r["cy"] if rankdir == "TB" else r["cx"] for r in rects]
    for g in cluster(vals):
        if len(g["items"]) > 1:
            members = "; ".join(f'n{i}' for i in g["items"])
            lines.append(f'{{ rank=same; {members}; }}')

    seen = set()
    for e in edges:
        key=(e["src"],e["dst"],e["label"])
        if key in seen:
            continue
        seen.add(key)
        label = f', label="{esc(e["label"])}"' if e["label"] else ""
        lines.append(f'n{e["src"]} -> n{e["dst"]} [minlen=1{label}];')

    if notes:
        note = " • ".join(dict.fromkeys(notes))
        # Cap very long legacy explanatory notes; the figure caption/body already
        # carries the full explanation and a huge line hurts diagram readability.
        if len(note) > 150:
            note = note[:147].rstrip() + "..."
        lines.append(f'note [shape=plain, fontsize=9.5, fontcolor="#5d6c7b", label="{esc(note)}"];')
        lines.append('{ rank=sink; note; }')

    lines.append("}")
    return "\n".join(lines)


def render_svg(dot, caption):
    p = subprocess.run(["dot", "-Tsvg"], input=dot.encode("utf-8"), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    raw = p.stdout.decode("utf-8")
    m = re.search(r"(<svg[\s\S]*</svg>)", raw)
    if not m:
        raise RuntimeError("Graphviz produced no SVG")
    soup = BeautifulSoup(m.group(1), "xml")
    svg = soup.find("svg")
    svg["class"] = "diagram diagram-redrawn"
    svg["role"] = "img"
    svg["aria-label"] = caption
    # Responsive inline SVG: viewBox is authoritative; width/height attributes
    # from Graphviz are unnecessary and sometimes cause clipping in GitBook.
    svg.attrs.pop("width", None)
    svg.attrs.pop("height", None)
    # Keep a little additional breathing room around the graph.
    style = svg.get("style", "")
    svg["style"] = (style + ";width:100%;height:auto;overflow:visible").lstrip(";")
    return str(svg)


def rebuild_html(src: Path, dst: Path):
    soup = BeautifulSoup(src.read_text(encoding="utf-8"), "html.parser")
    figures = soup.find_all("figure")
    rebuilt = 0
    for fig in figures:
        svg = fig.find("svg", class_="diagram")
        if not svg:
            continue
        cap_el = fig.find("figcaption")
        caption = " ".join(cap_el.get_text(" ", strip=True).split()) if cap_el else "Technical diagram"
        rects, edges, notes = extract_graph(svg)
        if len(rects) < 2:
            continue
        dot = build_dot(caption, rects, edges, notes)
        new_svg_markup = render_svg(dot, caption)
        new_svg = BeautifulSoup(new_svg_markup, "xml").find("svg")
        svg.replace_with(BeautifulSoup(str(new_svg), "html.parser").find("svg"))
        rebuilt += 1

    # Global figure rules improve PDF and web rendering.
    style = soup.find("style")
    if style:
        style.append("""
figure { break-inside: avoid-page !important; margin: 5mm 0 6mm; }
figure svg.diagram-redrawn {
  display:block;
  width:auto !important;
  max-width:100% !important;
  height:auto !important;
  max-height:178mm !important;
  margin:2mm auto;
}
figcaption { margin-bottom:2.5mm; break-after:avoid-page; }
""")
    dst.write_text(str(soup), encoding="utf-8")
    print(f"Rebuilt {rebuilt} diagrams")


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: rebuild_diagrams.py INPUT.html OUTPUT.html")
    rebuild_html(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
