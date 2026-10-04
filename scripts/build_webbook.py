#!/usr/bin/env python3
from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as md
from pathlib import Path
import re, shutil
import cairosvg

root = Path(__file__).resolve().parents[1]
html_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else root / "book/AX_Agent_Infrastructure_Book_RU.html"
docs = root / "docs"
assets = docs / ".gitbook/assets"
diagdir = assets / "diagrams"

if docs.exists():
    shutil.rmtree(docs)
diagdir.mkdir(parents=True, exist_ok=True)

def svg_to_png(src: Path, dst: Path, width: int):
    cairosvg.svg2png(
        bytestring=src.read_bytes(),
        write_to=str(dst),
        output_width=width,
    )

# Keep vector sources for provenance, but use PNG in published GitBook pages.
for name in ("cover.svg", "social-preview.svg", "favicon.svg"):
    shutil.copy2(root / "assets" / name, assets / name)

svg_to_png(root / "assets/cover.svg", assets / "cover.png", 1400)
svg_to_png(root / "assets/social-preview.svg", assets / "social-preview.png", 1600)
svg_to_png(root / "assets/favicon.svg", assets / "favicon.png", 512)

soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "html.parser")

slugs = [
"00-reading-guide",
"01-agent-stack-map",
"02-llm-as-compute",
"03-agent-loop-and-boundaries",
"04-agent-harness",
"05-context-state-memory-snapshot",
"06-tools-mcp-skills",
"07-multi-agent-architectures",
"08-process-container-gvisor-microvm",
"09-kubernetes-minimum",
"10-control-plane-distributed-systems",
"11-actor-model",
"12-agent-substrate-architecture",
"13-substrate-resources",
"14-suspend-snapshot-resume",
"15-substrate-networking",
"16-ax-control-plane",
"17-ax-primitives",
"18-ax-runner",
"19-ax-task-lifecycle",
"20-local-inference",
"21-inference-performance",
"22-multi-agent-on-ax",
"23-security-architecture",
"24-identity-authorization-hitl",
"25-observability",
"26-proxmox-kvm-lab",
"27-production-engineering",
"28-performance-scaling",
"29-failure-modes-troubleshooting",
"30-when-to-use-ax",
"31-antipatterns",
"32-labs",
"33-reference-multi-agent-architectures",
"34-mental-models-glossary-cheatsheets",
"35-context-engineering",
"36-mcp-2026",
"37-safe-tool-execution",
"38-ax-state-event-flow",
"39-substrate-snapshot-ownership",
"40-gateway-egress",
"41-local-inference-sizing",
"42-production-security-dr",
"43-operations-runbook",
"appendix-a-audit",
"appendix-b-production-readiness",
"appendix-c-sources-map",
]

def norm(s: str) -> str:
    return " ".join(s.split())

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

titles, fig_no = [], 0
for idx, (container, slug) in enumerate(zip(chapters, slugs)):
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
        if viewbox:
            parts = [float(x) for x in str(viewbox).replace(",", " ").split()]
            if len(parts) == 4:
                vb_w, vb_h = parts[2], parts[3]
                if not svg.get("width"):
                    svg["width"] = str(int(vb_w) if vb_w.is_integer() else vb_w)
                if not svg.get("height"):
                    svg["height"] = str(int(vb_h) if vb_h.is_integer() else vb_h)
        if not svg.get("width"):
            svg["width"] = "900"
        if not svg.get("height"):
            svg["height"] = "320"

        svg_bytes = str(svg).encode("utf-8")
        svg_path.write_bytes(svg_bytes)
        cairosvg.svg2png(
            bytestring=svg_bytes,
            write_to=str(png_path),
            output_width=1800,
        )
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
    (docs / f"{slug}.md").write_text(text, encoding="utf-8")

landing = """# AX & Agent Infrastructure

![Обложка книги](.gitbook/assets/cover.png)

**Практическое руководство по архитектуре и эксплуатации AI-агентов.**

Это веб-версия инженерной книги о современной agent infrastructure: от LLM, agent loop и harness до MCP, multi-agent orchestration, sandboxing, Agent Substrate, Google AX, local inference, security и production operations.

Книга рассчитана на системных и сетевых инженеров, SRE/DevOps, архитекторов инфраструктуры, разработчиков agentic-платформ и технических руководителей. Основная перспектива - **как систему развернуть, ограничить, наблюдать, масштабировать и диагностировать**, а не как переписывать внутренний Go-код AX.

## Базовые версии

| Компонент | Baseline |
| --- | --- |
| Google AX | `v0.3.1` / `e70162a` |
| Agent Substrate | `v0.3.0` / `ccecc78` |
| MCP | `2026-07-28` |

В тексте отдельно различаются release baseline, изменения в `main` и roadmap, чтобы планируемые возможности не выдавались за уже реализованные.

## Как читать

Для последовательного освоения начните с [руководства по чтению](00-reading-guide.md) и двигайтесь по оглавлению. Для эксплуатационной задачи можно сразу перейти к AX, Substrate, security, local inference или troubleshooting.

## Публичная веб-версия

- [Читать книгу в GitBook](https://seriousbusiness-1.gitbook.io/ax-agent-infrastructure/)

GitBook синхронизирован с каталогом `docs/` ветки `main`. Репозиторий остаётся source of truth для исходных материалов.

## Печатная версия

- [PDF](https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/main/book/AX_Agent_Infrastructure_Book_RU.pdf)
- [Исходный HTML](https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/main/book/AX_Agent_Infrastructure_Book_RU.html)
- [GitHub repository](https://github.com/5UN5H1N3/ax-agent-infrastructure-book)

> Это независимое учебно-техническое издание. Оно не является официальной документацией Google, Google AX, Agent Substrate или других упомянутых проектов.
"""
(docs / "README.md").write_text(landing, encoding="utf-8")

groups = [
("Начало", [0]),
("I. Фундамент агентных систем", list(range(1, 8))),
("II. Runtime, isolation и Agent Substrate", list(range(8, 16))),
("III. Google AX", list(range(16, 20))),
("IV. Local inference и multi-agent execution", list(range(20, 23))),
("V. Security, observability и эксплуатация", list(range(23, 32))),
("VI. Практика и reference architectures", list(range(32, 35))),
("VII. Deep dives", list(range(35, 44))),
("Приложения", list(range(44, 47))),
]

summary = ["# Summary", "", "* [Главная](README.md)", ""]
for group, indices in groups:
    summary += [f"## {group}", ""]
    for i in indices:
        summary.append(f"* [{titles[i]}]({slugs[i]}.md)")
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
