"""Offline read-only recovery planner over trusted, complete fixture inventory.

These dataclasses are NOT Substrate API objects or real snapshot manifests.
No checkpoint, filesystem restore, object store, RPC, authentication, concurrent
inventory collection or deletion is implemented. A successful local preflight
does not prove runtime compatibility, integrity or application recovery.
"""
from dataclasses import dataclass, field, replace
import json

from ch37_operation_ledger import Action


ACTION_SHA256 = Action().digest()


@dataclass(frozen=True)
class Checkpoint:
    run: str = "lab-network-017"
    operation: str = "op-17"
    action_sha256: str = ACTION_SHA256
    schema: int = 1
    local_operation_view: str = "DISPATCHING"


@dataclass(frozen=True)
class Snapshot:
    owner_kind: str
    owner_uid: str
    captured_actor_uid: str
    template_uid: str = "template-1"
    scope: str = "DATA"
    manifest_files: tuple = ("workspace.fixture",)
    available_files: frozenset = frozenset({"workspace.fixture"})
    manifest_present: bool = True
    runtime_profile: str = "fixture-runtime-1"
    checkpoint: Checkpoint | None = field(default_factory=Checkpoint)


@dataclass(frozen=True)
class Template:
    uid: str = "template-1"
    from_data: str = "GOLDEN"
    golden_tag: str | None = "tag-golden"
    runtime_profile: str = "fixture-runtime-1"
    checkpoint_schema: int = 1


@dataclass(frozen=True)
class Actor:
    uid: str
    external_uri: str
    template_uid: str = "template-1"
    in_progress_uri: str | None = None


@dataclass(frozen=True)
class Tag:
    uid: str
    uri: str
    ready: bool = True
    template_uid: str = "template-1"


@dataclass(frozen=True)
class Receipt:
    run: str = "lab-network-017"
    operation: str = "op-17"
    action_sha256: str = ACTION_SHA256
    state: str = "APPLIED"
    goal: str = "NOT_EVALUATED"


@dataclass
class Inventory:
    actors: dict
    templates: dict
    tags: dict
    snapshots: dict
    receipt: Receipt | None = field(default_factory=Receipt)
    complete: bool = True


class Hold(Exception):
    pass


def require(condition, reason):
    if not condition:
        raise Hold(reason)


def payload_check(snapshot):
    require(type(snapshot.manifest_present) is bool and snapshot.manifest_present, "manifest_missing")
    names = snapshot.manifest_files
    require(type(names) is tuple and bool(names), "invalid_file_list")
    require(all(type(n) is str and n and n not in (".", "..", "manifest.json")
                and "/" not in n and "\\" not in n for n in names), "invalid_file_list")
    require(len(set(names)) == len(names), "invalid_file_list")
    require(all(n in snapshot.available_files for n in names), "payload_missing")


def owned_snapshot(inv, uri):
    require(uri in inv.snapshots, "snapshot_missing")
    snap = inv.snapshots[uri]
    require(snap.scope in ("FULL", "DATA"), "unknown_scope")
    if snap.owner_kind == "TAG":
        tag = inv.tags.get(snap.owner_uid)
        require(tag is not None and tag.uid == snap.owner_uid, "owner_tag_missing")
        require(type(tag.ready) is bool and tag.ready and tag.uri == uri, "tag_not_finalized")
        require(tag.template_uid == snap.template_uid, "tag_provenance_mismatch")
    else:
        require(snap.owner_kind == "ACTOR", "unknown_owner")
        owner = inv.actors.get(snap.owner_uid)
        require(owner is not None and owner.uid == snap.owner_uid, "owner_actor_missing")
        require(owner.external_uri == uri, "not_current_actor_snapshot")
    payload_check(snap)
    return snap


