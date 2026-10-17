"""Offline, read-only evidence join. No AX, Redis, Substrate or network client.

Observation envelopes and identities are trusted collector fixtures, NOT AX API
fields or authentication. A bounded observation age is not an atomic snapshot
or proof that a fact remains true. All times use one artificial clock.
"""
from dataclasses import dataclass, replace
import json
import math


@dataclass(frozen=True)
class Identity:
    task: str = "lab/network-017"
    incarnation: str = "launch-017"
    actor_uid: str = "actor-fixture-017"
    assignment: str = "assignment-fixture-2"
    run: str = "lab-network-017"
    operation: str = "op-17"

    def __post_init__(self):
        if any(type(v) is not str or not v or len(v) > 100
               for v in vars(self).values()):
            raise ValueError("bounded non-empty identity required")


@dataclass(frozen=True)
class Observation:
    identity: Identity
    observed_at: float
    fetched_at: float
    values: dict


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


class Observer:
    """Retains per-source facts; never infers goal success from infrastructure."""
    def __init__(self, identity, *, now, max_age=10):
        if not isinstance(identity, Identity):
            raise ValueError("identity required")
        if not number(now) or not number(max_age) or max_age <= 0:
            raise ValueError("finite clock and positive age budget required")
        self.identity, self.now, self.max_age = identity, now, max_age

    def inspect(self, observations):
        claims, rejected = {}, {}
        for source in ("ax", "substrate", "runner", "ledger"):
            item = observations.get(source)
            reason = self._reject(item)
            if reason:
                rejected[source] = reason
                continue
            value = self._decode(source, item.values)
            if value is None:
                rejected[source] = "invalid_payload"
                continue
            claims[source] = {"observed_at": item.observed_at,
                              "fetched_at": item.fetched_at, **value}
        # No global green/red flag: these are different questions.
        return {"identity": vars(self.identity).copy(), "claims": claims,
                "unknown_sources": rejected, "next_reads": self._next(claims),
                "automatic_mutations": 0}

    def _reject(self, item):
        if item is None:
            return "missing"
        if not isinstance(item, Observation) or type(item.values) is not dict:
            return "invalid_envelope"
        if item.identity != self.identity:
            return "identity_mismatch"
        a, b = item.observed_at, item.fetched_at
        if not number(a) or not number(b) or not (0 <= a <= b <= self.now):
            return "invalid_time"
        if self.now - a > self.max_age:
            return "stale_observation"
        return None

    @staticmethod
    def _decode(source, v):
        if source == "ax":
            # A fresh read proves what the record says, not live readiness.
            phase, condition = v.get("phase"), v.get("workspace_condition")
            if type(phase) is not str or not phase or condition not in ("True", "False", "Unknown"):
                return None
            return {"record_phase": phase, "stored_workspace_condition": condition,
                    "live_workspace_ready": "UNKNOWN"}
        if source == "substrate":
            exists = v.get("actor_exists")
            if type(exists) is not bool:
                return None
            return {"actor_exists": exists}
        if source == "runner":
            ready, state = v.get("workspace_ready"), v.get("command_state")
            code = v.get("exit_code")
            if type(ready) is not bool or state not in ("RUNNING", "EXITED", "UNKNOWN"):
                return None
            if state == "EXITED":
                if type(code) is not int:
                    return None
            elif code is not None:
                return None
            return {"workspace_ready": ready, "command_state": state, "exit_code": code}
        applied, goal = v.get("operation_state"), v.get("goal_verification")
        if applied not in ("APPLIED", "OUTCOME_UNKNOWN", "DISPATCHING", "PREPARED"):
            return None
        if goal not in ("NOT_EVALUATED", "PASSED", "FAILED"):
            return None
        if goal == "PASSED" and applied != "APPLIED":
            return None
        return {"operation_state": applied, "goal_verification": goal}

    @staticmethod
    def _next(c):
        reads = []
        if "ax" not in c:
            reads.append("read_task_record")
        if "substrate" not in c:
            reads.append("read_current_actor_assignment")
        if "runner" not in c:
            reads.append("collect_current_runner_and_command_evidence")
        elif c["runner"]["command_state"] == "EXITED":
            reads.append("read_command_exit_log_and_application_checkpoint")
        elif c["runner"]["command_state"] == "UNKNOWN":
            reads.append("read_command_evidence")
        elif not c["runner"]["workspace_ready"]:
            reads.append("read_workspace_setup_log")
        ledger = c.get("ledger")
        if ledger is None or ledger["operation_state"] in ("OUTCOME_UNKNOWN", "DISPATCHING"):
            reads.append("read_operation_receipt_before_any_repeat")
        elif ledger["operation_state"] == "APPLIED" and ledger["goal_verification"] == "NOT_EVALUATED":
            reads.append("read_independent_goal_verification")
        if c.get("substrate", {}).get("actor_exists") is False and "runner" in c:
            reads.append("repeat_inventory_and_probe_for_time_skew")
        return reads


class DirtyCache:
    """Local invalidation generation, not an AX resource version.

    Watch payloads never replace state. Events during a read discard its result.
    A polling deadline bounds cached display age even if all events are lost.
    Single in-flight read required. No I/O/reconnect loop, concurrency control
    or backoff implementation here; generation is not a read sequence number.
    """
    def __init__(self, *, poll_interval=5):
        if not number(poll_interval) or poll_interval <= 0:
            raise ValueError("positive finite poll interval required")
        self.interval, self.generation = poll_interval, 0
        self.value, self.expires_at = None, 0

    def invalidate(self):
        self.generation += 1
        self.value, self.expires_at = None, 0

    def begin_read(self):
        return self.generation

    def finish_read(self, ticket, value, *, now):
        if not number(now) or now < 0:
            raise ValueError("non-negative finite clock required")
        if type(ticket) is not int or ticket != self.generation:
            return False
        self.value, self.expires_at = value, now + self.interval
        return True

    def get(self, *, now):
        if not number(now) or now < 0:
            raise ValueError("non-negative finite clock required")
        return self.value if now < self.expires_at else None


def fixtures():
    identity = Identity()
    payloads = {"ax": {"phase": "Running", "workspace_condition": "False"},
                "substrate": {"actor_exists": True},
                "runner": {"workspace_ready": True, "command_state": "EXITED", "exit_code": 1},
                "ledger": {"operation_state": "APPLIED", "goal_verification": "NOT_EVALUATED"}}
    return {source: Observation(identity, 96, 99, v) for source, v in payloads.items()}


def demo():
    samples = fixtures()
    observer = Observer(Identity(), now=100)
    normal = observer.inspect(samples)
    old_worker = dict(samples)
    old_worker["runner"] = replace(samples["runner"], identity=replace(Identity(), assignment="assignment-fixture-1"))
    old_fact = dict(samples)
    old_fact["runner"] = replace(samples["runner"], observed_at=50, fetched_at=99)
    cache = DirtyCache()
    cache.finish_read(cache.begin_read(), normal, now=100)
    # Simulates a dropped watch notification: polling still expires display.
    return {"incident": normal,
            "old_worker": observer.inspect(old_worker)["unknown_sources"],
            "fresh_fetch_old_fact": observer.inspect(old_fact)["unknown_sources"],
            "cache_after_lost_watch": cache.get(now=105), "network_calls": 0}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
