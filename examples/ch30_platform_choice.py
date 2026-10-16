"""Offline chapter 30 exercise: explicit requirements and a toy monthly estimate.

Inputs are invented assumptions, not platform measurements or capability discovery.
No APIs, deployments, benchmarks or production approvals are performed.
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP, localcontext
import json


def review(requirements, checks):
    validate_checks(requirements, checks)
    failed = [name for name in requirements if checks.get(name) is False]
    unknown = [name for name in requirements if checks.get(name) is None]
    if failed:
        status = "reject"
    elif unknown:
        status = "needs_evidence"
    else:
        status = "passes_supplied_checks"
    return {"status": status, "failed": failed, "unknown": unknown}


def validate_checks(requirements, checks):
    if not isinstance(requirements, tuple) or not requirements:
        raise ValueError("requirements must be a nonempty tuple")
    if any(not isinstance(name, str) or not name.strip() for name in requirements):
        raise ValueError("requirement names must be nonempty strings")
    if len(set(requirements)) != len(requirements):
        raise ValueError("duplicate requirement")
    if not isinstance(checks, dict) or set(checks) - set(requirements):
        raise ValueError("unknown check or invalid mapping")
    if any(value is not None and type(value) is not bool for value in checks.values()):
        raise ValueError("checks must be True, False or None")


def number(value):
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise ValueError("use finite nonnegative decimal strings, integers or Decimal")
    try:
        result = Decimal(value)
    except ArithmeticError as exc:
        raise ValueError("invalid decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("use finite nonnegative numbers")
    return result


@dataclass(frozen=True)
class Load:
    sessions: int = 1000
    month_hours: str = "720"
    occupied_fraction: str = "0.05"
    wakes_per_session: int = 120
    hourly_price: str = "0.04"
    stored_gib_per_session: str = "4"
    gib_month_price: str = "0.025"

    def __post_init__(self):
        if type(self.sessions) is not int or self.sessions < 1:
            raise ValueError("sessions must be a positive integer")
        if type(self.wakes_per_session) is not int or self.wakes_per_session < 0:
            raise ValueError("wakes must be a nonnegative integer")
        for name in ("month_hours", "occupied_fraction", "hourly_price",
                     "stored_gib_per_session", "gib_month_price"):
            number(getattr(self, name))
        if number(self.month_hours) <= 0 or number(self.occupied_fraction) > 1:
            raise ValueError("invalid hours or occupied fraction")


@dataclass(frozen=True)
class Option:
    pinned: bool
    transition_seconds: str
    extra_reserve_slots: int
    platform_monthly: str

    def __post_init__(self):
        if type(self.pinned) is not bool:
            raise ValueError("pinned must be boolean")
        number(self.transition_seconds)
        number(self.platform_monthly)
        if type(self.extra_reserve_slots) is not int or self.extra_reserve_slots < 0:
            raise ValueError("reserve must be a nonnegative integer")
        if self.pinned and (number(self.transition_seconds) or self.extra_reserve_slots):
            raise ValueError("pinned estimate already includes all environment hours")


def monthly_cost(load, option):
    """One slot is the same resource bundle in all synthetic options.

    occupied_fraction includes useful work and waits holding the slot, but excludes
    the modeled transition. transition_seconds is a MEAN total billed slot time
    per wake, including amortized suspend/export work; it is not a p95 latency.
    Extra reserve is idle capacity in addition to occupied/transition slot-hours.
    Stored GiB is a simplified all-in billable footprint, not snapshot logical size.
    """
    if not isinstance(load, Load) or not isinstance(option, Option):
        raise ValueError("expected Load and Option")
    with localcontext() as ctx:
        ctx.prec = 28
        n, hours, price = number(load.sessions), number(load.month_hours), number(load.hourly_price)
        fraction = Decimal(1) if option.pinned else number(load.occupied_fraction)
        compute = n * hours * fraction * price
        transition_hours = n * load.wakes_per_session * number(option.transition_seconds) / 3600
        if not option.pinned and n * hours * fraction + transition_hours > n * hours:
            raise ValueError("per-session occupation exceeds the modeled month")
        transition = transition_hours * price
        reserve = number(option.extra_reserve_slots) * hours * price
        storage = n * number(load.stored_gib_per_session) * number(load.gib_month_price)
        platform = number(option.platform_monthly)
        return {"compute": compute, "transitions": transition, "reserve": reserve,
                "storage": storage, "platform": platform,
                "total": compute + transition + reserve + storage + platform}


def money(value):
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


REQUIREMENTS = ("isolation", "durable_state", "return_slo", "operations")
OPTIONS = {
    "pinned": Option(True, "0", 0, "500"),
    "jobs": Option(False, "120", 0, "500"),
    "ax_data": Option(False, "10", 20, "800"),
}


def demo():
    load = Load()
    checks = {
        "pinned": dict.fromkeys(REQUIREMENTS, True),
        "jobs": {"isolation": True, "durable_state": True,
                 "return_slo": False, "operations": True},
        "ax_data": {"isolation": True, "durable_state": True,
                    "return_slo": True, "operations": None},
    }
    result = {}
    for name, option in OPTIONS.items():
        result[name] = {"monthly": {k: money(v) for k, v in monthly_cost(load, option).items()},
                        "review": review(REQUIREMENTS, checks[name])}
    result["relaxed_jobs"] = review(REQUIREMENTS, dict.fromkeys(REQUIREMENTS, True))
    result["ax_full_required"] = review(("individual_ram",), {"individual_ram": False})
    result["small_ax"] = {k: money(v) for k, v in monthly_cost(Load(sessions=10), OPTIONS["ax_data"]).items()}
    result["small_pinned"] = {k: money(v) for k, v in monthly_cost(Load(sessions=10), OPTIONS["pinned"]).items()}
    result["synthetic_inputs"] = True
    result["platform_validated"] = False
    return result


if __name__ == "__main__":
    print(json.dumps(demo(), ensure_ascii=False, indent=2))
