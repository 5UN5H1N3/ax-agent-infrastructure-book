"""Offline observability model. No OTel SDK, exporter, server or network.

The ledger and authenticated producer contexts are TRUSTED FIXTURES, not durable
or authenticated implementations. Diagnostics may be dropped or duplicated;
they never update authoritative job state or the SLI population.
"""

from collections import deque
from dataclasses import dataclass
import hashlib
import json
import re


NAMES = frozenset({"job.accepted", "approval.waiting", "tool.sent",
                   "tool.protocol_ok", "tool.verified"})
STAGES = frozenset({"approval", "publish", "verify"})
RESULTS = frozenset({"ok", "error", "timeout", "pending"})


@dataclass(frozen=True)
class Context:
    job: str
    task: str
    activation: str
    producer: str
    trace_id: str
    span_id: str


@dataclass(frozen=True)
class Event:
    context: Context
    id: str
    sequence: int
    occurred_at: int
    name: str
    attributes: tuple[tuple[str, str | int], ...]
    schema: int = 1


def event(context, event_id, sequence, occurred_at, name, attributes=None):
    """Build an event at the trusted producer boundary using a tiny allowlist."""
    if name not in NAMES:
        raise ValueError("unknown event")
    for value, length in ((context.trace_id, 32), (context.span_id, 16)):
        if not re.fullmatch(f"[0-9a-f]{{{length}}}", value) or set(value) == {"0"}:
            raise ValueError("invalid trace context")
    if not event_id or type(sequence) is not int or sequence < 0:
        raise ValueError("invalid event identity")
    if type(occurred_at) is not int:
        raise ValueError("invalid timestamp")
    kept = {}
    for key, value in (attributes or {}).items():
        if key == "stage" and isinstance(value, str) and value in STAGES:
            kept[key] = value
        elif key == "result" and isinstance(value, str) and value in RESULTS:
            kept[key] = value
        elif key == "duration_ms" and type(value) is int and 0 <= value <= 3600000:
            kept[key] = value
    # Payload cannot overwrite context, even with job/trace_id/ate.* keys.
    return Event(context, event_id, sequence, occurred_at, name,
                 tuple(sorted(kept.items())))


class Collector:
    """Bounded diagnostic queue and in-memory deduplication, not an audit log."""

    def __init__(self, capacity=2):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("invalid capacity")
        self.capacity = capacity
        self.queue = deque()
        self.records = {}
        self.attempted = 0
        self.dropped = 0
        self.duplicates = 0

    def offer(self, item):
        self.attempted += 1
        if len(self.queue) >= self.capacity:
            self.dropped += 1
            return False
        self.queue.append(item)
        return True

    def flush(self):
        while self.queue:
            item = self.queue.popleft()
            key = (item.context.producer, item.id)
            previous = self.records.get(key)
            if previous is not None:
                if previous != item:
                    raise ValueError("conflicting event identity")
                self.duplicates += 1
            else:
                self.records[key] = item

    def latest_streams(self, job):
        # Sequence only orders ONE producer/activation stream, not a whole job.
        latest = {}
        for item in self.records.values():
            if item.context.job != job:
                continue
            key = (item.context.producer, item.context.task, item.context.activation)
            if key not in latest or item.sequence > latest[key].sequence:
                latest[key] = item
        return latest


@dataclass(frozen=True)
class Goal:
    job: str
    accepted_at: int
    deadline: int
    phase: str
    verified_at: int | None = None
    eligible: bool = True


def outcome_sli(goals, start, end, now):
    """Deadline-matured acceptance cohort; diagnostics are deliberately absent."""
    if not start < end <= now:
        raise ValueError("invalid observation window")
    if len({g.job for g in goals}) != len(goals):
        raise ValueError("duplicate goal")
    if any(g.deadline < g.accepted_at or (g.verified_at is not None and
           not g.accepted_at <= g.verified_at <= now) for g in goals):
        raise ValueError("invalid goal time")
    cohort = [g for g in goals if g.eligible and start <= g.accepted_at < end]
    due = [g for g in cohort if g.deadline <= now]
    good = sum(g.verified_at is not None and g.verified_at <= g.deadline for g in due)
    return {"eligible": len(cohort), "due": len(due), "not_due": len(cohort) - len(due),
            "good": good, "bad": len(due) - good,
            "ratio": good / len(due) if due else None}


