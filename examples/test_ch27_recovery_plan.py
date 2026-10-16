"""Deterministic recovery-planning fixtures; no infrastructure is contacted."""
from dataclasses import replace
import unittest
from ch27_recovery_plan import Observation, assess, demo, point, reconcile, recovery_budget, select_point, sha, worked_case


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.p = point()

    def reject(self, reason, **change):
        chosen, rejected = select_point([replace(self.p, **change)], 130)
        self.assertIsNone(chosen)
        self.assertEqual(list(rejected.values()), [reason])

    def effect(self, **kwargs):
        options = dict(old_writer_fenced=True, journal_available=True, idempotency_valid=True)
        options.update(kwargs)
        return reconcile(Observation(), **options)

    def test_valid_point(self):
        self.assertEqual(select_point([self.p], 130), (self.p, {}))

    def test_empty_catalog(self):
        self.assertEqual(select_point([], 130), (None, {}))

    def test_select_latest_independent_of_order(self):
        for points in [[point("old", 110), self.p], [self.p, point("old", 110)]]:
            self.assertEqual(select_point(points, 130)[0], self.p)

    def test_incomplete(self):
        self.reject("incomplete", completed=False)

    def test_future(self):
        self.reject("future_cut", cut=131)

    def test_task_mapping(self):
        self.reject("mapping_mismatch", task_actor="other")

    def test_database_mapping(self):
        self.reject("mapping_mismatch", db_actor="other")

    def test_both_mappings_wrong(self):
        self.reject("mapping_mismatch", task_actor="other", db_actor="other")

    def test_reference_mismatch(self):
        self.reject("reference_mismatch", db_snapshot="other")

    def test_empty_reference(self):
        self.reject("reference_mismatch", db_snapshot="", blob_name="")

    def test_missing_blob(self):
        self.reject("missing_blob", blob=None)

    def test_corrupt_bytes(self):
        self.reject("digest_mismatch", blob=b"changed")

    def test_wrong_digest(self):
        self.reject("digest_mismatch", digest="0" * 64)

    def test_key_missing(self):
        self.reject("key_unavailable", key_available=False)

    def test_unknown_format(self):
        self.reject("compatibility_unknown", snapshot_format="unknown")

    def test_reader_change_requires_declared_contract(self):
        self.assertEqual(select_point([self.p], 130, reader="demo-v2")[1], {"p120": "compatibility_unknown"})

    def test_declared_compatibility_is_only_input(self):
        self.assertEqual(select_point([self.p], 130, reader="demo-v2",
                                     compatible={("demo-v1", "demo-v2")})[0], self.p)

    def test_fallback_records_rejection(self):
        newer = replace(point("new", 125), blob=None)
        self.assertEqual(select_point([self.p, newer], 130), (self.p, {"new": "missing_blob"}))

    def test_invalid_incident(self):
        for value in [-1, True, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_point([self.p], value)

    def test_invalid_cut(self):
        for value in [-1, True, 1.5]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                select_point([replace(self.p, cut=value)], 130)

    def test_empty_reader(self):
        with self.assertRaises(ValueError):select_point([self.p], 130, reader="")

    def test_duplicate_point_name(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_catalog"):
            select_point([self.p, replace(self.p, cut=110)], 130)

    def test_empty_point_name(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_catalog"):
            select_point([replace(self.p, name="")], 130)

    def test_duplicate_cut(self):
        with self.assertRaisesRegex(ValueError, "ambiguous_cut"):
            select_point([self.p, replace(self.p, name="other")], 130)

    def test_effect_applied(self):
        self.assertEqual(self.effect(), "already_applied")

    def test_fencing_required(self):
        self.assertEqual(self.effect(old_writer_fenced=False), "hold_fencing")

    def test_surviving_journal_required(self):
        self.assertEqual(self.effect(journal_available=False), "hold_journal")

    def test_operation_mismatch(self):
        self.assertEqual(reconcile(Observation(operation="other"), old_writer_fenced=True,
                                  journal_available=True, idempotency_valid=True), "hold_conflict")

    def test_payload_mismatch(self):
        self.assertEqual(reconcile(Observation(payload=sha(b"C2")), old_writer_fenced=True,
                                  journal_available=True, idempotency_valid=True), "hold_conflict")

    def test_receipt_required(self):
        self.assertEqual(reconcile(Observation(receipt=None), old_writer_fenced=True,
                                  journal_available=True, idempotency_valid=True), "hold_unknown")

    def test_unknown_is_not_absence(self):
        self.assertEqual(reconcile(Observation(state="unknown"), old_writer_fenced=True,
                                  journal_available=True, idempotency_valid=True), "hold_unknown")

    def test_missing_search_result_not_definitive(self):
        self.assertEqual(reconcile(Observation(state="not_applied", receipt=None), old_writer_fenced=True,
                                  journal_available=True, idempotency_valid=True), "hold_unknown")

    def test_retry_requires_live_idempotency_contract(self):
        obs = Observation(state="not_applied", receipt=None, definitive_absence=True)
        for valid, expected in [(True, "retry_same_operation"), (False, "hold_unknown")]:
            with self.subTest(valid=valid):
                self.assertEqual(reconcile(obs, old_writer_fenced=True, journal_available=True,
                                           idempotency_valid=valid), expected)

    def test_invalid_target_state(self):
        with self.assertRaises(ValueError):
            reconcile(Observation(state="404"), old_writer_fenced=True, journal_available=True,
                      idempotency_valid=True)

    def test_budget_numbers(self):
        self.assertEqual(recovery_budget(130, 120, 170, 15, 45),
                         {"exposure_s": 10, "recovery_elapsed_s": 40, "rpo_met": True, "rto_met": True})

    def test_budget_boundaries_inclusive(self):
        self.assertTrue(all(recovery_budget(130, 115, 175, 15, 45)[k] for k in ["rpo_met", "rto_met"]))

    def test_rpo_missed(self):
        self.assertFalse(recovery_budget(130, 110, 170, 15, 45)["rpo_met"])

    def test_rto_missed(self):
        self.assertFalse(recovery_budget(130, 120, 180, 15, 45)["rto_met"])

    def test_unverified_has_no_rto_measurement(self):
        result = recovery_budget(130, 120, None, 15, 45)
        self.assertIsNone(result["recovery_elapsed_s"])
        self.assertIsNone(result["rto_met"])

    def test_invalid_budget_times(self):
        for args in [(130, 131, 170, 15, 45), (-1, 0, 170, 15, 45),
                     (130, 120, 129, 15, 45), (130, 120, True, 15, 45),
                     (130, 120, 170, -1, 45), (130, 120, 170, 15, True)]:
            with self.subTest(args=args), self.assertRaises(ValueError):recovery_budget(*args)

    def test_model_pass_never_certifies_production(self):
        result = worked_case([self.p])
        self.assertEqual(result["gate"], "model_pass")
        self.assertFalse(result["production_ready"])
        self.assertFalse(result["live_restore_executed"])

    def test_unresolved_effect_discards_claimed_verification_time(self):
        result = worked_case([self.p], observation=Observation(state="unknown"))
        self.assertIsNone(result["recovery_elapsed_s"])
        self.assertEqual(result["gate"], "hold_unknown")

    def test_retry_advice_is_not_completed_effect(self):
        result = worked_case([self.p], observation=Observation(state="not_applied", definitive_absence=True))
        self.assertEqual(result["gate"], "hold_retry_needed")
        self.assertIsNone(result["recovery_elapsed_s"])

    def test_service_verification_missing(self):
        self.assertEqual(worked_case([self.p], verified_at=None)["gate"], "hold_verification")

    def test_default_assessment_holds_without_fencing_evidence(self):
        self.assertEqual(assess([self.p])["gate"], "hold_fencing")

    def test_no_point_does_not_reconcile(self):
        self.assertEqual(assess([])["effect"], "not_checked")

    def test_demo_ten_scenarios(self):
        data = demo()
        self.assertEqual(len(data), 10)
        self.assertEqual(data["old_fallback"]["gate"], "hold_rpo")
        self.assertEqual(data["too_slow"]["gate"], "hold_rto")
        self.assertTrue(all(not r["production_ready"] and not r["live_restore_executed"]
                            for r in data.values()))


if __name__ == "__main__":
    unittest.main()
