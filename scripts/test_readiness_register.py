"""Guards for the plan/template, not proof of deployment readiness."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from bs4 import BeautifulSoup
from readiness_register import build_register, check_register, serialize

ROOT = Path(__file__).resolve().parents[1]


class ReadinessRegisterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        text = (ROOT/'book/AX_Agent_Infrastructure_Book_RU.html').read_text()
        start = text.index('<h1 id="appendix-b"')
        end = text.index('<h1 id="appendix-c"', start)
        cls.fixture = text[start:end]

    def setUp(self):
        self.soup = BeautifulSoup(self.fixture, 'html.parser')

    def test_complete_unexecuted_plan(self):
        result = build_register(self.soup)
        self.assertEqual(len(result['criteria']), 25)
        cases = [case for row in result['criteria'] for case in row['cases']]
        self.assertEqual(len(cases), 33)
        self.assertTrue(all(c['status']=='SKIPPED' and c['evidence']==[] and c['required'] for c in cases))
        self.assertTrue(all(not r['criterion_closed'] and r['assignee'] is None for r in result['criteria']))
        self.assertEqual(result['decision'], 'HOLD')
        self.assertIsNone(result['candidate_inventory'])
        self.assertIsNone(result['release_owner'])
        self.assertFalse(result['production_ready'])
        self.assertFalse(result['canary_observed'])

    def test_titles_come_from_criterion_definitions(self):
        heading = next(h for h in self.soup.find_all('h3') if h.get_text().startswith('P1.'))
        heading.string = 'P1. New canonical wording'
        self.assertEqual(build_register(self.soup)['criteria'][0]['title'], 'New canonical wording')

    def test_missing_and_duplicate_definitions_rejected(self):
        for duplicate in (False, True):
            soup = copy.deepcopy(self.soup)
            heading = next(h for h in soup.find_all('h3') if h.get_text().startswith('P1.'))
            if duplicate:
                heading.insert_after(copy.copy(heading))
            else:
                heading.decompose()
            with self.subTest(duplicate=duplicate), self.assertRaisesRegex(ValueError, 'canonical readiness'):
                build_register(soup)

    def test_missing_duplicate_or_reordered_rows_rejected(self):
        for mode in ('missing','duplicate','reordered'):
            soup=copy.deepcopy(self.soup);row=soup.select_one('tr[data-criterion="P1"]')
            if mode=='missing':row.decompose()
            elif mode=='duplicate':row.insert_after(copy.copy(row))
            else:row.find_next_sibling('tr').insert_after(row.extract())
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'once and in order'):
                build_register(soup)

    def test_missing_reason_or_owner_rejected(self):
        for index in (1,3):
            soup=copy.deepcopy(self.soup)
            soup.select_one('tr[data-criterion="P1"]').find_all('td',recursive=False)[index].clear()
            with self.subTest(index=index),self.assertRaisesRegex(ValueError,'incomplete'):
                build_register(soup)

    def test_unknown_applicability_rejected(self):
        self.soup.select_one('tr[data-criterion="P1"]')['data-applicability']='PASS'
        with self.assertRaisesRegex(ValueError,'invalid applicability'):build_register(self.soup)

    def test_required_criterion_cannot_lose_cases(self):
        self.soup.select_one('tr[data-criterion="P1"] ul').replace_with('No test')
        with self.assertRaisesRegex(ValueError,'needs cases'):build_register(self.soup)

    def test_duplicate_case_rejected(self):
        item=self.soup.select_one('li[data-case="P1-inventory"]')
        item.insert_after(copy.copy(item))
        with self.assertRaisesRegex(ValueError,'Duplicate readiness case'):build_register(self.soup)

    def test_case_cannot_change_criterion(self):
        self.soup.select_one('li[data-case="P1-inventory"]')['data-case']='S1-inventory'
        with self.assertRaisesRegex(ValueError,'invalid case ID'):build_register(self.soup)

    def test_unknown_or_absent_stage_rejected(self):
        for stage in ('rollout',None):
            soup=copy.deepcopy(self.soup);item=soup.select_one('li[data-case="G2-observe"]')
            if stage is None:del item['data-stage']
            else:item['data-stage']=stage
            with self.subTest(stage=stage),self.assertRaisesRegex(ValueError,'stage'):build_register(soup)

    def test_canary_plan_is_not_observation(self):
        result=build_register(self.soup)
        g2=next(r for r in result['criteria'] if r['id']=='G2')
        self.assertEqual([(c['id'],c['stage']) for c in g2['cases']],
                         [('G2-plan','pre_canary'),('G2-observe','before_expand')])
        self.assertFalse(g2['criterion_closed'])

    def test_proposed_na_is_not_approved_or_pass(self):
        h4=next(r for r in build_register(self.soup)['criteria'] if r['id']=='H4')
        self.assertEqual(h4['applicability'],'proposed_NA')
        self.assertIsNone(h4['applicability_approved_by'])
        self.assertEqual(h4['cases'],[])
        self.assertEqual(h4['supporting_case_ids'],['P1-inventory','S5-no-write'])
        self.assertFalse(h4['criterion_closed'])

    def test_na_requires_existing_support(self):
        for value in ('','S5-absent'):
            soup=copy.deepcopy(self.soup);soup.select_one('tr[data-criterion="H4"]')['data-support-cases']=value
            with self.subTest(value=value),self.assertRaisesRegex(ValueError,'support'):build_register(soup)

    def test_na_cannot_have_fabricated_test_result(self):
        h4=self.soup.select_one('tr[data-criterion="H4"]')
        h4.find_all('td',recursive=False)[2].append(BeautifulSoup('<li data-case="H4-result" data-stage="pre_canary">PASS</li>','html.parser'))
        with self.assertRaisesRegex(ValueError,'fabricated'):build_register(self.soup)

    def test_profile_fields_required(self):
        self.soup.select_one('[data-profile-field="rights"]').decompose()
        with self.assertRaisesRegex(ValueError,'profile boundary'):build_register(self.soup)

    def test_export_freshness(self):
        self.assertEqual(check_register(self.soup,ROOT/'book/readiness-readonly-register.json'),[])
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'register.json'
            self.assertIn('differs',check_register(self.soup,path)[0])
            altered=build_register(self.soup);altered['decision']='READY'
            path.write_text(serialize(altered))
            self.assertIn('differs',check_register(self.soup,path)[0])

    def test_cli_creates_template_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'review.json'
            command=[sys.executable,str(ROOT/'scripts/readiness_register.py'),'--output',str(path)]
            first=subprocess.run(command,capture_output=True,text=True)
            self.assertEqual(first.returncode,0,first.stderr)
            original=path.read_bytes()
            second=subprocess.run(command,capture_output=True,text=True)
            self.assertNotEqual(second.returncode,0)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(json.loads(original)['decision'],'HOLD')


if __name__=='__main__':unittest.main()
