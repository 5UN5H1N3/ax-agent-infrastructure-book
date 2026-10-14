"""Deterministic parking model, not a router or Substrate integration test.

Times are supplied values. No clock, network, backoff, TLS or real RPC runs.
Each RouterLab has its own flights and lot, mirroring a process-local boundary.
"""
from dataclasses import dataclass, field
import json

RETRYABLE = frozenset({"ResourceExhausted", "FailedPrecondition", "Unavailable", "Aborted"})


def retry_action(last_result, *, elapsed_ms, budget_ms, in_flight):
    for value in (elapsed_ms, budget_ms):
        if type(value) is not int or value < 0:
            raise ValueError("times must be nonnegative integer milliseconds")
    if budget_ms == 0:
        raise ValueError("budget must be positive")
    if in_flight:
        return "WAIT_FOR_IN_FLIGHT"
    if last_result == "OK":
        return "ROUTE"
    if last_result not in RETRYABLE:
        return "FAIL_FAST"
    if elapsed_ms >= budget_ms:
        return "STOP_RETRIES"
    return "START_NEXT_ATTEMPT"


@dataclass
class Flight:
    started_ms: int
    attempts: int = 1
    in_flight: bool = True
    last_result: str | None = None
    retrying: bool = False
    waiters: set[str] = field(default_factory=set)


class RouterLab:
    """Explicit state transitions, with no event loop or concurrency claim."""
    def __init__(self, capacity=2, budget_ms=5000):
        if type(capacity) is not int or capacity < 1:
            raise ValueError("positive lot capacity required")
        retry_action(None, elapsed_ms=0, budget_ms=budget_ms, in_flight=False)
        self.capacity, self.budget_ms = capacity, budget_ms
        self.flights = {}
        self.parked = set()
        self.results = {}
        self.seen = set()
        self.rpc_starts = 0
        self.now_ms = 0

    def _time(self, now_ms):
        if type(now_ms) is not int or now_ms < self.now_ms:
            raise ValueError("supplied time must be monotonic integer milliseconds")
        self.now_ms = now_ms

    def _park(self, flight, request_id):
        if request_id in self.parked:
            return
        if len(self.parked) >= self.capacity:
            flight.waiters.remove(request_id)
            self.results[request_id] = "ROUTER_AT_CAPACITY"
        else:
            self.parked.add(request_id)

    def arrive(self, actor, request_id, now_ms):
        self._time(now_ms)
        if not actor or not request_id or request_id in self.seen:
            raise ValueError("nonempty actor and unique request id required")
        self.seen.add(request_id)
        if actor not in self.flights:
            self.flights[actor] = Flight(now_ms)
            self.rpc_starts += 1
        flight = self.flights[actor]
        flight.waiters.add(request_id)
        if flight.retrying:
            self._park(flight, request_id)
        return flight

    def complete_attempt(self, actor, result, now_ms):
        self._time(now_ms)
        flight = self.flights[actor]
        if not flight.in_flight:
            raise ValueError("no running attempt")
        flight.in_flight = False
        flight.last_result = result
        if result in RETRYABLE:
            flight.retrying = True
            for request_id in sorted(flight.waiters):
                self._park(flight, request_id)
            if now_ms - flight.started_ms >= self.budget_ms:
                self._finish(actor, "STOP_RETRIES")
        else:
            self._finish(actor, "ROUTE" if result == "OK" else "FAIL_FAST:" + result)

    def next_attempt(self, actor, now_ms):
        self._time(now_ms)
        flight = self.flights[actor]
        action = retry_action(flight.last_result,
                              elapsed_ms=now_ms-flight.started_ms,
                              budget_ms=self.budget_ms,
                              in_flight=flight.in_flight)
        if action == "START_NEXT_ATTEMPT":
            flight.in_flight = True
            flight.attempts += 1
            self.rpc_starts += 1
        elif action == "STOP_RETRIES":
            self._finish(actor, action)
        return action

    def leave(self, actor, request_id, outcome="CALLER_CANCELED"):
        flight = self.flights[actor]
        if request_id not in flight.waiters:
            raise ValueError("request is not waiting")
        flight.waiters.remove(request_id)
        self.parked.discard(request_id)
        self.results[request_id] = outcome
        # The shared attempt stays alive even if all callers have left.

    def _finish(self, actor, outcome):
        flight = self.flights.pop(actor)
        for request_id in flight.waiters:
            self.results[request_id] = outcome
            self.parked.discard(request_id)


def demo():
    router = RouterLab()
    first = router.arrive("review-42", "r1", 0)
    router.arrive("review-42", "r2", 20)
    router.complete_attempt("review-42", "ResourceExhausted", 100)
    router.arrive("review-42", "r3", 200)
    router.arrive("already-running", "fast", 250)
    router.complete_attempt("already-running", "OK", 260)
    router.leave("review-42", "r1")
    router.next_attempt("review-42", 4800)
    router.arrive("review-42", "late", 4900)
    at_budget = router.next_attempt("review-42", 5100)
    router.complete_attempt("review-42", "OK", 6200)
    other = RouterLab()
    other.arrive("review-42", "on-other-router", 0)
    return {
        "source": "supplied_timeline_and_process_local_model",
        "substrate_contacted": False,
        "review_flight_attempts": first.attempts,
        "action_after_budget": at_budget,
        "late_join_remaining_budget_ms": 100,
        "completed_ms": 6200,
        "results": dict(sorted(router.results.items())),
        "parked_after_completion": len(router.parked),
        "other_router_rpc_starts": other.rpc_starts,
    }


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2, ensure_ascii=False))
