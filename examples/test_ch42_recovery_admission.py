from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from ch37_operation_ledger import Action, Conflict, KEY, Target
from ch42_recovery_admission import (
    Checkpoint, Credential, CurrentAuthority, RecoveryEvidence, assess,
    credential_problem, demo, fixture_target,
)


class RecoveryAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(prefix="test-ch42-")
        self.addCleanup(self.directory.cleanup)
        self.target = fixture_target(Path(self.directory.name) / "target.db")

    def check_gate(self, expected, **changes):
        result = assess(self.target, **changes)
        self.assertEqual(result["gate"], expected)
        self.assertEqual(result["write_admission"], "CLOSED")
        self.assertEqual(result["assessment_mutations"], 0)
        self.assertFalse(result["production_ready"])
        self.assertEqual(self.target.effect_count(), 1)
        return result

    def test_ready_means_canary_only(self):
        result = self.check_gate("READY_FOR_CANARY")
        self.assertEqual(result["historical_effect"], "APPLIED")
        self.assertEqual(result["operation_next"], "VERIFY_EXISTING_EFFECT")

    def test_repeated_assessment_does_not_replay(self):
        for _ in range(3):
            self.check_gate("READY_FOR_CANARY")

    def test_unfenced_writer(self):
        self.check_gate("HOLD_FENCING", evidence=RecoveryEvidence(old_writers_fenced=False))

    def test_unknown_fencing(self):
        self.check_gate("HOLD_FENCING", evidence=RecoveryEvidence(old_writers_fenced=None))

    def test_unverified_data(self):
        self.check_gate("HOLD_DATA", evidence=RecoveryEvidence(data_verified=False))

    def test_foreign_tenant_boundary_unchecked(self):
        self.check_gate("HOLD_TENANT_BOUNDARY", evidence=RecoveryEvidence(tenant_boundary_checked=False))

    def test_unknown_authority(self):
        self.check_gate("HOLD_CURRENT_AUTHORITY", authority=CurrentAuthority(available=False))

    def test_lookup_denied(self):
        result = self.check_gate("HOLD_LOOKUP_PERMISSION", authority=CurrentAuthority(lookup_allowed=False))
        self.assertEqual(result["historical_effect"], "NOT_CHECKED")

    def test_lookup_remains_separate_from_write_permission(self):
        result = self.check_gate("HOLD_CURRENT_PERMISSION", authority=CurrentAuthority(followup_allowed=False))
        self.assertEqual(result["historical_effect"], "APPLIED")
        self.assertEqual(result["operation_next"], "VERIFY_EXISTING_EFFECT")

    def test_revoked_restored_credential(self):
        result = self.check_gate("HOLD_CREDENTIAL_REVOKED", credential=Credential(record_id="cred-v1", generation=1))
        self.assertEqual(result["historical_effect"], "APPLIED")

    def test_generation_is_independent_of_record_id(self):
        self.check_gate("HOLD_CREDENTIAL_GENERATION", credential=Credential(record_id="older", generation=1))

    def test_fresh_credential_cannot_restore_permission(self):
        self.check_gate("HOLD_CURRENT_PERMISSION", authority=CurrentAuthority(followup_allowed=False))

    def test_expired(self):
        self.check_gate("HOLD_CREDENTIAL_EXPIRED", now=901)

    def test_exact_expiry_is_invalid(self):
        self.check_gate("HOLD_CREDENTIAL_EXPIRED", now=900)

    def test_before_expiry(self):
        self.check_gate("READY_FOR_CANARY", now=899)

    def test_wrong_tenant(self):
        self.check_gate("HOLD_CREDENTIAL_SCOPE", credential=Credential(tenant="other"))

    def test_wrong_audience(self):
        self.check_gate("HOLD_CREDENTIAL_SCOPE", credential=Credential(audience="model-api"))

    def test_invalid_generation(self):
        self.check_gate("HOLD_CREDENTIAL_METADATA", credential=Credential(generation=0))

    def test_boolean_generation(self):
        self.check_gate("HOLD_CREDENTIAL_METADATA", credential=Credential(generation=True))

    def test_invalid_expiry(self):
        self.check_gate("HOLD_CREDENTIAL_EXPIRED", credential=Credential(expires_at="900"))

    def test_wrong_run(self):
        self.check_gate("HOLD_CHECKPOINT_BINDING", checkpoint=Checkpoint(run="other"))

    def test_wrong_operation(self):
        self.check_gate("HOLD_CHECKPOINT_BINDING", checkpoint=Checkpoint(operation="op-18"))

    def test_new_key_does_not_bypass_history(self):
        self.check_gate("HOLD_CHECKPOINT_BINDING", checkpoint=Checkpoint(key="new-key"))

    def test_changed_arguments(self):
        self.check_gate("HOLD_CHECKPOINT_BINDING", checkpoint=Checkpoint(action_digest=Action(local_pref=200).digest()))

    def test_other_checkpoint_state(self):
        self.check_gate("HOLD_CHECKPOINT_BINDING", checkpoint=Checkpoint(local_state="NOT_SENT"))

    def test_missing_receipt_is_unknown_even_in_empty_fixture(self):
        empty = Target(Path(self.directory.name) / "empty.db")
        result = assess(empty)
        self.assertEqual(result["gate"], "HOLD_OUTCOME_UNKNOWN")
        self.assertEqual(result["historical_effect"], "UNKNOWN")
        self.assertEqual(empty.effect_count(), 0)

    def test_lookup_timeout(self):
        class Unavailable:
            def lookup(self, key, action):
                raise TimeoutError("fixture")
        self.assertEqual(assess(Unavailable())["gate"], "HOLD_LOOKUP_UNAVAILABLE")

    def test_lookup_connection_failure(self):
        class Unavailable:
            def lookup(self, key, action):
                raise ConnectionError("fixture")
        self.assertEqual(assess(Unavailable())["gate"], "HOLD_LOOKUP_UNAVAILABLE")

    def test_provider_conflict(self):
        class Conflicting:
            def lookup(self, key, action):
                raise Conflict("fixture")
        self.assertEqual(assess(Conflicting())["gate"], "HOLD_RECEIPT_CONFLICT")

    def test_receipt_binding_fields(self):
        receipt = self.target.lookup(KEY, Action())
        for field, value in [("key", "wrong"), ("digest", "0" * 64),
                             ("applied_revision", "config-19"), ("local_pref", 200)]:
            with self.subTest(field=field):
                class WrongReceipt:
                    def lookup(self, key, action):
                        return {**receipt, field: value}
                self.assertEqual(assess(WrongReceipt())["gate"], "HOLD_RECEIPT_CONFLICT")

    def test_checks_do_not_call_apply(self):
        receipt = self.target.lookup(KEY, Action())
        class ReadOnly:
            def lookup(self, key, action):
                return receipt
            def apply(self, *args, **kwargs):
                raise AssertionError("assessment must not write")
        self.assertEqual(assess(ReadOnly())["gate"], "READY_FOR_CANARY")

    def test_no_lookup_before_fencing(self):
        class NoLookup:
            def lookup(self, *args):
                raise AssertionError("premature lookup")
        result = assess(NoLookup(), evidence=RecoveryEvidence(old_writers_fenced=False))
        self.assertEqual(result["gate"], "HOLD_FENCING")

    def test_invalid_clock(self):
        for now in [-1, True, 1.5, "600"]:
            with self.subTest(now=now), self.assertRaises(ValueError):
                assess(self.target, now=now)

    def test_credential_helper_clock(self):
        with self.assertRaises(ValueError):
            credential_problem(Credential(), CurrentAuthority(), -1)

    def test_demo_decisions(self):
        output = demo()
        self.assertEqual({k: v["gate"] for k, v in output["cases"].items()}, {
            "restored_credential": "HOLD_CREDENTIAL_REVOKED",
            "fresh_credential_old_permission": "HOLD_CURRENT_PERMISSION",
            "receipt_unavailable": "HOLD_OUTCOME_UNKNOWN",
            "current_checks_pass": "READY_FOR_CANARY",
            "old_writer_unfenced": "HOLD_FENCING",
        })

    def test_demo_effect_count(self):
        output = demo()
        self.assertEqual(output["fixture_effects_before_assessment"], 1)
        self.assertEqual(output["fixture_effects_after_assessment"], 1)
        self.assertEqual(output["external_calls"], 0)
        self.assertFalse(output["live_restore_executed"])


if __name__ == "__main__":
    unittest.main()
