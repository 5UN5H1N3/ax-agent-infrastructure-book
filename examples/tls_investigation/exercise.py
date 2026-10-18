"""Read prepared investigation cards. No DNS, TLS, model or scheduler runs."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

DATA = Path(__file__).with_name('cases.json')


def load_cases():
    data = json.loads(DATA.read_text(encoding='utf-8'))
    if data.get('format') != 'tls-investigation-v1' or data.get('synthetic') is not True:
        raise ValueError('Expected synthetic investigation cards')
    return data


def card(case, packet='initial', *, key=False):
    data = load_cases()
    selected = data['cases'][case]
    payload = selected['key'] if key else selected['packets'][packet]
    return dict(synthetic=True, network_used=False, cryptography_executed=False,
                scheduler_started=False, case=case,
                kind='answer_key' if key else 'observation', data=deepcopy(payload))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=('chain', 'dns'), default='chain')
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument('--packet', default=None,
                        help='initial, dns, peer, inventory, verify, history; DNS case also address_control, cache')
    choice.add_argument('--key', action='store_true', help='Reveal the prepared answer, not an inferred diagnosis')
    args = parser.parse_args(argv)
    try:
        result = card(args.case, args.packet or 'initial', key=args.key)
    except KeyError:
        parser.error('No such packet in this case; see the companion README')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
