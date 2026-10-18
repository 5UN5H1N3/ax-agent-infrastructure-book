"""Card consistency and reader tests, not TLS or agent-model integration."""
from copy import deepcopy
from datetime import datetime, timedelta
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from examples.tls_investigation import exercise as ex

ROOT = Path(__file__).resolve().parents[1]


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


class InvestigationTests(unittest.TestCase):
    def setUp(self):
        self.data = ex.load_cases()
        self.a = self.data['cases']['chain']
        self.b = self.data['cases']['dns']

    def test_scope_and_synthetic_boundary(self):
        self.assertTrue(self.data['synthetic'])
        self.assertEqual(self.data['scope'], 'api.example.com')
        self.assertEqual(set(self.data['cases']), {'chain', 'dns'})

    def test_every_packet_has_unique_provenance(self):
        ids = []
        for case in self.data['cases'].values():
            for packet in case['packets'].values():
                ids.append(packet['id'])
                self.assertTrue(packet['source'])
                self.assertEqual(utc(packet['at']).utcoffset(), timedelta(0))
        self.assertEqual(len(ids), 10)
        self.assertEqual(len(set(ids)), 10)

    def test_key_only_references_existing_observations(self):
        for case in self.data['cases'].values():
            ids = {p['id'] for p in case['packets'].values()}
            self.assertLessEqual(set(case['key']['finding_evidence']), ids)
            order = case['key']['full_worked_order']
            self.assertEqual(set(order), set(case['packets']))
            times = [utc(case['packets'][name]['at']) for name in order]
            self.assertEqual(times, sorted(times))

    def test_alternative_first_reads_exist_and_are_not_answers(self):
        for case in self.data['cases'].values():
            self.assertGreaterEqual(len(case['key']['first_read_options']), 2)
            self.assertLessEqual(set(case['key']['first_read_options']), set(case['packets']))
            self.assertNotIn('initial', case['key']['first_read_options'])

    def test_chain_leaf_is_valid_at_the_given_time(self):
        p = self.a['packets']['initial']
        self.assertLess(utc(p['leaf']['not_before']), utc(p['at']))
        self.assertLess(utc(p['at']), utc(p['leaf']['not_after']))
        self.assertTrue(all(abs(n) <= 1 for n in p['clock_offsets_seconds'].values()))

    def test_same_leaf_and_sni_have_different_path_results(self):
        east, west = self.a['packets']['initial']['probes']
        for key in ('leaf', 'serial', 'sni', 'hostname_match'):
            self.assertEqual(east[key], west[key])
        self.assertFalse(east['path_ok'])
        self.assertTrue(west['path_ok'])

    def test_chain_dns_matches_actual_dial_for_both_regions(self):
        packet = self.a['packets']['dns']
        self.assertEqual(packet['expected_by_region'], packet['client_answers'])
        for p in self.a['packets']['initial']['probes']:
            self.assertEqual(p['address'], packet['client_answers'][p['region']])
            self.assertEqual(p['address'], self.a['packets']['peer'][p['region']]['address'])
        self.assertFalse(packet['gateway_or_proxy'])

    def test_missing_intermediate_not_missing_root(self):
        p = self.a['packets']['peer']
        self.assertEqual(p['east']['sent'], ['L1'])
        self.assertEqual(p['west']['sent'], ['L1', 'I1'])
        self.assertNotIn('R1', p['west']['sent'])

    def test_inventory_labels_form_the_claimed_path(self):
        certs = {p['id']: p for p in self.a['packets']['inventory']['certificates']}
        self.assertEqual(certs['L1']['issuer'], 'I1')
        self.assertEqual(certs['I1']['issuer'], 'R1')
        self.assertTrue(certs['I1']['is_ca'])
        self.assertTrue(certs['R1']['trust_anchor'])
        self.assertEqual(certs['L1']['serial'], self.a['packets']['initial']['probes'][0]['serial'])
        now = utc(self.a['packets']['initial']['at'])
        self.assertLess(utc(certs['I1']['not_before']), now)
        self.assertLess(now, utc(certs['I1']['not_after']))

    def test_comparison_keeps_client_trust_time_and_name(self):
        before = self.a['packets']['initial']
        check = self.a['packets']['verify']
        for key in ('client_profile', 'trust_anchors', 'intermediate_cache', 'aia_fetch', 'expected_hostname'):
            self.assertEqual(before[key], check[key])
        self.assertEqual(check['verification_time'], before['at'])
        self.assertEqual(check['captured_from'], self.a['packets']['peer']['id'])
        self.assertEqual(check['intermediate_from'], self.a['packets']['inventory']['id'])
        self.assertFalse(check['network_used'])
        self.assertEqual(check['revocation'], 'not_evaluated')

    def test_comparison_only_adds_untrusted_intermediate(self):
        p = self.a['packets']['verify']
        self.assertEqual(p['before']['chain'], p['after']['chain'])
        self.assertEqual(p['before']['extra_untrusted'], [])
        self.assertEqual(p['after']['extra_untrusted'], ['I1'])
        self.assertNotIn('I1', p['trust_anchors'])
        self.assertFalse(p['before']['path_ok'])
        self.assertTrue(p['after']['path_ok'])

    def test_bundle_change_keeps_the_leaf_serial(self):
        p = self.a['packets']['history']
        self.assertEqual(p['leaf_serial_before'], p['leaf_serial_after'])
        self.assertEqual(p['previous_bundle'], ['L1', 'I1'])
        self.assertEqual(p['current_bundle'], ['L1'])
        self.assertLess(utc(p['deployed_at']), utc(self.a['packets']['initial']['at']))

    def test_proposal_is_scoped_versioned_and_not_executed(self):
        key = self.a['key']
        history = self.a['packets']['history']
        self.assertEqual(key['phase'], 'WaitingForApproval')
        self.assertFalse(key['incident_restored'])
        self.assertEqual(key['proposal']['expected_revision'], history['current_revision'])
        self.assertEqual(key['proposal']['target'], history['target'])
        self.assertEqual(key['proposal']['desired_bundle'], history['previous_bundle'])
        self.assertEqual(key['proposal']['rollback_revision'], history['previous_revision'])
        self.assertFalse(key['proposal']['executed'])

    def test_missing_evidence_does_not_confirm_a_root_cause(self):
        self.assertEqual(self.a['key']['unavailable_inventory_and_verify']['phase'], 'Blocked')
        self.assertTrue(self.b['key']['cache_unavailable'])

    def test_dns_case_uses_correct_sni_but_wrong_destination_name(self):
        east, west = self.b['packets']['initial']['probes']
        self.assertEqual(east['sni'], west['sni'])
        self.assertEqual(east['sni'], self.data['scope'])
        self.assertTrue(east['path_ok'] and west['path_ok'])
        self.assertNotIn(self.data['scope'], east['san'])
        self.assertFalse(east['hostname_match'])
        self.assertTrue(west['hostname_match'])

    def test_dns_case_does_not_depend_on_expired_leaf(self):
        p = self.b['packets']['initial']
        for start, end in p['leaf_validity'].values():
            self.assertLess(utc(start), utc(p['at']))
            self.assertLess(utc(p['at']), utc(end))

    def test_dns_answer_is_joined_to_the_clients_dial(self):
        east, west = self.b['packets']['initial']['probes']
        p = self.b['packets']['dns']
        self.assertEqual(east['address'], p['east_answer'])
        self.assertEqual(west['address'], p['west_answer'])
        self.assertEqual(p['west_answer'], p['authoritative_address'])
        self.assertNotEqual(p['east_answer'], p['authoritative_address'])

    def test_address_control_does_not_silently_fix_normal_dns(self):
        p = self.b['packets']['address_control']
        self.assertEqual(p['address'], self.b['packets']['dns']['authoritative_address'])
        self.assertEqual(p['sni'], self.b['packets']['initial']['probes'][0]['sni'])
        self.assertEqual(p['trust_anchors'], self.b['packets']['initial']['trust_anchors'])
        self.assertEqual(p['changed_variable'], 'dial_address')
        self.assertTrue(p['path_ok'] and p['hostname_match'])
        self.assertFalse(p['normal_dns_path_verified'])

    def test_cache_expiry_uses_original_ttl_before_the_change(self):
        p = self.b['packets']['cache']
        dns = self.b['packets']['dns']
        expiry = utc(p['cached_at']) + timedelta(seconds=p['original_ttl_seconds'])
        self.assertEqual(expiry, utc(p['expires_at']))
        self.assertLess(utc(p['cached_at']), utc(dns['changed_at']))
        self.assertLess(utc(dns['changed_at']), utc(dns['at']))
        self.assertEqual((expiry-utc(dns['at'])).total_seconds(), dns['east_remaining_ttl_seconds'])
        self.assertEqual(p['address'], dns['east_answer'])
        self.assertFalse(p['serve_stale'] or p['ttl_override'])

    def test_wait_has_owner_deadline_and_no_execution(self):
        key = self.b['key']
        wait = key['scheduled_read']
        self.assertEqual(key['phase'], 'WaitingForTool')
        self.assertTrue(wait['owner'])
        self.assertLess(utc(self.b['packets']['cache']['expires_at']), utc(wait['at']))
        self.assertLess(utc(wait['at']), utc(wait['deadline']))
        self.assertFalse(wait['executed'] or key['incident_restored'])
        self.assertIsNone(key['write_proposal'])

    def test_viewer_returns_no_hidden_answer_by_default(self):
        p = ex.card('chain')
        self.assertEqual(p['kind'], 'observation')
        self.assertEqual(p['data']['id'], 'A0')
        self.assertNotIn('finding', p['data'])
        self.assertNotIn('key', p['data'])
        for key in ('network_used', 'cryptography_executed', 'scheduler_started'):
            self.assertFalse(p[key])

    def test_every_card_and_explicit_key_can_be_read(self):
        for case, data in self.data['cases'].items():
            for name, p in data['packets'].items():
                self.assertEqual(ex.card(case, name)['data'], p)
            self.assertEqual(ex.card(case, key=True)['data'], data['key'])

    def test_unknown_case_or_packet_is_not_guessed(self):
        for case, name in [('unknown', 'initial'), ('chain', 'cache'), ('dns', 'peer')]:
            with self.assertRaises(KeyError):
                ex.card(case, name)

    def test_cli_default_and_key_are_distinct(self):
        for args, kind in [([], 'observation'), (['--key'], 'answer_key')]:
            with patch('sys.stdout', new_callable=io.StringIO) as out:
                self.assertEqual(ex.main(args), 0)
            self.assertEqual(json.loads(out.getvalue())['kind'], kind)

    def test_cli_unknown_card_is_nonzero(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit) as result:
                ex.main(['--case', 'dns', '--packet', 'peer'])
        self.assertEqual(result.exception.code, 2)

    def test_cli_key_cannot_be_mixed_with_packet(self):
        with patch('sys.stderr', new_callable=io.StringIO):
            with self.assertRaises(SystemExit):
                ex.main(['--key', '--packet', 'initial'])

    def test_non_synthetic_input_is_rejected(self):
        raw = deepcopy(self.data)
        raw['synthetic'] = False
        with patch.object(Path, 'read_text', return_value=json.dumps(raw)):
            with self.assertRaisesRegex(ValueError, 'synthetic'):
                ex.load_cases()

    def test_module_runs_from_repository_root(self):
        p = subprocess.run([sys.executable, '-m', 'examples.tls_investigation.exercise',
                            '--case', 'dns', '--packet', 'cache'], cwd=ROOT,
                           capture_output=True, text=True, timeout=3, check=True)
        self.assertEqual(json.loads(p.stdout)['data']['id'], 'B3')


class PrintedCards(HTMLParser):
    def __init__(self):
        super().__init__()
        self.active = None
        self.cards = {}

    def handle_starttag(self, tag, attrs):
        if tag == 'pre':
            self.active = dict(attrs).get('data-tls-card')
            if self.active:
                if self.active in self.cards:
                    raise ValueError('Duplicate TLS card')
                self.cards[self.active] = ''

    def handle_data(self, text):
        if self.active:
            self.cards[self.active] += text

    def handle_endtag(self, tag):
        if tag == 'pre':
            self.active = None


class BookBindingTests(unittest.TestCase):
    def test_printed_cards_equal_the_exercise_data(self):
        parser = PrintedCards()
        parser.feed((ROOT/'book/AX_Agent_Infrastructure_Book_RU.html').read_text())
        self.assertEqual(set(parser.cards), {'chain.peer', 'chain.verify', 'dns.dns', 'dns.cache'})
        data = ex.load_cases()['cases']
        for name, text in parser.cards.items():
            case, packet = name.split('.')
            self.assertEqual(json.loads(text), data[case]['packets'][packet])


if __name__ == '__main__':
    unittest.main()
