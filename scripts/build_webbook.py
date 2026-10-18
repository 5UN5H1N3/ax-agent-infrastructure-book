#!/usr/bin/env python3
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md
from pathlib import Path
import re, shutil, sys
from rasterize_svg import render_svg, validate_graphviz_png
from book_structure import SLUGS, GROUPS, ANCHORS, navigation_title, passport_markdown
from example_catalog import examples_readme, WEB_SECTION_TARGETS, add_web_section_anchors

root = Path(__file__).resolve().parents[1]
html_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "book/AX_Agent_Infrastructure_Book_RU.html"
docs = root / "docs"
assets = docs / ".gitbook/assets"
diagdir = assets / "diagrams"

if not html_path.is_file():
    raise SystemExit(f"HTML source not found: {html_path}")

if docs.exists():
    shutil.rmtree(docs)
diagdir.mkdir(parents=True, exist_ok=True)

def svg_to_png(src: Path, dst: Path, width: int):
    dst.write_bytes(render_svg(src.read_bytes(), width))

# Canonical publication artwork is the original approved raster artwork.
# Do not regenerate cover/social-preview from the later SVG approximations.
shutil.copy2(root / "assets/cover.png", assets / "cover.png")
shutil.copy2(root / "assets/social-preview.png", assets / "social-preview.png")
shutil.copy2(root / "assets/favicon.svg", assets / "favicon.svg")
svg_to_png(root / "assets/favicon.svg", assets / "favicon.png", 512)

soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

slugs = SLUGS

def norm(s: str) -> str:
    return " ".join(s.split())

def rewrite_internal_links(container, targets):
    """Map print chapter anchors to files that GitBook can resolve."""
    for link in container.find_all("a", href=True):
        href = link["href"]
        if href.startswith("#") and href[1:] in targets:
            link["href"] = targets[href[1:]]

def split_section(section):
    groups, current = [], []
    for child in list(section.children):
        if isinstance(child, Tag) and child.name == "h1" and current:
            groups.append(current)
            current = []
        current.append(child)
    if current:
        groups.append(current)
    return [g for g in groups if any(isinstance(x, Tag) and x.name == "h1" for x in g)]

def make_container(nodes):
    tmp = BeautifulSoup("<div></div>", "html.parser")
    div = tmp.div
    for node in nodes:
        div.append(node.__copy__() if hasattr(node, "__copy__") else node)
    return div

chapters = []
for sec in soup.find_all("section", class_="chapter"):
    for group in split_section(sec):
        chapters.append(make_container(group))

if len(chapters) != len(slugs):
    raise SystemExit(f"Unexpected page count: {len(chapters)} != {len(slugs)}")

chapter_targets = {f"ch-{i}": f"{slug}.md" for i, slug in enumerate(slugs[:44])}
chapter_targets.update({
    "appendix": "appendix-a-audit.md",
    "appendix-b": "appendix-b-production-readiness.md",
    "appendix-c": "appendix-c-sources-map.md",
    "version-profiles": "00-reading-guide.md",
    **WEB_SECTION_TARGETS,
})

titles, fig_no = [], 0
for idx, (container, slug) in enumerate(zip(chapters, slugs)):
    rewrite_internal_links(container, chapter_targets)
    for x in container.select(".chapter-num"):
        x.decompose()

    for badges in container.select(".badges"):
        values = [norm(x.get_text(" ", strip=True)) for x in badges.select(".badge")]
        p = soup.new_tag("p")
        strong = soup.new_tag("strong")
        strong.string = "Статус: "
        p.append(strong)
        p.append(" · ".join(f"`{v}`" for v in values))
        badges.replace_with(p)

    for src in container.select(".sources"):
        lead = src.find("strong")
        if lead:
            h = soup.new_tag("h3")
            h.string = norm(lead.get_text(" ", strip=True))
            lead.extract()
            src.insert_before(h)
        src.unwrap()

    for fig in list(container.find_all("figure")):
        svg = fig.find("svg")
        if not svg:
            continue
        fig_no += 1
        cap = fig.find("figcaption")
        caption = norm(cap.get_text(" ", strip=True)) if cap else f"Схема {fig_no}"
        stem = f"{idx:02d}-{fig_no:02d}"
        svg_path = diagdir / f"{stem}.svg"
        png_path = diagdir / f"{stem}.png"

        # Inline SVG from the print source may rely on CSS/layout and therefore
        # omit intrinsic dimensions. GitBook/CDN and CairoSVG both behave more
        # reliably when standalone SVG assets have xmlns + explicit dimensions.
        svg["xmlns"] = "http://www.w3.org/2000/svg"
        viewbox = svg.get("viewBox") or svg.get("viewbox")
        vb_w, vb_h = 900.0, 320.0
        if viewbox:
            try:
                parts = [float(x) for x in re.split(r"[\s,]+", str(viewbox).strip())]
            except ValueError:
                parts = []
            if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                vb_w, vb_h = parts[2], parts[3]
        svg["viewBox"] = f"0 0 {vb_w:g} {vb_h:g}"
        svg.attrs.pop("viewbox", None)
        # Always replace inherited/percentage dimensions with absolute intrinsic
        # dimensions. CairoSVG cannot resolve a standalone width="100%" SVG.
        svg["width"] = str(int(vb_w) if vb_w.is_integer() else vb_w)
        svg["height"] = str(int(vb_h) if vb_h.is_integer() else vb_h)
        # Graphviz emits width:100%;height:auto on the root SVG.  CairoSVG
        # treats the unresolved auto height as zero for standalone assets,
        # producing a fully transparent PNG.  Intrinsic dimensions above are
        # authoritative, so remove the conflicting inline style before render.
        svg.attrs.pop("style", None)

        svg_bytes = str(svg).encode("utf-8")
        svg_path.write_bytes(svg_bytes)
        out_w = 1800
        out_h = max(240, int(round(out_w * vb_h / vb_w)))
        # The diagrams use a light palette, just like the PDF.  Baking in the
        # page background keeps labels/edges readable in dark GitBook themes
        # and transparent-image previews.  Branding retains its own alpha.
        png_bytes = render_svg(svg_bytes, out_w, out_h, background="white")
        if svg.find("g", class_="graph"):
            validate_graphviz_png(svg_bytes, png_bytes)
        png_path.write_bytes(png_bytes)
        p = soup.new_tag("p")
        im = soup.new_tag("img", src=f".gitbook/assets/diagrams/{stem}.png", alt=caption)
        p.append(im)
        p.append(soup.new_tag("br"))
        em = soup.new_tag("em")
        em.string = caption
        p.append(em)
        fig.replace_with(p)

    for tag in container.find_all(True):
        tag.attrs.pop("style", None)

    h1 = container.find("h1")
    title = norm(h1.get_text(" ", strip=True))
    titles.append(title)

    text = md(str(container), heading_style="ATX", bullets="-", strip=["div", "section", "span"])
    text = re.sub(r"\n{4,}", "\n\n\n", text).strip() + "\n"
    text = add_web_section_anchors(text, slug)
    (docs / f"{slug}.md").write_text(text, encoding="utf-8")

