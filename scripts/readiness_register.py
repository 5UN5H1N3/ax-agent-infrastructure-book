"""Export the unexecuted read-only review plan from the canonical book HTML.

This is a template exporter, not a readiness decision engine or a test runner.
Criterion names come from B.2-B.7, while profile/case plans live in B.8.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]


def build_register(soup):
    start = soup.find(id="appendix-b")
    if start is None:
        raise ValueError("Missing appendix B")
    headings = []
    for sibling in start.next_siblings:
        if getattr(sibling, "name", None) == "h1":
            break
        if getattr(sibling, "name", None) == "h3":
            match = re.fullmatch(r"([PSHROG][1-9][0-9]*)\. (.+)", sibling.get_text(" ", strip=True))
            if match:
                headings.append(match.groups())
    ids = [key for key, _ in headings]
    if len(ids) != 25 or len(set(ids)) != 25:
        raise ValueError("Expected 25 unique canonical readiness criteria")
    plan = soup.find(id="readiness-readonly-register")
    if plan is None:
        raise ValueError("Missing complete read-only register")
    rows = plan.select("tr[data-criterion]")
    if [row['data-criterion'] for row in rows] != ids:
        raise ValueError("Read-only register must cover canonical criteria once and in order")
    fields = {cell['data-profile-field']: cell.get_text(" ", strip=True)
              for cell in plan.select('[data-profile-field]')}
    if set(fields) != {'scope', 'rights', 'limits', 'recovery', 'canary'} or not all(fields.values()):
        raise ValueError("Missing or incomplete read-only profile boundary")
    records = []
    case_ids = []
    for row, (key, title) in zip(rows, headings):
        cells = row.find_all('td', recursive=False)
        if len(cells) != 4 or any(not c.get_text(strip=True) for c in cells):
            raise ValueError(f"{key}: incomplete applicability, cases or owner")
        applicability = row.get('data-applicability')
        if applicability not in {'required', 'partial', 'proposed_NA'}:
            raise ValueError(f"{key}: invalid applicability")
        cases = []
        for item in cells[2].select('li[data-case]'):
            case_id = item['data-case']
            stage = item.get('data-stage')
            if not re.fullmatch(re.escape(key)+r'-[a-z][a-z0-9-]*', case_id):
                raise ValueError(f"{key}: invalid case ID")
            if stage not in {'pre_canary', 'before_expand'}:
                raise ValueError(f"{case_id}: missing or invalid stage")
            description = item.get_text(' ', strip=True)
            if not description:
                raise ValueError(f"{case_id}: missing expected observation")
            cases.append({'id': case_id, 'stage': stage, 'required': True,
                          'expected': description, 'status': 'SKIPPED',
                          'reason': 'План проверки; выполнение не зарегистрировано.', 'evidence': []})
            case_ids.append(case_id)
        support = row.get('data-support-cases', '').split()
        if applicability == 'proposed_NA':
            if cases or not support:
                raise ValueError(f"{key}: proposed N/A needs support cases and no fabricated result")
        elif not cases:
            raise ValueError(f"{key}: applicable criterion needs cases")
        records.append({'id': key, 'title': title, 'applicability': applicability,
                        'applicability_reason': cells[1].get_text(' ', strip=True),
                        'applicability_approved_by': None, 'supporting_case_ids': support,
                        'owner_role': cells[3].get_text(' ', strip=True), 'assignee': None,
                        'cases': cases, 'criterion_closed': False,
                        'decision_basis': 'Нет выполненных обязательных cases и согласованного основания закрытия.'})
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("Duplicate readiness case IDs")
    for record in records:
        if any(key not in case_ids for key in record['supporting_case_ids']):
            raise ValueError(f"{record['id']}: unknown supporting case")
    return {'schema_version': 1, 'profile_id': 'network-readonly-plan',
            'evidence_scope': 'unexecuted_plan', 'phase': 'pre_canary',
            'candidate_inventory': None, 'release_owner': None, 'profile': fields,
            'criteria': records, 'decision': 'HOLD',
            'decision_basis': 'Нужны inventory, назначенные люди и результаты обязательных cases текущего этапа.',
            'canary_observed': False, 'production_ready': False}


def serialize(register):
    return json.dumps(register, ensure_ascii=False, indent=2) + '\n'


def check_register(soup, exported=None):
    try:
        register = build_register(soup)
    except ValueError as error:
        return [f'Readiness register: {error}']
    if exported is not None and (not exported.is_file() or exported.read_text() != serialize(register)):
        return ['Readiness register: generated JSON differs from canonical HTML']
    return []


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--html', type=Path, default=ROOT/'book/AX_Agent_Infrastructure_Book_RU.html')
    parser.add_argument('--output', type=Path, help='Create a new editable JSON file; never overwrite an existing review')
    args = parser.parse_args()
    soup = BeautifulSoup(args.html.read_text(), 'html.parser')
    result = serialize(build_register(soup))
    if args.output:
        with args.output.open('x', encoding='utf-8') as stream:
            stream.write(result)
    else:
        print(result, end='')
