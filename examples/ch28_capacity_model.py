"""Offline FIFO exercise with Worker slots and a shared restore service.

Integer milliseconds, one synthetic clock. Each accepted job holds a Worker
from assignment through restore waiting, restore and execution. A restore lane
has a fixed independent service rate: adding lanes explicitly adds bandwidth,
not merely concurrent requests to the same real storage system. There is no
network, AX/Substrate client, autoscaler, retry, real correctness verifier or
measurement of production capacity here.
"""
from dataclasses import dataclass
from heapq import heapify, heappop, heappush
import json
from math import ceil


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


@dataclass(frozen=True)
class Config:
    worker_slots: int = 4
    restore_lanes: int = 1
    restore_ms: int = 1000
    execute_ms: int = 500
    max_inflight: int | None = None

    def __post_init__(self):
        for name in ('worker_slots', 'restore_lanes', 'execute_ms'):
            if not integer(getattr(self, name), 1):
                raise ValueError(f'{name} must be a positive integer')
        if not integer(self.restore_ms):
            raise ValueError('restore_ms must be a nonnegative integer')
        if self.max_inflight is not None and not integer(self.max_inflight, 1):
            raise ValueError('max_inflight must be positive or None')


@dataclass(frozen=True)
class Trace:
    request_id: str
    sent_ms: int
    assigned_ms: int
    restore_started_ms: int
    restored_ms: int
    ended_ms: int
    correct: bool
    status: str = 'completed'

    def __post_init__(self):
        if not isinstance(self.request_id, str) or not self.request_id:
            raise ValueError('request ID required')
        times = (self.sent_ms, self.assigned_ms, self.restore_started_ms,
                 self.restored_ms, self.ended_ms)
        if not all(integer(t) for t in times) or tuple(sorted(times)) != times:
            raise ValueError('ordered nonnegative integer timestamps required')
        if type(self.correct) is not bool:
            raise ValueError('correct must be boolean')
        if self.status not in ('completed', 'rejected'):
            raise ValueError('unknown status')
        if self.status == 'rejected' and (len(set(times)) != 1 or self.correct):
            raise ValueError('rejection is immediate and never a useful result')


def phase_times(trace):
    return {
        'worker_wait_ms': trace.assigned_ms - trace.sent_ms,
        'restore_wait_ms': trace.restore_started_ms - trace.assigned_ms,
        'restore_ms': trace.restored_ms - trace.restore_started_ms,
        'execute_ms': trace.ended_ms - trace.restored_ms,
        'e2e_ms': trace.ended_ms - trace.sent_ms,
    }


def simulate(arrivals, config=Config(), validity=None):
    arrivals = list(arrivals)
    if not arrivals or not all(integer(t) for t in arrivals):
        raise ValueError('nonempty integer arrivals required')
    if arrivals != sorted(arrivals):
        raise ValueError('arrivals must be ordered')
    validity = [True] * len(arrivals) if validity is None else list(validity)
    if len(validity) != len(arrivals) or any(type(v) is not bool for v in validity):
        raise ValueError('one boolean validity flag per arrival required')
    if not isinstance(config, Config):
        raise ValueError('Config required')
    workers = [0] * config.worker_slots
    lanes = [0] * config.restore_lanes
    heapify(workers)
    heapify(lanes)
    inflight, result = [], []
    for i, (sent, correct) in enumerate(zip(arrivals, validity), 1):
        while inflight and inflight[0] <= sent:
            heappop(inflight)
        if config.max_inflight is not None and len(inflight) >= config.max_inflight:
            result.append(Trace(f'r{i}', sent, sent, sent, sent, sent,
                                False, 'rejected'))
            continue
        assigned = max(sent, heappop(workers))
        restore_started = max(assigned, heappop(lanes))
        restored = restore_started + config.restore_ms
        ended = restored + config.execute_ms
        heappush(lanes, restored)
        heappush(workers, ended)
        heappush(inflight, ended)
        result.append(Trace(f'r{i}', sent, assigned, restore_started,
                            restored, ended, correct))
    return result


def percentile(values, p):
    """Nearest rank over raw integer samples; None for an empty population."""
    values = sorted(values)
    if not integer(p, 1) or p > 100 or any(not integer(v) for v in values):
        raise ValueError('integer samples and percentile in 1..100 required')
    return values[ceil(p * len(values) / 100) - 1] if values else None


def summarize(traces, arrival_end_ms=10000, end_ms=24000, deadline_ms=2500):
    traces = list(traces)
    if not all(integer(v, 1) for v in (arrival_end_ms, end_ms, deadline_ms)):
        raise ValueError('positive integer windows and deadline required')
    if end_ms < arrival_end_ms or not traces:
        raise ValueError('observation must cover a nonempty arrival cohort')
    if len({t.request_id for t in traces}) != len(traces):
        raise ValueError('duplicate request ID')
    if any(t.sent_ms >= arrival_end_ms for t in traces):
        raise ValueError('arrival cohort is half-open: [0, arrival_end_ms)')
    accepted = [t for t in traces if t.status == 'completed']
    completed = [t for t in accepted if t.ended_ms <= end_ms]
    pending = [t for t in accepted if t.ended_ms > end_ms]
    useful = sum(t.correct and t.ended_ms - t.sent_ms <= deadline_ms
                 for t in completed)
    return {
        'offered': len(traces), 'accepted': len(accepted),
        'rejected': len(traces) - len(accepted),
        'completed': len(completed), 'pending': len(pending),
        'invalid_completed': sum(not t.correct for t in completed),
        'late_completed': sum(t.ended_ms - t.sent_ms > deadline_ms
                              for t in completed),
        'pending_deadline_missed': sum(t.sent_ms + deadline_ms <= end_ms
                                       for t in pending),
        'pending_deadline_unresolved': sum(t.sent_ms + deadline_ms > end_ms
                                           for t in pending),
        'on_time_correct': useful,
        'completed_p95_ms': percentile([t.ended_ms - t.sent_ms
                                         for t in completed], 95),
        'observed_success_fraction': useful / len(traces),
        'goodput_rps': round(useful / (end_ms / 1000), 3),
        'completion_rps': round(len(completed) / (end_ms / 1000), 3),
        'arrival_window_ms': arrival_end_ms, 'observation_end_ms': end_ms,
        'live_benchmark_executed': False,
        'production_capacity_proven': False,
    }


def demo():
    arrivals = list(range(0, 10000, 500))
    configs = {
        'baseline': Config(),
        'more_workers': Config(worker_slots=8),
        'more_restore_capacity': Config(restore_lanes=2),
        'bounded_admission': Config(max_inflight=2),
    }
    result = {name: summarize(simulate(arrivals, config))
              for name, config in configs.items()}
    flags = [True] * len(arrivals)
    flags[0] = False
    result['fast_but_invalid'] = summarize(
        simulate(arrivals, Config(restore_lanes=2), flags))
    result['cut_at_10s'] = summarize(simulate(arrivals), end_ms=10000)
    return result


if __name__ == '__main__':
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