landing = """# AX & Agent Infrastructure

![Обложка книги](.gitbook/assets/cover.png)

**Практическое руководство по архитектуре и эксплуатации AI-агентов.**

Это веб-версия инженерной книги о современной agent infrastructure: от LLM, agent loop и harness до MCP, multi-agent orchestration, sandboxing, Agent Substrate, Google AX, local inference, security и production operations.

Книга рассчитана на системных и сетевых инженеров, SRE/DevOps, архитекторов инфраструктуры, разработчиков agentic-платформ и технических руководителей. Основная перспектива - **как систему развернуть, ограничить, наблюдать, масштабировать и диагностировать**, а не как переписывать внутренний Go-код AX.

{edition_passport}

## Как читать

Для последовательного освоения начните с [руководства по чтению](00-reading-guide.md) и двигайтесь по оглавлению. Для эксплуатационной задачи можно сразу перейти к AX, Substrate, security, local inference или troubleshooting.

## Практика

- [Начать с локального примера и выбрать программу из каталога](00-reading-guide.md#praktika-kod-i-katalog-primerov): фиксированный код, окружение, воздействие и авторские тесты всех 39 программ.
- [Карта зависимостей лабораторных](32-labs.md#karta-zavisimostei-i-marshruty-prokhozhdeniya): CPU вместо cloud, необязательный GPU и условия перехода к AX-сценариям.

## Публичная веб-версия

- [Читать книгу в GitBook](https://romandocs.gitbook.io/ax-agent-infrastructure/)

GitBook Git Sync настроен на каталог `docs/` ветки `main`. Публичная версия отражает опубликованную ветку, а не неопубликованные рабочие изменения. Репозиторий остаётся источником материалов.

## Печатная версия

- [PDF](https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/main/book/AX_Agent_Infrastructure_Book_RU.pdf)
- [Исходный HTML](https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/main/book/AX_Agent_Infrastructure_Book_RU.html)
- [GitHub repository](https://github.com/5UN5H1N3/ax-agent-infrastructure-book)

> Это независимое учебно-техническое издание. Оно не является официальной документацией Google, Google AX, Agent Substrate или других упомянутых проектов.
"""
passport = make_container([soup.find(id="edition-passport")])
rewrite_internal_links(passport, chapter_targets)
passport_text = passport_markdown(passport, md)
landing = landing.replace("{edition_passport}", passport_text)
(docs / "README.md").write_text(landing, encoding="utf-8")
(root / "examples/README.md").write_text(examples_readme(soup), encoding="utf-8")

groups = GROUPS

summary = ["# Summary", "", "* [Главная](README.md)", ""]
for group, indices in groups:
    summary += [f"## {group}", ""]
    for i in indices:
        summary.append(f"* [{navigation_title(i, titles[i])}]({slugs[i]}.md)")
    summary.append("")
(docs / "SUMMARY.md").write_text("\n".join(summary).rstrip() + "\n", encoding="utf-8")

# Site-level Git Sync manifest. Keep this generated so rebuilding docs never removes it.
manifest = """$schema: https://api.gitbook.com/gitbook-docs.yaml

site:
  title: AX & Agent Infrastructure
  structure:
    - type: space
      key: ax-agent-infrastructure
      title: AX & Agent Infrastructure
      path: ax-agent-infrastructure
      default: true
      content:
        directory: ./
        language: ru
"""
(docs / "gitbook-docs.yaml").write_text(manifest, encoding="utf-8")

print(f"Generated {len(chapters)} Markdown pages and {fig_no} diagrams in SVG+PNG.")
