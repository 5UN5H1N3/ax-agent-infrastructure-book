"""Offline trace calculator; no router, clock, sandbox, RPC or HTTP server."""
from dataclasses import dataclass
import json


@dataclass(frozen=True)
class Attempt:
    start_ms: int
    end_ms: int
    result: str  # ok, retryable or fatal; durations are supplied, not measured


def flight_result(attempts, budget_ms=5000):
    """One flight starting at t=0; a retry budget does not cancel an attempt."""
    if budget_ms <= 0 or not attempts:
        raise ValueError("positive budget and a nonempty trace required")
    previous_end = -1
    parked = False
    for index, attempt in enumerate(attempts):
        if (attempt.start_ms < 0 or attempt.end_ms < attempt.start_ms
                or attempt.start_ms < previous_end
                or (index == 0 and attempt.start_ms != 0)
                or attempt.result not in {"ok", "retryable", "fatal"}):
            raise ValueError("invalid attempt trace")
        if attempt.start_ms >= budget_ms:
            if any(a.start_ms < attempt.start_ms for a in attempts[index + 1:]):
                raise ValueError("trace is not ordered")
            # This proposed attempt cannot start. No RPC is executed here.
            return {"outcome": "budget_exhausted", "finished_ms": budget_ms,
                    "attempts": index, "parked": parked, "served_late": False}
        previous_end = attempt.end_ms
        if attempt.result == "ok":
            if index != len(attempts) - 1:
                raise ValueError("trace continues after success")
            return {"outcome": "routable", "finished_ms": attempt.end_ms,
                    "attempts": index + 1, "parked": parked,
                    "served_late": attempt.end_ms > budget_ms}
        if attempt.result == "fatal":
            if index != len(attempts) - 1:
                raise ValueError("trace continues after a fatal result")
            return {"outcome": "error", "finished_ms": attempt.end_ms,
                    "attempts": index + 1, "parked": parked, "served_late": False}
        parked = True
        if attempt.end_ms >= budget_ms:
            if index != len(attempts) - 1:
                raise ValueError("trace continues after exhaustion")
            return {"outcome": "budget_exhausted", "finished_ms": attempt.end_ms,
                    "attempts": index + 1, "parked": parked, "served_late": False}
    raise ValueError("trace needs a terminal result or a proposed retry past budget")


def caller_result(flight, arrived_ms, deadline_ms):
    """Assumes this caller joined this existing flight; no flight registry."""
    end = flight["finished_ms"]
    if not 0 <= arrived_ms < end or deadline_ms <= arrived_ms:
        raise ValueError("caller must join before completion with a future deadline")
    timed_out = deadline_ms < end
    return {"outcome": "caller_timeout" if timed_out else flight["outcome"],
            "wait_ms": min(end, deadline_ms) - arrived_ms,
            "flight_outlives_caller": timed_out}


def boot_source(local_present=False, external_declared=False):
    """Selects a simplified source, not its integrity or compatibility."""
    if local_present:
        return "local_restore"
    if external_declared:
        return "external_restore"
    return "cold_boot"


def restore_result(source, success):
    if source not in {"local_restore", "external_restore", "cold_boot"}:
        raise ValueError("unknown source")
    return {"state": "RUNNING" if success else "CRASHED",
            "automatic_cold_fallback": False}


def wakeup_gate(probe_results):
    """None = probe absent. True = observed HTTP 200. False = probe failed.

    This is a completed-wakeup observation, not a polling implementation.
    It cannot prove that an endpoint's 200 means business safety.
    """
    if not probe_results or any(x is not None and type(x) is not bool
                                for x in probe_results):
        raise ValueError("one bool or None per container required")
    passed = all(x is not False for x in probe_results)
    return {"state": "RUNNING" if passed else "CRASHED",
            "all_containers_probed": all(x is not None for x in probe_results),
            "continuous_health_verified": False,
            "business_safety_verified": False}


def demo():
    trace = [Attempt(0, 10, "retryable"), Attempt(100, 5200, "ok")]
    flight = flight_result(trace)
    return {
        "source": "offline_supplied_trace",
        "cluster_contacted": False,
        "clock_or_latency_measured": False,
        "shared_flight": flight,
        "first_caller": caller_result(flight, 0, 10000),
        "late_joiner": caller_result(flight, 4900, 14900),
        "short_deadline_caller": caller_result(flight, 4900, 5100),
        "declared_snapshot_failure": restore_result(boot_source(False, True), False),
        "one_unprobed_container": wakeup_gate([True, None]),
    }


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
