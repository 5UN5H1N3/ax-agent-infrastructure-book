"""Derive the repository examples entry from the canonical reader guide."""
from copy import deepcopy
from pathlib import Path
import re

from markdownify import markdownify
from book_structure import ANCHORS, SLUGS


def examples_readme(soup):
    guide = soup.find(id="examples-guide")
    if guide is None:
        raise ValueError("Missing canonical examples guide")
    guide = deepcopy(guide)
    guide.find("h3").name = "h1"
    for heading in guide.find_all("h4"):
        heading.name = "h2"
    targets = {anchor: f"../docs/{slug}.md" for anchor, slug in zip(ANCHORS, SLUGS)}
    targets["examples-guide"] = "../docs/00-reading-guide.md#практика-код-и-каталог-примеров"
    for link in guide.find_all("a", href=True):
        if link["href"].startswith("#") and link["href"][1:] in targets:
            link["href"] = targets[link["href"][1:]]
    body = markdownify(str(guide), heading_style="ATX", bullets="-", strip=["div", "wbr"])
    body = re.sub(r"\n{4,}", "\n\n\n", body).strip()
    return ("<!-- Generated from examples-guide in the canonical book HTML. -->\n\n"
            + body + "\n")


def check_example_catalog(soup, root=None):
    errors = []
    guide = soup.find(id="examples-guide")
    if guide is None:
        return ["Example catalog: missing canonical entry"]
    ref = guide.get("data-code-ref", "")
    if re.fullmatch(r"[0-9a-f]{40}", ref) is None:
        errors.append("Example catalog: code revision must be a full SHA")
    prefix = f"https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{ref}/examples/"
    rows = guide.select("table.example-catalog tr[data-example]")
    names = [row["data-example"] for row in rows]
    if len(names) != 39 or len(set(names)) != 39:
        errors.append("Example catalog: expected 39 unique runnable programs")
    for row in rows:
        name = row["data-example"]
        cells = row.find_all("td", recursive=False)
        if len(cells) != 3 or any(not cell.get_text(strip=True) for cell in cells):
            errors.append(f"Example catalog: incomplete purpose/mode for {name}")
            continue
        number = int(name[2:4])
        links = [a.get("href") for a in cells[0].find_all("a")]
        if links != [f"#ch-{number}", prefix+name, prefix+"test_"+name]:
            errors.append(f"Example catalog: chapter/code/test links differ for {name}")
    text = guide.get_text(" ", strip=True)
    for phrase in ("Python 3.13.5", "не заявленный минимум", "корня репозитория книги",
                   "OpenSSL CLI", "POSIX", "кодом 23", "3 failures",
                   "только явно", "не помещайте секрет в URL"):
        if phrase not in text:
            errors.append(f"Example catalog: missing launch boundary {phrase!r}")
    if root is not None:
        examples = Path(root) / "examples"
        programs = {p.name for p in examples.glob("ch*.py")}
        if programs != set(names):
            errors.append("Example catalog: runnable file inventory differs from catalog")
        for name in names:
            if not (examples / ("test_"+name)).is_file():
                errors.append(f"Example catalog: missing author test for {name}")
    lab = soup.find(id="lab-dependencies")
    if lab is None:
        errors.append("Lab map: missing dependency entry")
    else:
        lab_text = lab.get_text(" ", strip=True)
        if len(lab.select("tbody tr")) != 12:
            errors.append("Lab map: expected twelve prerequisite/output rows")
        for phrase in ("не стенд главы 26", "совокупность 7–8", "8–9", "Нет cloud account и GPU",
                       "локальная часть 11", "завершение 11", "технически не является обязательным",
                       "Интеграция AX не прошла", "частичную работу нельзя отмечать"):
            if phrase not in lab_text:
                errors.append(f"Lab map: missing route boundary {phrase!r}")
    return errors
