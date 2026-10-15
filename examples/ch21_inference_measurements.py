"""Offline measurement exercise, not a model benchmark.

All timestamps are synthetic integer milliseconds on one clock. The FIFO
server has one slot, no batching and a fixed one-second service time.
The cohort must be fully observed within the explicit measurement window.
"""
from dataclasses import dataclass
import json
from math import ceil, isfinite


def nonnegative_int(value):
    return type(value) is int and value >= 0


@dataclass(frozen=True)
class Trace:
    request_id: str
    sent_ms: int
    started_ms: int
    token_ms: tuple[int, ...]
    ended_ms: int
    valid: bool
    status: str = 'ok'

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError('request ID required')
        times = (self.sent_ms, self.started_ms, *self.token_ms, self.ended_ms)
        if not all(nonnegative_int(t) for t in times):
            raise ValueError('timestamps must be nonnegative integer milliseconds')
        if list(times) != sorted(times):
            raise ValueError('timestamps must be ordered')
        if type(self.valid) is not bool:
            raise ValueError('valid must be boolean')
        if self.status not in ('ok', 'error', 'rejected', 'cancelled'):
            raise ValueError('unknown status')
        if self.status == 'ok' and not self.token_ms:
            raise ValueError('completed output requires tokens')


def percentile(values, p):
    """Nearest rank on raw samples. Not Prometheus histogram interpolation."""
    if not isfinite(p) or not 0 < p <= 100:
        raise ValueError('percentile must be in (0, 100]')
    samples = sorted(values)
    if not samples:
        return None
    if not all(isfinite(v) for v in samples):
        raise ValueError('samples must be finite')
    return samples[ceil(p * len(samples) / 100) - 1]


def request_metrics(trace: Trace):
    tokens = trace.token_ms
    return {
        'queue_ms': trace.started_ms - trace.sent_ms,
        'ttft_ms': tokens[0] - trace.sent_ms if tokens else None,
        'e2e_ms': trace.ended_ms - trace.sent_ms,
        'tpot_ms': ((tokens[-1] - tokens[0]) / (len(tokens) - 1)
                    if len(tokens) > 1 else None),
        'itl_ms': tuple(b - a for a, b in zip(tokens, tokens[1:])),
    }


def summarize(traces, start_ms, end_ms, deadline_ms):
    traces = list(traces)
    if not traces:
        raise ValueError('cohort must not be empty')
    if not all(nonnegative_int(t) for t in (start_ms, end_ms, deadline_ms)):
        raise ValueError('window and deadline must be integer milliseconds')
    if end_ms <= start_ms or deadline_ms <= 0:
        raise ValueError('window and deadline must be positive')
    if len({t.request_id for t in traces}) != len(traces):
        raise ValueError('duplicate request ID')
    if any(t.sent_ms < start_ms or t.ended_ms > end_ms for t in traces):
        raise ValueError('window must cover the entire drained cohort')
    complete = [t for t in traces if t.status == 'ok']
    metrics = [request_metrics(t) for t in complete]
    useful = sum(t.status == 'ok' and t.valid
                 and t.ended_ms - t.sent_ms <= deadline_ms for t in traces)
    seconds = (end_ms - start_ms) / 1000
    return {
        'attempts': len(traces),
        'transport_ok': len(complete),
        'failed': len(traces) - len(complete),
        'invalid_completed': sum(not t.valid for t in complete),
        'queue_p95_ms': percentile([m['queue_ms'] for m in metrics], 95),
        'ttft_p95_ms': percentile([m['ttft_ms'] for m in metrics], 95),
        'e2e_p95_ms': percentile([m['e2e_ms'] for m in metrics], 95),
        'on_time_valid': useful,
        'output_tokens_per_s': round(sum(len(t.token_ms) for t in traces) / seconds, 3),
        'goodput_rps': round(useful / seconds, 3),
    }


def simulate(arrivals, validity=(True, True, False, True)):
    arrivals = list(arrivals)
    if len(arrivals) != len(validity) or not arrivals:
        raise ValueError('one validity value per arrival required')
    if not all(nonnegative_int(t) for t in arrivals) or arrivals != sorted(arrivals):
        raise ValueError('arrivals must be ordered integer milliseconds')
    free_ms = 0
    traces = []
    for i, (sent_ms, valid) in enumerate(zip(arrivals, validity)):
        started_ms = max(sent_ms, free_ms)
        token_ms = tuple(started_ms + offset for offset in (200, 400, 700, 1000))
        free_ms = started_ms + 1000
        traces.append(Trace(f'r{i+1}', sent_ms, started_ms, token_ms, free_ms, valid))
    return traces


def demo():
    profiles = {'closed': (0, 1000, 2000, 3000), 'open': (0, 250, 500, 750)}
    keys = ('queue_p95_ms', 'ttft_p95_ms', 'e2e_p95_ms',
            'on_time_valid', 'output_tokens_per_s', 'goodput_rps')
    result = {}
    for name, arrivals in profiles.items():
        summary = summarize(simulate(arrivals), 0, 4000, 2000)
        result[name] = {key: summary[key] for key in keys}
    return result


if __name__ == '__main__':
    print(json.dumps(demo(), indent=2))
