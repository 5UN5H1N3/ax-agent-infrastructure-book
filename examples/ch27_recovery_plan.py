"""Offline recovery-planning exercise, NOT an AX/Substrate backup format.

Synthetic bytes, declared compatibility and trusted target observations only.
No shell, network, database, Kubernetes, runtime or backup/restore calls.
No writes are authorized or performed; production_ready is always False.
"""
from dataclasses import dataclass, replace
import hashlib
import json


def sha(data):
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Point:
    name: str
    cut: int
    task_actor: str
    db_actor: str
    db_snapshot: str
    blob_name: str
    blob: bytes | None
    digest: str
    snapshot_format: str = "demo-v1"
    completed: bool = True
    key_available: bool = True


def point(name="p120", cut=120):
    body = f"DEMO-ONLY billing checkpoint {cut}".encode()
    return Point(name, cut, "actor-7", "actor-7", name, name, body, sha(body))


def select_point(points, incident_at, reader="demo-v1", compatible=frozenset({("demo-v1", "demo-v1")})):
    if type(incident_at) is not int or incident_at < 0 or not reader:
        raise ValueError("invalid_incident")
    if len({p.name for p in points}) != len(points) or any(not p.name for p in points):
        raise ValueError("ambiguous_catalog")
    if len({p.cut for p in points}) != len(points):
        raise ValueError("ambiguous_cut")
    if any(type(p.cut) is not int or p.cut < 0 for p in points):
        raise ValueError("invalid_cut")
    rejected = {}
    for p in sorted(points, key=lambda p: p.cut, reverse=True):
        if p.cut > incident_at:
            reason = "future_cut"
        elif not p.completed:
            reason = "incomplete"
        elif (p.task_actor, p.db_actor) != ("actor-7", "actor-7"):
            reason = "mapping_mismatch"
        elif not p.db_snapshot or p.db_snapshot != p.blob_name:
            reason = "reference_mismatch"
        elif p.blob is None:
            reason = "missing_blob"
        elif sha(p.blob) != p.digest:
            reason = "digest_mismatch"
        elif not p.key_available:
            reason = "key_unavailable"
        elif (p.snapshot_format, reader) not in compatible:
            reason = "compatibility_unknown"
        else:
            return p, rejected
        rejected[p.name] = reason
    return None, rejected


@dataclass(frozen=True)
class Observation:
    state: str = "applied"
    operation: str = "publish-742"
    payload: str = sha(b"candidate-C1")
    receipt: str | None = "receipt-126"
    definitive_absence: bool = False


def reconcile(observed, *, old_writer_fenced, journal_available, idempotency_valid):
    if observed.state not in {"applied", "not_applied", "unknown"}:
        raise ValueError("invalid_target_state")
    if not old_writer_fenced:
        return "hold_fencing"
    if not journal_available:
        return "hold_journal"
    if observed.operation != "publish-742" or observed.payload != sha(b"candidate-C1"):
        return "hold_conflict"
    if observed.state == "applied" and observed.receipt:
        return "already_applied"
    if observed.state == "not_applied" and observed.definitive_absence and idempotency_valid:
        return "retry_same_operation"
    return "hold_unknown"


def recovery_budget(incident_at, cut, verified_at, rpo_limit, rto_limit):
    values = [incident_at, cut, rpo_limit, rto_limit]
    if any(type(v) is not int or v < 0 for v in values) or cut > incident_at:
        raise ValueError("invalid_budget_input")
    if verified_at is not None and (
            type(verified_at) is not int or verified_at < incident_at):
        raise ValueError("invalid_verification_time")
    exposure = incident_at - cut
    elapsed = None if verified_at is None else verified_at - incident_at
    return {"exposure_s": exposure, "recovery_elapsed_s": elapsed,
            "rpo_met": exposure <= rpo_limit,
            "rto_met": None if elapsed is None else elapsed <= rto_limit}


def assess(points, *, observation=Observation(), old_writer_fenced=False,
           journal_available=False, idempotency_valid=False, verified_at=None):
    chosen, rejected = select_point(points, 130)
    result = {"point": None if chosen is None else chosen.name, "rejected": rejected,
              "effect": "not_checked", "exposure_s": None, "recovery_elapsed_s": None,
              "production_ready": False, "live_restore_executed": False}
    if chosen is None:
        return {**result, "gate": "hold_no_point"}
    effect = reconcile(observation, old_writer_fenced=old_writer_fenced,
                       journal_available=journal_available, idempotency_valid=idempotency_valid)
    # Even a claimed verification time is not usable while the effect is unresolved.
    budget = recovery_budget(130, chosen.cut,
                             verified_at if effect == "already_applied" else None, 15, 45)
    result.update(budget, effect=effect)
    if effect.startswith("hold_"):
        gate = effect
    elif effect == "retry_same_operation":
        gate = "hold_retry_needed"
    elif verified_at is None:
        gate = "hold_verification"
    elif not budget["rpo_met"]:
        gate = "hold_rpo"
    elif not budget["rto_met"]:
        gate = "hold_rto"
    else:
        gate = "model_pass"
    return {**result, "gate": gate}


def worked_case(points, **changes):
    """Explicit trusted fixture, not observations from a recovery system."""
    values = dict(old_writer_fenced=True, journal_available=True,
                  idempotency_valid=True, verified_at=170)
    values.update(changes)
    return assess(points, **values)


def demo():
    p = point()
    scenarios = {
        "good": worked_case([replace(point("p125", 125), completed=False), p]),
        "old_fallback": worked_case([replace(point("p125", 125), blob=None), point("p110", 110)]),
        "corrupt_blob": worked_case([replace(p, blob=b"corrupted")]),
        "mapping_lost": worked_case([replace(p, task_actor="other-actor")]),
        "unknown_format": worked_case([replace(p, snapshot_format="demo-v2")]),
        "missing_key": worked_case([replace(p, key_available=False)]),
        "old_writer_live": worked_case([p], old_writer_fenced=False),
        "unknown_effect": worked_case([p], observation=Observation(state="unknown", receipt=None)),
        "retry_needed": worked_case([p], observation=Observation(state="not_applied", receipt=None,
                                                            definitive_absence=True)),
        "too_slow": worked_case([p], verified_at=180),
    }
    return scenarios


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