def restore_plan(inv, actor_uid):
    result = {"decision": "HOLD", "dependencies": [], "mutation_gate": "CLOSED",
              "automatic_mutations": 0, "runtime_restore_executed": False}
    try:
        require(inv.complete is True, "inventory_incomplete")
        actor = inv.actors.get(actor_uid)
        require(actor is not None and actor.uid == actor_uid, "actor_missing")
        tmpl = inv.templates.get(actor.template_uid)
        require(tmpl is not None and tmpl.uid == actor.template_uid, "template_missing")
        # Only the committed pointer is selected. In-progress is never promoted.
        snap = owned_snapshot(inv, actor.external_uri)
        require(snap.owner_kind == "TAG" or snap.owner_uid == actor.uid, "foreign_actor_snapshot")
        result["dependencies"].append(actor.external_uri)
        require(snap.template_uid == tmpl.uid, "template_migration_requires_review")
        require(snap.runtime_profile == tmpl.runtime_profile, "runtime_profile_requires_review")
        if snap.scope == "FULL":
            path = "FULL"
        else:
            require(tmpl.from_data in ("COLD_BOOT", "GOLDEN"), "unknown_data_policy")
            path = "DATA_COLD_BOOT"
            if tmpl.from_data == "GOLDEN":
                tag = inv.tags.get(tmpl.golden_tag)
                require(tag is not None, "golden_tag_missing")
                base = owned_snapshot(inv, tag.uri)
                require(base.owner_kind == "TAG" and base.owner_uid == tag.uid, "golden_owner_mismatch")
                require(base.template_uid == tmpl.uid, "golden_template_mismatch")
                require(base.scope == "FULL", "golden_requires_full")
                require(base.runtime_profile == tmpl.runtime_profile, "golden_runtime_requires_review")
                result["dependencies"].append(tag.uri)
                path = "DATA_ON_GOLDEN"
        cp = snap.checkpoint
        require(isinstance(cp, Checkpoint), "application_checkpoint_missing")
        require(type(cp.schema) is int and type(tmpl.checkpoint_schema) is int
                and cp.schema == tmpl.checkpoint_schema, "checkpoint_schema_mismatch")
        require(all(type(v) is str and 0 < len(v) <= 100 for v in (cp.run, cp.operation))
                and type(cp.action_sha256) is str
                and len(cp.action_sha256) == 64
                and all(c in "0123456789abcdef" for c in cp.action_sha256), "invalid_operation_identity")
        receipt = inv.receipt
        require(isinstance(receipt, Receipt), "external_receipt_missing")
        require((receipt.run, receipt.operation, receipt.action_sha256) ==
                (cp.run, cp.operation, cp.action_sha256), "operation_identity_mismatch")
        require(receipt.state == "APPLIED", "resolve_operation_before_recovery")
        require(receipt.goal in ("NOT_EVALUATED", "PASSED", "FAILED"), "invalid_goal_state")
        result.update(decision="PREFLIGHT_ONLY", restore_path=path,
                      captured_actor_uid=snap.captured_actor_uid,
                      payload_owner=f"{snap.owner_kind}:{snap.owner_uid}",
                      local_operation_view=cp.local_operation_view,
                      authoritative_operation=receipt.state,
                      next_step={"NOT_EVALUATED": "VERIFY_GOAL", "PASSED": "REVIEW_COMPLETION",
                                 "FAILED": "REVIEW_FAILED_GOAL"}[receipt.goal])
    except Hold as exc:
        result["reason"] = str(exc)
    return result


def deletion_review(inv, tag_uid, *, creation_barrier_confirmed=False):
    """Diagnostic only. The boolean is a trusted fixture, not a real barrier.

    A real lifecycle barrier + fresh inventory + UID/version guards + authority
    are needed outside this model. No branch grants permission to delete.
    """
    result = {"decision": "HOLD", "borrowers": [], "golden_templates": [],
              "automatic_mutations": 0}
    if inv.complete is not True or creation_barrier_confirmed is not True:
        return {**result, "reason": "need_complete_inventory_and_creation_barrier"}
    tag = inv.tags.get(tag_uid)
    if tag is None:
        return {**result, "reason": "tag_missing"}
    result["borrowers"] = sorted(a.uid for a in inv.actors.values() if a.external_uri == tag.uri)
    result["golden_templates"] = sorted(t.uid for t in inv.templates.values() if t.golden_tag == tag_uid)
    if result["borrowers"] or result["golden_templates"]:
        return {**result, "reason": "referenced"}
    return {**result, "decision": "MANUAL_REVIEW_NO_KNOWN_REFERENCES"}


def fixtures():
    own = "fixture://actors/actor-a/snapshots/s1"
    copy = "fixture://tags/tag-copy"
    golden = "fixture://tags/tag-golden"
    a = Snapshot("ACTOR", "actor-a", "actor-a")
    t = replace(a, owner_kind="TAG", owner_uid="tag-copy")
    g = Snapshot("TAG", "tag-golden", "golden-actor", scope="FULL", checkpoint=None)
    return Inventory(actors={"actor-a": Actor("actor-a", own), "actor-b": Actor("actor-b", copy)},
                     templates={"template-1": Template()},
                     tags={"tag-copy": Tag("tag-copy", copy), "tag-golden": Tag("tag-golden", golden)},
                     snapshots={own: a, copy: t, golden: g})


def demo():
    inv = fixtures()
    before = restore_plan(inv, "actor-b")
    held = deletion_review(inv, "tag-copy", creation_barrier_confirmed=True)
    # Prepared inventory after B's own completed suspend, not a SuspendActor call.
    own = "fixture://actors/actor-b/snapshots/s2"
    inv.snapshots[own] = replace(inv.snapshots[inv.actors["actor-b"].external_uri],
                                 owner_kind="ACTOR", owner_uid="actor-b", captured_actor_uid="actor-b")
    inv.actors["actor-b"] = replace(inv.actors["actor-b"], external_uri=own)
    return {"borrowed_restore": before, "tag_copy_while_borrowed": held,
            "after_own_suspend": restore_plan(inv, "actor-b"),
            "tag_copy_after_own_suspend": deletion_review(inv, "tag-copy", creation_barrier_confirmed=True),
            "golden_after_own_suspend": deletion_review(inv, "tag-golden", creation_barrier_confirmed=True),
            "network_calls": 0}


if __name__ == "__main__":
    print(json.dumps(demo(), indent=2))
