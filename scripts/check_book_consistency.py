#!/usr/bin/env python3
"""Check structural links and regressions fixed by the coherence review.

These checks guard known contradictions; they are not a runtime or factual audit.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import unquote

from bs4 import BeautifulSoup
from book_structure import ANCHORS, SLUGS, GROUPS, navigation_title


def check_source(source: Path) -> list[str]:
    soup = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser")
    errors = []
    if len(soup.select("section.chapter h1")) != 47:
        errors.append("Expected 47 chapter and appendix headings")
    ids = {tag["id"] for tag in soup.find_all(id=True)}
    for link in soup.find_all("a", href=True):
        if link["href"].startswith("#") and link["href"][1:] not in ids:
            errors.append(f"Missing print anchor: {link['href']}")
    for number in range(44):
        if soup.find(id=f"ch-{number}") is None:
            errors.append(f"Missing chapter {number}")
    if any(error.startswith("Missing chapter") for error in errors):
        return errors
    text = source.read_text(encoding="utf-8")
    for obsolete in ("docs/CORE_CONCEPTS.md", "/internal/runner", "Model/Gateway",
                     "Task / Workspace / Gateway / Model",
                     "AX управляет ссылкой Task на Model",
                     "Workspace/Model bindings", "AX Tasks/Workspaces/Gateways",
                     "Workspace, Model, Gateway и Task", "связь с Workspace/Model",
                     "связать её с Workspace и Model", "AX передаёт runner стабильный URL",
                     "Actor никогда не получает secret"):
        if obsolete in text:
            errors.append(f"Obsolete resource or source reference: {obsolete}")
    checks = {
        0: ("current AX README уже описывает",),
        18: ("resume является новым boot", "process tree не переживает"),
        19: ("race между resume Task и обновлением Workspace/Model",
             "Только после этого AX удаляет Task record",
             "Observer обнаруживает readiness и исправляет stale status"),
        2: ("связывает Task с model endpoint",),
        13: ("но не ссылка на уже удалённые bytes", "после resume containers стартуют заново"),
        14: ("Fresh containers + восстановленные данные", "Data + fresh boot",
             "process стартует заново после lifecycle"),
        26: ("Task condition объясняет причину",),
        28: ("controller создаёт один", "event visible controller", "stream backlog"),
        29: ("controller не увидел event", "возраст очереди controller",
             "возраст reconcile event растёт", "Сопоставьте generation/spec revision",
             "Connect/initialize"),
        32: ("измерьте RPO:", "Lab 9. Gateway:",
             "По manifest reference AX создайте Model с credential reference"),
        40: ("Task/Actor name и UID", "для MCP over HTTP - initialize и"),
    }
    for number, phrases in checks.items():
        chapter = soup.find(id=f"ch-{number}").get_text(" ", strip=True)
        for phrase in phrases:
            if phrase in chapter:
                errors.append(f"Chapter {number}: obsolete assertion {phrase!r}")
    if "RESUME_SOURCE_GOLDEN" not in soup.find(id="ch-18").get_text():
        errors.append("Chapter 18 must explain the verified Data-on-golden path")
    registry = soup.find(id="version-profiles")
    table = registry.find_next("table") if registry is not None else None
    if table is None or len(table.select("tbody tr")) != 10:
        errors.append("Expected the complete ten-row version profile registry")
    for number in range(44):
        heading = soup.find(id=f"ch-{number}").find("h1")
        title = heading.get_text(" ", strip=True)
        toc_section = soup.select_one("section.toc")
        link = toc_section.find("a", href=f"#ch-{number}") if toc_section else None
        if link is None or link.get_text(" ", strip=True) != f"{number}. {title}":
            errors.append(f"Chapter {number}: table of contents title differs from heading")
    headings = soup.select("section.chapter h1")
    toc = soup.select_one("section.toc")
    expected = [("#" + anchor, navigation_title(i, h.get_text(" ", strip=True)))
                for i, (anchor, h) in enumerate(zip(ANCHORS, headings))]
    actual = [(a["href"], a.get_text(" ", strip=True)) for a in toc.find_all("a", href=True)] if toc else []
    if actual != expected:
        errors.append("Print contents must list all 44 chapters and three appendices in order")
    if toc is None or [x.get_text(" ", strip=True) for x in toc.select(".toc-group")] != [g for g, _ in GROUPS]:
        errors.append("Print contents groups differ from shared navigation")
    passport = soup.find(id="edition-passport")
    passport_text = passport.get_text(" ", strip=True) if passport else ""
    for phrase in ("44 нумерованные главы (0–43", "3 приложения (A–C)",
                   "42 технические схемы", "14 лабораторных работ", "не новый опубликованный релиз",
                   "Лабораторная пара не испытана"):
        if phrase not in passport_text:
            errors.append(f"Edition passport: missing or inconsistent field {phrase!r}")
    if len(soup.select("section.chapter figure svg")) != 42:
        errors.append("Edition passport requires 42 chapter diagrams, excluding the cover")
    for badge in soup.select(".badge"):
        if any(label in badge.get_text() for label in ("CURRENT MAIN VERIFIED", "IMPLEMENTED / VERIFIED", "CURRENT ARCHITECTURE")):
            errors.append("Ambiguous verification/version badge in edition")
    for short in ("e70162a", "ccecc78", "296e329", "36d6d22", "df78882", "ac23328"):
        if re.search(r"(?<![0-9a-f])" + short + r"(?![0-9a-f])", soup.get_text()):
            errors.append(f"Use eight-character version signature, not {short}")
    # These are explicit AX examples, not arbitrary application JSON/YAML.
    for block in soup.find_all("pre"):
        example = block.get_text()
        if "apiVersion: ax.io/v1alpha1" not in example:
            continue
        if re.search(r"^kind:\s*Gateway\s*$", example, re.M):
            errors.append("AX manifest example contains unsupported Gateway kind")
        if re.search(r"^\s+modelRef:", example, re.M):
            errors.append("AX manifest example contains unsupported Task modelRef")
        if re.search(r"^  (?:uid|generation|resourceVersion|observedGeneration):", example, re.M):
            errors.append("AX manifest example contains unsupported Kubernetes metadata")
    for number in (11, 12, 13, 14, 15):
        chapter = soup.find(id=f"ch-{number}")
        if "ccecc788" not in chapter.get_text():
            errors.append(f"Chapter {number}: missing Substrate implementation profile")
        for link in chapter.find_all("a", href=True):
            if re.search(r"github.com/(?:agent-substrate/substrate|google/ax)/(?:blob|tree)/main/",
                         link["href"]):
                errors.append(f"Chapter {number}: floating implementation source {link['href']}")
    chapter26 = soup.find(id="ch-26")
    for block in chapter26.find_all("pre"):
        command = block.get_text()
        if "ax apply" in command and "ax ssh" in command:
            if "ax resume task" not in command or command.index("ax resume task") > command.index("ax ssh"):
                errors.append("Chapter 26: direct-path Task must be resumed before first ssh")
    lab = soup.find(id="ch-32").get_text(" ", strip=True)
    for contract in ("672533541dbf", "UpdateTask", "spec.suspend", "не подтверждённой совместимой сборкой"):
        if contract not in lab:
            errors.append(f"Chapter 32: missing release/compatibility contract {contract}")
    return errors


def check_docs(docs: Path) -> list[str]:
    errors = []
    pages = list(docs.glob("*.md"))
    if len(pages) != 49:
        errors.append("Expected 49 Markdown files")
    for page in pages:
        # Exclude code blocks: sample Markdown there is not a publication link.
        body = re.sub(r"```[\s\S]*?```", "", page.read_text(encoding="utf-8"))
        for target in re.findall(r"\]\(([^\s)]+)\)", body):
            if re.match(r"[a-z]+:", target):
                continue
            if target.startswith("#ch-") or target == "#version-profiles":
                errors.append(f"{page.name}: unmapped print anchor {target}")
            local = unquote(target.split("#")[0])
            if local and not (page.parent / local).is_file():
                errors.append(f"{page.name}: missing local file {local}")
    guide = (docs / "00-reading-guide.md").read_text(encoding="utf-8")
    if "99 страниц" in (docs / "README.md").read_text(encoding="utf-8"):
        errors.append("Obsolete publication page count")
    if "Карта версий прикладных разделов" not in guide:
        errors.append("Missing web version registry")
    expected = []
    for index, slug in enumerate(SLUGS):
        page = docs / f"{slug}.md"
        if not page.is_file():
            errors.append(f"Missing chapter page {page.name}")
            continue
        heading = re.search(r"^# (.+)$", page.read_text(encoding="utf-8"), re.M)
        if heading:
            expected.append((navigation_title(index, heading[1]), page.name))
        else:
            errors.append(f"Missing chapter title in {page.name}")
    summary = (docs / "SUMMARY.md").read_text(encoding="utf-8")
    entries = re.findall(r"^\* \[(.+)\]\((.+)\)$", summary, re.M)
    if entries != [("Главная", "README.md")] + expected:
        errors.append("Web contents titles/numbers/order differ from chapter pages")
    if re.findall(r"^## (.+)$", summary, re.M) != [g for g, _ in GROUPS]:
        errors.append("Web contents groups differ from shared navigation")
    source = docs.parent / "book/AX_Agent_Infrastructure_Book_RU.html"
    if source.is_file():
        passport = BeautifulSoup(source.read_text(encoding="utf-8"), "html.parser").find(id="edition-passport")
        if passport:
            for landing in (docs / "README.md",):
                text = landing.read_text(encoding="utf-8")
                for row in passport.select("tbody tr"):
                    if any(cell.get_text(" ", strip=True) not in text for cell in row.find_all("td")):
                        errors.append(f"{landing}: edition passport differs from HTML")
        readme = (docs.parent / "README.md").read_text(encoding="utf-8")
        if "docs/README.md#паспорт-издания" not in readme:
            errors.append("Repository README must reference the canonical edition passport")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=Path, required=True)
    parser.add_argument("--docs", type=Path)
    args = parser.parse_args()
    errors = check_source(args.html)
    if args.docs is not None:
        errors += check_docs(args.docs)
    if errors:
        raise SystemExit("\n".join(errors))
    print("Book structure, chapter links and known coherence regressions: OK")


if __name__ == "__main__":
    main()
