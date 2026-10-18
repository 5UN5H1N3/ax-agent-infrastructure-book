"""Derive the repository examples entry from the canonical reader guide."""
from copy import deepcopy
from pathlib import Path
import re

from markdownify import markdownify
from book_structure import ANCHORS, SLUGS

WEB_SECTION_TARGETS = {
    "examples-guide": "00-reading-guide.md#praktika-kod-i-katalog-primerov",
    "lab-dependencies": "32-labs.md#karta-zavisimostei-i-marshruty-prokhozhdeniya",
}
WEB_SECTION_HEADINGS = {
    "examples-guide": "Практика: код и каталог примеров",
    "lab-dependencies": "Карта зависимостей и маршруты прохождения",
}


def add_web_section_anchors(text, slug):
    """GitBook transliterates heading IDs; explicit IDs also work on GitHub."""
    for key, target in WEB_SECTION_TARGETS.items():
        page, fragment = target.split("#")
        if page != slug + ".md":
            continue
        marker = f"### {WEB_SECTION_HEADINGS[key]}\n"
        if text.count(marker) != 1:
            raise ValueError(f"Missing or duplicate catalog/map heading: {key}")
        text = text.replace(marker, f'<a id="{fragment}"></a>\n\n' + marker, 1)
    return text


def check_web_section_anchors(docs):
    errors = []
    for target in WEB_SECTION_TARGETS.values():
        page, fragment = target.split("#")
        path = Path(docs) / page
        if path.is_file() and f'<a id="{fragment}"></a>' not in path.read_text(encoding="utf-8"):
            errors.append(f"Missing cross-edition practice anchor: {target}")
    return errors


def examples_readme(soup):
    guide = soup.find(id="examples-guide")
    if guide is None:
        raise ValueError("Missing canonical examples guide")
    guide = deepcopy(guide)
    guide.find("h3").name = "h1"
    for heading in guide.find_all("h4"):
        heading.name = "h2"
    targets = {anchor: f"../docs/{slug}.md" for anchor, slug in zip(ANCHORS, SLUGS)}
    targets.update({key: "../docs/" + target for key, target in WEB_SECTION_TARGETS.items()})
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
        for name in ('harness.py', 'evals.py', 'cases.json', 'README.md'):
            if not (examples / 'learning_harness' / name).is_file():
                errors.append(f"Learning companion: missing {name}")
        if not (examples / 'test_ch04_learning_harness.py').is_file():
            errors.append('Learning companion: missing common regression test')
        for name in ('profile.py', 'qwen3_06b_profile.json', 'README.md'):
            if not (examples / 'model_profile' / name).is_file():
                errors.append(f'Model profile: missing {name}')
        if not (examples / 'test_ch20_model_profile.py').is_file():
            errors.append('Model profile: missing regression test')
        for name in ('demo.py', 'test_demo.py', 'requirements.txt', 'README.md'):
            if not (examples / 'trace_context' / name).is_file():
                errors.append(f'Trace context: missing {name}')
    trace_entry = guide.select_one('p.trace-context-source')
    if (trace_entry is None or 'examples/trace_context/' not in trace_entry.get_text()
            or 'отдельный' not in trace_entry.get_text()
            or 'не содержат' not in trace_entry.get_text()):
        errors.append('Trace context: missing separate dependency/source boundary')
    trace_ref = trace_entry.get('data-code-ref', '') if trace_entry else ''
    if re.fullmatch(r'[0-9a-f]{40}', trace_ref) is None:
        errors.append('Trace context: source revision must be a full SHA')
    elif [link.get('href') for link in trace_entry.find_all('a') if not link.get('href', '').startswith('#')] != [
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/tree/{trace_ref}/examples/trace_context',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{trace_ref}/examples/trace_context/requirements.txt',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{trace_ref}/examples/trace_context/test_demo.py',
    ]:
        errors.append('Trace context: pinned code/dependency/test links differ')
    profile_entry = guide.select_one('p.model-profile-source')
    if (profile_entry is None or 'examples/model_profile/' not in profile_entry.get_text()
            or '7d927c1a' not in profile_entry.get_text()
            or 'не содержат' not in profile_entry.get_text()):
        errors.append('Model profile: missing edition-local source boundary')
    profile_ref = profile_entry.get('data-code-ref', '') if profile_entry else ''
    if re.fullmatch(r'[0-9a-f]{40}', profile_ref) is None:
        errors.append('Model profile: source revision must be a full SHA')
    elif [link.get('href') for link in profile_entry.find_all('a') if not link.get('href', '').startswith('#')] != [
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/tree/{profile_ref}/examples/model_profile',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{profile_ref}/examples/model_profile/qwen3_06b_profile.json',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{profile_ref}/examples/test_ch20_model_profile.py',
    ]:
        errors.append('Model profile: pinned code/passport/test links differ')
    if 'examples/learning_harness/' not in text or 'не входит в этот старый срез' not in text:
        errors.append('Learning companion: missing edition-local source boundary')
    companion = guide.select_one('p.learning-companion-source')
    companion_ref = companion.get('data-code-ref', '') if companion else ''
    if re.fullmatch(r'[0-9a-f]{40}', companion_ref) is None:
        errors.append('Learning companion: source revision must be a full SHA')
    elif [link.get('href') for link in companion.find_all('a')] != [
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/tree/{companion_ref}/examples/learning_harness',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{companion_ref}/examples/learning_harness/cases.json',
        f'https://github.com/5UN5H1N3/ax-agent-infrastructure-book/blob/{companion_ref}/examples/test_ch04_learning_harness.py',
    ]:
        errors.append('Learning companion: pinned source/corpus/test links differ')
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
