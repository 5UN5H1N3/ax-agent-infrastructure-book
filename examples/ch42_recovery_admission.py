"""Offline recovery admission exercise, not an authentication implementation.

Target and its durable receipt are the SQLite fixture from chapter 37. Only
fixture setup applies an effect; assessment uses lookup only. Checkpoint and
current authority are separate immutable inputs. All security observations are
trusted fixtures: there is no JWT verification, real fencing, KMS, restore,
Kubernetes, network or model call. READY_FOR_CANARY is not a write grant.
"""
from dataclasses import dataclass, replace
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from ch37_operation_ledger import (
    Action, Conflict, KEY, OPERATION, RUN, Target, WorkerStopped,
)


@dataclass(frozen=True)
class Checkpoint:
    run: str = RUN
    operation: str = OPERATION
    key: str = KEY
    action_digest: str = Action().digest()
    local_state: str = "DISPATCHING"


@dataclass(frozen=True)
class Credential:
    """Metadata already established by a trusted verifier; never token bytes."""
    record_id: str = "cred-v2"
    generation: int = 2
    tenant: str = "lab"
    audience: str = "lab-network-writer"
    expires_at: int = 900


@dataclass(frozen=True)
class CurrentAuthority:
    """Current external authority, NOT restored from the Actor checkpoint."""
    available: bool = True
    lookup_allowed: bool = True  # Separate recovery-reader permission.
    followup_allowed: bool = True
    min_generation: int = 2
    revoked: frozenset = frozenset({"cred-v1"})


@dataclass(frozen=True)
class RecoveryEvidence:
    """Trusted, current observations for this isolated recovery exercise."""
    old_writers_fenced: bool = True
    data_verified: bool = True
    tenant_boundary_checked: bool = True


def credential_problem(credential, authority, now):
    if type(now) is not int or now < 0:
        raise ValueError("nonnegative integer fixture time required")
    if credential.tenant != "lab" or credential.audience != "lab-network-writer":
        return "HOLD_CREDENTIAL_SCOPE"
    if type(credential.generation) is not int or credential.generation < 1:
        return "HOLD_CREDENTIAL_METADATA"
    if type(credential.expires_at) is not int or credential.expires_at <= now:
        return "HOLD_CREDENTIAL_EXPIRED"
    if credential.record_id in authority.revoked:
        return "HOLD_CREDENTIAL_REVOKED"
    if credential.generation < authority.min_generation:
        return "HOLD_CREDENTIAL_GENERATION"
    return None


def assess(target, checkpoint=Checkpoint(), *, credential=Credential(),
           authority=CurrentAuthority(), evidence=RecoveryEvidence(), now=600):
    """Read one historical receipt, then assess readiness for a canary.

    A missing receipt is UNKNOWN: lookup absence alone does not establish that
    a real remote write never happened. No apply/retry path exists here.
    """
    if type(now) is not int or now < 0:
        raise ValueError("nonnegative integer fixture time required")
    result = {"gate": "HOLD", "historical_effect": "NOT_CHECKED",
              "operation_next": "DO_NOT_REPLAY", "write_admission": "CLOSED",
              "assessment_mutations": 0, "production_ready": False}

    def hold(reason):
        return {**result, "gate": reason}

    if evidence.old_writers_fenced is not True:
        return hold("HOLD_FENCING")
    if evidence.data_verified is not True:
        return hold("HOLD_DATA")
    binding = (checkpoint.run, checkpoint.operation, checkpoint.key,
               checkpoint.action_digest, checkpoint.local_state)
    expected = (RUN, OPERATION, KEY, Action().digest(), "DISPATCHING")
    if binding != expected:
        return hold("HOLD_CHECKPOINT_BINDING")
    if authority.available is not True:
        return hold("HOLD_CURRENT_AUTHORITY")
    if authority.lookup_allowed is not True:
        return hold("HOLD_LOOKUP_PERMISSION")
    try:
        receipt = target.lookup(checkpoint.key, Action())
    except Conflict:
        return hold("HOLD_RECEIPT_CONFLICT")
    except (TimeoutError, ConnectionError):
        return hold("HOLD_LOOKUP_UNAVAILABLE")
    if receipt is None:
        result["historical_effect"] = "UNKNOWN"
        return hold("HOLD_OUTCOME_UNKNOWN")
    if (receipt.get("key") != checkpoint.key
            or receipt.get("digest") != checkpoint.action_digest
            or receipt.get("applied_revision") != "config-18"
            or receipt.get("local_pref") != 150):
        return hold("HOLD_RECEIPT_CONFLICT")
    result.update(historical_effect="APPLIED", operation_next="VERIFY_EXISTING_EFFECT")
    # Historical lookup uses the recovery reader, not the restored write token.
    problem = credential_problem(credential, authority, now)
    if problem:
        return hold(problem)
    if authority.followup_allowed is not True:
        return hold("HOLD_CURRENT_PERMISSION")
    if evidence.tenant_boundary_checked is not True:
        return hold("HOLD_TENANT_BOUNDARY")
    return {**result, "gate": "READY_FOR_CANARY"}


def fixture_target(path, *, applied=True):
    target = Target(path)
    if applied:
        try:
            target.apply(KEY, Action(), fault="stop_after_commit")
        except WorkerStopped:
            pass
        assert target.effect_count() == 1
    return target


def demo():
    with TemporaryDirectory(prefix="ax-ch42-") as directory:
        target = fixture_target(Path(directory) / "target.db")
        empty = fixture_target(Path(directory) / "empty.db", applied=False)
        denied = CurrentAuthority(followup_allowed=False)
        old = Credential(record_id="cred-v1", generation=1)
        cases = {
            "restored_credential": assess(target, credential=old, authority=denied),
            "fresh_credential_old_permission": assess(target, authority=denied),
            "receipt_unavailable": assess(empty),
            "current_checks_pass": assess(target),
            "old_writer_unfenced": assess(
                target, evidence=replace(RecoveryEvidence(), old_writers_fenced=False)),
        }
        assert target.effect_count() == 1 and empty.effect_count() == 0
        return {"cases": cases, "fixture_effects_before_assessment": 1,
                "fixture_effects_after_assessment": target.effect_count(),
                "external_calls": 0, "live_restore_executed": False}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
