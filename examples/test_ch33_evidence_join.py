"""Author-side checks for the deliberately small, offline evidence join."""
from dataclasses import replace
import unittest

import ch33_evidence_join as model


class EvidenceJoinTests(unittest.TestCase):
    def setUp(self):
        self.subject, self.store, self.reports = model.code_fixture()

    def decide(self, subject=None, reports=None, policy=None, now=120, not_before=0):
        return model.join(self.subject if subject is None else subject,
                          model.CODE_POLICY if policy is None else policy,
                          self.reports if reports is None else reports,
                          self.store, now, not_before)

    def changed_report(self, **changes):
        first = self.reports[0]
        data = {**self.store.read(first.artifact_sha256), **changes}
        return [replace(first, artifact_sha256=self.store.put(data)), self.reports[1]]

    def test_valid_pair_is_ready(self):
        self.assertEqual(self.decide(), "ready_for_next_step")

    def test_order_is_irrelevant_for_valid_pair(self):
        self.assertEqual(self.decide(reports=list(reversed(self.reports))), "ready_for_next_step")

    def test_no_reports_waits(self):
        self.assertEqual(self.decide(reports=[]), "waiting:missing_evidence")

    def test_missing_review_waits(self):
        self.assertEqual(self.decide(reports=self.reports[:1]), "waiting:missing_evidence")

    def test_missing_tests_waits(self):
        self.assertEqual(self.decide(reports=self.reports[1:]), "waiting:missing_evidence")

    def test_duplicate_delivery_is_idempotent(self):
        self.assertEqual(self.decide(reports=self.reports * 2), "ready_for_next_step")

    def test_ambiguous_attempts_are_not_silently_replaced(self):
        duplicate = replace(self.reports[0], captured_at=101)
        self.assertEqual(self.decide(reports=self.reports + [duplicate]), "rejected:ambiguous_attempts")

    def test_each_subject_dimension_is_bound(self):
        for field, value in [("run", "another-run"), ("target", "another-repo"),
                             ("base_revision", "base-2"), ("candidate_revision", "candidate-2"),
                             ("content_sha256", "d" * 64), ("stage", "post_merge")]:
            with self.subTest(field=field):
                self.assertEqual(self.decide(subject=replace(self.subject, **{field: value})),
                                 "rejected:subject_mismatch")

    def test_new_policy_invalidates_old_subject(self):
        self.assertEqual(self.decide(policy=replace(model.CODE_POLICY, name="new-rule")),
                         "rejected:policy_changed")

    def test_subject_with_new_policy_does_not_reuse_old_reports(self):
        policy = replace(model.CODE_POLICY, max_age=3599)
        changed = replace(self.subject, policy_sha256=policy.fingerprint())
        self.assertEqual(self.decide(subject=changed, policy=policy), "rejected:subject_mismatch")

    def test_producer_cannot_be_substituted(self):
        changed = [replace(self.reports[0], producer="implementer"), self.reports[1]]
        self.assertEqual(self.decide(reports=changed), "rejected:producer_mismatch")

    def test_self_declared_producer_is_not_a_transport_identity(self):
        first = self.store.read(self.reports[0].artifact_sha256)
        first["producer"] = "trusted-ci"
        changed = [replace(self.reports[0], artifact_sha256=self.store.put(first)), self.reports[1]]
        self.assertEqual(self.decide(reports=changed), "rejected:report_schema")

    def test_modified_bytes_are_detected(self):
        self.store.blobs[self.reports[0].artifact_sha256] = b"tampered"
        self.assertEqual(self.decide(), "rejected:artifact_missing_or_changed")

    def test_missing_blob_is_rejected(self):
        del self.store.blobs[self.reports[0].artifact_sha256]
        self.assertEqual(self.decide(), "rejected:artifact_missing_or_changed")

    def test_unexpected_kind_is_rejected(self):
        self.assertEqual(self.decide(reports=self.changed_report(kind="coordinator_summary")),
                         "rejected:unexpected_kind")

    def test_wrong_subject_is_rejected(self):
        self.assertEqual(self.decide(reports=self.changed_report(subject="old-run")),
                         "rejected:subject_mismatch")

    def test_missing_check_is_rejected(self):
        checks = dict.fromkeys(model.TEST_NAMES[:-1], True)
        self.assertEqual(self.decide(reports=self.changed_report(checks=checks)), "rejected:checks_schema")

    def test_unexpected_check_is_rejected(self):
        checks = {**dict.fromkeys(model.TEST_NAMES, True), "extra": True}
        self.assertEqual(self.decide(reports=self.changed_report(checks=checks)), "rejected:checks_schema")

    def test_string_pass_is_not_boolean_true(self):
        checks = dict.fromkeys(model.TEST_NAMES, True)
        checks["test_tail"] = "true"
        self.assertEqual(self.decide(reports=self.changed_report(checks=checks)), "rejected:checks_schema")

    def test_false_check_overrides_pass_label(self):
        checks = dict.fromkeys(model.TEST_NAMES, True)
        checks["test_tail"] = False
        self.assertEqual(self.decide(reports=self.changed_report(checks=checks)), "rejected:checks_failed")

    def test_fail_label_is_not_accepted(self):
        self.assertEqual(self.decide(reports=self.changed_report(verdict="fail")), "rejected:checks_failed")

    def test_inconclusive_is_not_success(self):
        self.assertEqual(self.decide(reports=self.changed_report(verdict="inconclusive")),
                         "manual_review:inconclusive")

    def test_unknown_verdict_is_rejected(self):
        self.assertEqual(self.decide(reports=self.changed_report(verdict="probably")), "rejected:checks_schema")

    def test_future_report_is_rejected(self):
        self.assertEqual(self.decide(now=99), "rejected:outside_window")

    def test_report_before_phase_is_rejected(self):
        self.assertEqual(self.decide(not_before=101), "rejected:outside_window")

    def test_max_age_boundary_is_inclusive(self):
        self.assertEqual(self.decide(now=3700), "ready_for_next_step")

    def test_stale_report_is_rejected(self):
        self.assertEqual(self.decide(now=3701), "rejected:stale")

    def test_broken_fixture_really_fails_three_checks(self):
        _, checks = model.trusted_fixture_checks(False)
        self.assertEqual([key for key, value in checks.items() if not value],
                         ["test_coverage", "test_short", "test_tail"])

    def test_repaired_fixture_passes_ten_checks(self):
        _, checks = model.trusted_fixture_checks(True)
        self.assertEqual(len(checks), 10)
        self.assertTrue(all(checks.values()))

    def test_test_policy_includes_actual_protected_suite_digest(self):
        self.assertIn(model.digest(model.TEST_SOURCE.encode()), model.CODE_POLICY.name)

    def test_fixture_selector_is_not_source_input(self):
        for value in (1, "source", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                model.trusted_fixture_checks(value)

    def test_trusted_producer_can_lie_a_documented_limit(self):
        subject, _, _ = model.code_fixture(False)
        reports = [model.evidence(self.store, subject, rule, 100) for rule in model.CODE_POLICY.rules]
        self.assertEqual(self.decide(subject=subject, reports=reports), "ready_for_next_step")

    def test_net_checks_are_scoped_to_post_change_window(self):
        subject, store, reports = model.net_fixture()
        self.assertEqual(model.join(subject, model.NET_POLICY, reports, store, 130, 110), "ready_for_next_step")

    def test_net_stale_checks_do_not_pass(self):
        subject, store, reports = model.net_fixture()
        self.assertEqual(model.join(subject, model.NET_POLICY, reports, store, 151, 110), "rejected:stale")

    def test_net_other_vrf_is_rejected(self):
        subject, store, reports = model.net_fixture()
        other = replace(subject, target="lab-edge-1/VRF:OTHER")
        self.assertEqual(model.join(other, model.NET_POLICY, reports, store, 130, 110), "rejected:subject_mismatch")

    def test_receipt_metadata_is_validated(self):
        for field, value in [("producer", ""), ("artifact_sha256", "x"), ("captured_at", True), ("captured_at", -1)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.reports[0], **{field: value})

    def test_subject_metadata_is_validated(self):
        for field, value in [("run", ""), ("target", " padded "), ("content_sha256", "x"), ("policy_sha256", "A" * 64)]:
            with self.subTest(field=field), self.assertRaises(ValueError):
                replace(self.subject, **{field: value})

    def test_policy_is_validated(self):
        for options in ({"rules": ()}, {"rules": model.CODE_POLICY.rules * 2}, {"max_age": True}, {"max_age": -1}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                replace(model.CODE_POLICY, **options)

    def test_rule_is_validated(self):
        for checks in ((), ("a", "a"), ["a"], ("",)):
            with self.subTest(checks=checks), self.assertRaises(ValueError):
                model.Rule("tests", "ci", checks)

    def test_join_context_is_validated(self):
        for options in ({"now": True}, {"now": -1}, {"not_before": 121}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.decide(**options)

    def test_report_objects_are_required(self):
        with self.assertRaises(ValueError):
            self.decide(reports=[{}])

    def test_non_object_report_is_rejected(self):
        report = replace(self.reports[0], artifact_sha256=self.store.put([]))
        self.assertEqual(self.decide(reports=[report]), "rejected:report_schema")

    def test_bad_json_is_rejected_even_with_matching_digest(self):
        raw = b"{not json}"
        key = model.digest(raw)
        self.store.blobs[key] = raw
        with self.assertRaisesRegex(ValueError, "invalid_report_json"):
            self.store.read(key)

    def test_noncanonical_json_is_rejected(self):
        raw = b'{"same":1,"same":2}'
        key = model.digest(raw)
        self.store.blobs[key] = raw
        with self.assertRaisesRegex(ValueError, "noncanonical_report"):
            self.store.read(key)

    def test_oversized_report_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.put({"data": "x" * 20000})

    def test_invalid_artifact_key_is_rejected(self):
        with self.assertRaises(ValueError):
            self.store.read([])

    def test_demo_documents_fourteen_cases_without_effects(self):
        result = model.demo()
        self.assertEqual(len(result["cases"]), 14)
        self.assertFalse(result["external_effects_executed"])
        self.assertFalse(result["producer_authentication_implemented"])
        self.assertFalse(result["live_network_verified"])


if __name__ == "__main__":
    unittest.main()
