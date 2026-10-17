from copy import deepcopy
from dataclasses import replace
import unittest

import ch39_restore_preflight as m


class RestorePreflightTests(unittest.TestCase):
    def setUp(self):
        self.inv = m.fixtures()
        self.uri = self.inv.actors["actor-b"].external_uri

    def plan(self):
        return m.restore_plan(self.inv, "actor-b")

    def snapshot(self, **values):
        self.inv.snapshots[self.uri] = replace(self.inv.snapshots[self.uri], **values)

    def checkpoint(self, **values):
        self.snapshot(checkpoint=replace(self.inv.snapshots[self.uri].checkpoint, **values))

    def template(self, **values):
        self.inv.templates["template-1"] = replace(self.inv.templates["template-1"], **values)

    def test_data_golden_needs_two_sources(self):
        r = self.plan()
        self.assertEqual(r["decision"], "PREFLIGHT_ONLY")
        self.assertEqual(r["restore_path"], "DATA_ON_GOLDEN")
        self.assertEqual(r["dependencies"], [self.uri, "fixture://tags/tag-golden"])

    def test_provenance_is_not_payload_owner(self):
        r = self.plan()
        self.assertEqual(r["captured_actor_uid"], "actor-a")
        self.assertEqual(r["payload_owner"], "TAG:tag-copy")

    def test_rollback_does_not_erase_external_applied(self):
        r = self.plan()
        self.assertEqual(m.ACTION_SHA256, m.Action().digest())
        self.assertEqual(r["local_operation_view"], "DISPATCHING")
        self.assertEqual(r["authoritative_operation"], "APPLIED")
        self.assertEqual(r["next_step"], "VERIFY_GOAL")

    def test_no_restore_or_mutation_is_claimed(self):
        r = self.plan()
        self.assertEqual(r["mutation_gate"], "CLOSED")
        self.assertFalse(r["runtime_restore_executed"])
        self.assertEqual(r["automatic_mutations"], 0)

    def test_source_actor_can_be_gone(self):
        del self.inv.actors["actor-a"]
        self.assertEqual(self.plan()["decision"], "PREFLIGHT_ONLY")

    def test_new_source_snapshot_does_not_retarget_copy(self):
        self.inv.actors["actor-a"] = replace(self.inv.actors["actor-a"], external_uri="fixture://actors/actor-a/snapshots/s3")
        self.assertEqual(self.plan()["dependencies"][0], self.uri)

    def test_in_progress_is_not_selected(self):
        self.inv.actors["actor-b"] = replace(self.inv.actors["actor-b"], in_progress_uri="fixture://newer")
        self.inv.snapshots["fixture://newer"] = replace(self.inv.snapshots[self.uri], manifest_present=False)
        self.assertEqual(self.plan()["dependencies"][0], self.uri)

    def test_in_progress_does_not_replace_missing_current(self):
        self.inv.actors["actor-b"] = replace(self.inv.actors["actor-b"], external_uri="fixture://missing", in_progress_uri=self.uri)
        self.assertEqual(self.plan()["reason"], "snapshot_missing")

    def test_manifest_without_payload_is_held(self):
        self.snapshot(available_files=frozenset())
        self.assertEqual(self.plan()["reason"], "payload_missing")

    def test_payload_without_manifest_is_held(self):
        self.snapshot(manifest_present=False)
        self.assertEqual(self.plan()["reason"], "manifest_missing")

    def test_invalid_manifest_filenames(self):
        for names in [(), ("../x",), ("a/b",), ("a\\b",), (".",), ("..",), ("manifest.json",), (1,), ("x", "x"), ["workspace.fixture"]]:
            with self.subTest(names=names):
                self.snapshot(manifest_files=names)
                self.assertEqual(self.plan()["reason"], "invalid_file_list")

    def test_pending_tag_is_not_ready(self):
        self.inv.tags["tag-copy"] = replace(self.inv.tags["tag-copy"], ready=False)
        self.assertEqual(self.plan()["reason"], "tag_not_finalized")

    def test_string_ready_is_rejected(self):
        self.inv.tags["tag-copy"] = replace(self.inv.tags["tag-copy"], ready="False")
        self.assertEqual(self.plan()["reason"], "tag_not_finalized")

    def test_tag_row_absence_blocks_even_if_bytes_exist(self):
        del self.inv.tags["tag-copy"]
        self.assertEqual(self.plan()["reason"], "owner_tag_missing")

    def test_tag_provenance_must_match_snapshot(self):
        self.inv.tags["tag-copy"] = replace(self.inv.tags["tag-copy"], template_uid="another")
        self.assertEqual(self.plan()["reason"], "tag_provenance_mismatch")

    def test_new_actor_uid_does_not_adopt_old_owned_bytes(self):
        old_uri = self.inv.actors["actor-a"].external_uri
        self.inv.actors["actor-b"] = replace(self.inv.actors["actor-b"], external_uri=old_uri)
        self.assertEqual(self.plan()["reason"], "foreign_actor_snapshot")

    def test_owned_old_point_is_not_promoted_to_current(self):
        old_uri = self.inv.actors["actor-a"].external_uri
        self.inv.actors["actor-a"] = replace(self.inv.actors["actor-a"], external_uri="fixture://new")
        self.inv.actors["actor-b"] = replace(self.inv.actors["actor-b"], external_uri=old_uri)
        self.assertEqual(self.plan()["reason"], "not_current_actor_snapshot")

    def test_full_ignores_data_golden_policy(self):
        self.snapshot(scope="FULL")
        del self.inv.tags["tag-golden"]
        self.assertEqual(self.plan()["restore_path"], "FULL")
        self.assertEqual(self.plan()["dependencies"], [self.uri])

    def test_cold_boot_does_not_require_golden(self):
        self.template(from_data="COLD_BOOT")
        del self.inv.tags["tag-golden"]
        self.assertEqual(self.plan()["restore_path"], "DATA_COLD_BOOT")

    def test_golden_dependency_remains_after_own_suspend(self):
        r = m.demo()
        self.assertEqual(r["after_own_suspend"]["restore_path"], "DATA_ON_GOLDEN")
        self.assertEqual(r["golden_after_own_suspend"]["reason"], "referenced")

    def test_missing_golden_is_not_silent_cold_boot(self):
        del self.inv.tags["tag-golden"]
        self.assertEqual(self.plan()["reason"], "golden_tag_missing")

    def test_data_golden_cannot_supply_guest_state(self):
        uri = self.inv.tags["tag-golden"].uri
        self.inv.snapshots[uri] = replace(self.inv.snapshots[uri], scope="DATA")
        self.assertEqual(self.plan()["reason"], "golden_requires_full")

    def test_other_golden_template_is_refused(self):
        uri = self.inv.tags["tag-golden"].uri
        self.inv.tags["tag-golden"] = replace(self.inv.tags["tag-golden"], template_uid="other")
        self.inv.snapshots[uri] = replace(self.inv.snapshots[uri], template_uid="other")
        self.assertEqual(self.plan()["reason"], "golden_template_mismatch")

    def test_template_migration_requires_operator_review(self):
        self.inv.templates["template-2"] = replace(m.Template(), uid="template-2")
        self.inv.actors["actor-b"] = replace(self.inv.actors["actor-b"], template_uid="template-2")
        self.assertEqual(self.plan()["reason"], "template_migration_requires_review")

    def test_runtime_profile_mismatch(self):
        self.snapshot(runtime_profile="fixture-runtime-2")
        self.assertEqual(self.plan()["reason"], "runtime_profile_requires_review")

    def test_schema_mismatch(self):
        self.checkpoint(schema=2)
        self.assertEqual(self.plan()["reason"], "checkpoint_schema_mismatch")

    def test_boolean_is_not_schema_version(self):
        self.checkpoint(schema=True)
        self.assertEqual(self.plan()["reason"], "checkpoint_schema_mismatch")

    def test_missing_checkpoint(self):
        self.snapshot(checkpoint=None)
        self.assertEqual(self.plan()["reason"], "application_checkpoint_missing")

    def test_missing_external_receipt(self):
        self.inv.receipt = None
        self.assertEqual(self.plan()["reason"], "external_receipt_missing")

    def test_operation_identity_components(self):
        for field in ["run", "operation", "action_sha256"]:
            with self.subTest(field=field):
                self.inv.receipt = replace(m.Receipt(), **{field: "other"})
                self.assertEqual(self.plan()["reason"], "operation_identity_mismatch")

    def test_unknown_outcome_never_grants_retry(self):
        for state in ["OUTCOME_UNKNOWN", "DISPATCHING", "PREPARED", "unknown"]:
            with self.subTest(state=state):
                self.inv.receipt = replace(m.Receipt(), state=state)
                r = self.plan()
                self.assertEqual(r["reason"], "resolve_operation_before_recovery")
                self.assertEqual(r["automatic_mutations"], 0)

    def test_goal_failed_is_not_hidden_by_applied(self):
        self.inv.receipt = replace(m.Receipt(), goal="FAILED")
        self.assertEqual(self.plan()["next_step"], "REVIEW_FAILED_GOAL")

    def test_goal_passed_remains_review_only(self):
        self.inv.receipt = replace(m.Receipt(), goal="PASSED")
        self.assertEqual(self.plan()["next_step"], "REVIEW_COMPLETION")

    def test_unknown_goal_is_refused(self):
        self.inv.receipt = replace(m.Receipt(), goal="SUCCESS")
        self.assertEqual(self.plan()["reason"], "invalid_goal_state")

    def test_incomplete_inventory_is_refused(self):
        self.inv.complete = False
        self.assertEqual(self.plan()["reason"], "inventory_incomplete")

    def test_unknown_scope_is_refused(self):
        self.snapshot(scope="UNSPECIFIED")
        self.assertEqual(self.plan()["reason"], "unknown_scope")

    def test_invalid_operation_identity(self):
        for changes in [{"run": True}, {"operation": ""}, {"run": "x" * 101},
                        {"action_sha256": "not-a-digest"}]:
            with self.subTest(changes=changes):
                self.snapshot(checkpoint=replace(m.Checkpoint(), **changes))
                self.assertEqual(self.plan()["reason"], "invalid_operation_identity")

    def test_missing_payload_in_golden_blocks_plan(self):
        uri = self.inv.tags["tag-golden"].uri
        self.inv.snapshots[uri] = replace(self.inv.snapshots[uri], available_files=frozenset())
        self.assertEqual(self.plan()["reason"], "payload_missing")

    def test_plan_does_not_mutate_inventory(self):
        old = deepcopy(self.inv)
        self.plan()
        self.assertEqual(self.inv, old)

    def test_delete_requires_barrier(self):
        self.assertEqual(m.deletion_review(self.inv, "tag-copy")["reason"], "need_complete_inventory_and_creation_barrier")

    def test_borrower_blocks_tag_cleanup(self):
        r = m.deletion_review(self.inv, "tag-copy", creation_barrier_confirmed=True)
        self.assertEqual(r["decision"], "HOLD")
        self.assertEqual(r["borrowers"], ["actor-b"])

    def test_template_dependency_survives_without_actors(self):
        self.inv.actors = {}
        r = m.deletion_review(self.inv, "tag-golden", creation_barrier_confirmed=True)
        self.assertEqual(r["golden_templates"], ["template-1"])
        self.assertEqual(r["decision"], "HOLD")

    def test_deletion_review_never_deletes_or_grants_permission(self):
        self.inv.actors.pop("actor-b")
        old = deepcopy(self.inv)
        r = m.deletion_review(self.inv, "tag-copy", creation_barrier_confirmed=True)
        self.assertEqual(r["decision"], "MANUAL_REVIEW_NO_KNOWN_REFERENCES")
        self.assertEqual(r["automatic_mutations"], 0)
        self.assertEqual(self.inv, old)

    def test_demo_releases_only_direct_borrowing(self):
        r = m.demo()
        self.assertEqual(r["tag_copy_after_own_suspend"]["decision"], "MANUAL_REVIEW_NO_KNOWN_REFERENCES")
        self.assertEqual(r["golden_after_own_suspend"]["decision"], "HOLD")
        self.assertEqual(r["network_calls"], 0)


if __name__ == "__main__":
    unittest.main()