def investigate(job, ledger, collector):
    """Choose the next check from a trusted state fixture, not from log absence."""
    if ledger is None:
        return {"state": "unknown", "next": "restore_state_access", "gap": None}
    goal = ledger.get(job)
    if goal is None:
        return {"state": "not_found", "next": "check_request_identity", "gap": None}
    events = [x for x in collector.records.values() if x.context.job == job]
    has_verified = any(x.name == "tool.verified" for x in events)
    if goal.verified_at is not None:
        return {"state": "verified", "next": "inspect_telemetry" if not has_verified
                else "none", "gap": not has_verified}
    checks = {"approval": "check_approval_deadline", "uncertain": "reconcile_target",
              "running": "follow_activation"}
    return {"state": goal.phase, "next": checks[goal.phase], "gap": None}


def metric_labels(stage, result, **extra):
    """Exact bounded value sets; identifiers remain in events, not labels."""
    if extra or stage not in STAGES or result not in RESULTS:
        raise ValueError("unbounded metric labels")
    return tuple(sorted({"stage": stage, "result": result}.items()))


def fixtures():
    return [Goal("happy", 0, 50, "done", 20),
            Goal("approval_wait", 0, 50, "approval"),
            Goal("reply_lost", 0, 50, "uncertain"),
            Goal("telemetry_lost", 0, 50, "done", 30),
            Goal("late_verified", 0, 50, "done", 55),
            Goal("young", 45, 95, "running")]


def context(job, activation="activation-1", producer="publisher"):
    # Deterministic fixture IDs, NOT a production trace ID generator.
    seed = f"{job}:{activation}".encode()
    trace = hashlib.sha256(seed).hexdigest()[:32]
    span = hashlib.sha256(seed + producer.encode()).hexdigest()[:16]
    return Context(job, "task-publisher", activation, producer, trace, span)


def demo():
    goals = fixtures()
    ledger = {g.job: g for g in goals}
    collector = Collector(capacity=1)
    for g in goals:
        item = event(context(g.job), g.job + ":1", 1, 0, "job.accepted")
        collector.offer(item)
        if g.verified_at is not None:
            verification = event(context(g.job), g.job + ":2", 2,
                                 g.verified_at, "tool.verified")
            if g.job == "telemetry_lost":
                # Full queue deliberately loses the diagnostic verification.
                collector.offer(verification)
            else:
                collector.flush()
                collector.offer(verification)
        collector.flush()
    # Same record arrives on a second ingestion path; it is deduplicated.
    duplicate = collector.records[("publisher", "happy:2")]
    collector.offer(duplicate)
    collector.flush()
    # Older event arrives last with a later (skewed) wall-clock timestamp.
    old = event(context("happy"), "happy:0", 0, 100, "tool.sent")
    collector.offer(old)
    collector.flush()
    rows = {}
    for g in goals:
        assessment = investigate(g.job, ledger, collector)
        assessment["deadline_result"] = ("not_due" if g.deadline > 60 else
            "good" if g.verified_at is not None and g.verified_at <= g.deadline else "bad")
        rows[g.job] = assessment
    completed = [g for g in goals if g.verified_at is not None]
    naive = sum(g.verified_at <= g.deadline for g in completed) / len(completed)
    return {"jobs": rows, "sli": outcome_sli(goals, 0, 50, 60),
            "naive_completed_only_ratio": naive,
            "pipeline": {"attempted": collector.attempted, "dropped": collector.dropped,
                         "duplicates": collector.duplicates, "stored": len(collector.records)},
            "latest_happy": next(iter(collector.latest_streams("happy").values())).name,
            "bounded_label_combinations": len(STAGES) * len(RESULTS)}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
