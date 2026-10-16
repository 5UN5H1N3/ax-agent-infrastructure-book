"""Synthetic fixtures only: these tests never start a laboratory."""
from dataclasses import replace
import unittest

from ch26_lab_evidence import Plan, Report, demo, failure_domains, passed, required_gates, review


class LabEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.plan = Plan()

    def check_review(self, result, gate="guest", records=None, **kwargs):
        actual = review(self.plan, [] if records is None else records, "run-1", 40, **kwargs)
        self.assertEqual(actual, {"gate": gate, "result": result, "compatibility_proven": False})

    def invalid_plan(self, message, **kwargs):
        with self.assertRaisesRegex(ValueError, "^" + message + "$"):
            replace(self.plan, **kwargs).digest()

    def test_digest_stable(self):
        self.assertEqual(self.plan.digest(), Plan().digest())

    def test_network_order_irrelevant(self):
        self.assertEqual(self.plan.digest(), replace(self.plan, networks=self.plan.networks[::-1]).digest())

    def test_all_identity_fields_change_digest(self):
        for field, value in {"lab_id": "other", "kube_context": "other", "runtime": "microvm",
                             "ax": "a" * 40, "substrate": "b" * 40,
                             "runner": "other.invalid/image@sha256:" + "c" * 64}.items():
            with self.subTest(field=field):
                self.assertNotEqual(self.plan.digest(), replace(self.plan, **{field: value}).digest())

    def test_missing_lab(self):
        self.invalid_plan("missing_target", lab_id="")

    def test_missing_context(self):
        self.invalid_plan("missing_target", kube_context="")

    def test_unknown_runtime(self):
        self.invalid_plan("unknown_runtime", runtime="auto")

    def test_short_commit(self):
        self.invalid_plan("unpinned_source", ax="ac233282")

    def test_mutable_branch(self):
        self.invalid_plan("unpinned_source", substrate="main")

    def test_image_tag(self):
        self.invalid_plan("unpinned_image", runner="runner:latest")

    def test_missing_network(self):
        self.invalid_plan("invalid_network_set", networks=self.plan.networks[:-1])

    def test_duplicate_network_name(self):
        self.invalid_plan("invalid_network_set", networks=self.plan.networks[:-1] + (self.plan.networks[0],))

    def test_host_bits(self):
        self.invalid_plan("invalid_cidr", networks=(("management", "192.0.2.1/24"),) + self.plan.networks[1:])

    def test_malformed_network(self):
        self.invalid_plan("invalid_cidr", networks=(("management", "oops"),) + self.plan.networks[1:])

    def test_ipv6_out_of_scope(self):
        self.invalid_plan("ipv4_model_only", networks=(("management", "2001:db8::/64"),) + self.plan.networks[1:])

    def test_overlap_contained_subnet(self):
        self.invalid_plan("network_overlap", networks=self.plan.networks[:-1] + (("services", "10.240.0.0/24"),))

    def test_every_network_pair_checked(self):
        for index in range(1, 4):
            networks = list(self.plan.networks)
            networks[index] = (networks[index][0], networks[0][1])
            with self.subTest(index=index):
                self.invalid_plan("network_overlap", networks=tuple(networks))

    def test_missing_first_gate(self):
        self.check_review("missing")

    def test_missing_later_gate(self):
        self.check_review("missing", "substrate", passed(self.plan, 2))

    def test_not_run_is_not_pass(self):
        records = passed(self.plan)
        records[0] = replace(records[0], status="not_run")
        self.check_review("missing", records=records)

    def test_failure_blocks_later_passes(self):
        records = passed(self.plan)
        records[0] = replace(records[0], status="failed")
        self.check_review("failed", records=records)

    def test_wrong_run(self):
        self.check_review("wrong_run", records=passed(self.plan, run_id="other"))

    def test_wrong_plan(self):
        self.check_review("wrong_plan", records=passed(replace(self.plan, lab_id="other")))

    def test_expired(self):
        self.check_review("stale_or_future", records=passed(self.plan, observed_at=9))

    def test_future(self):
        self.check_review("stale_or_future", records=passed(self.plan, observed_at=41))

    def test_exact_age_boundary(self):
        self.check_review("records_complete", "-", passed(self.plan))

    def test_zero_age(self):
        self.check_review("records_complete", "-", passed(self.plan, observed_at=40), max_age=0)

    def test_review_scope_validation(self):
        for run, now, age in [("", 40, 30), ("run-1", -1, 30), ("run-1", True, 30),
                              ("run-1", 40, -1), ("run-1", 40, 1.5)]:
            with self.subTest(run=run, now=now, age=age), self.assertRaises(ValueError):
                review(self.plan, [], run, now, age)

    def test_invalid_reports(self):
        for change in [{"gate": "unknown"}, {"status": "green"}, {"observed_at": -1}, {"observed_at": True}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "invalid_report"):
                review(self.plan, [replace(passed(self.plan)[0], **change)], "run-1", 40)

    def test_duplicate_report(self):
        record = passed(self.plan)[0]
        with self.assertRaisesRegex(ValueError, "duplicate_gate"):
            review(self.plan, [record, record], "run-1", 40)

    def test_report_order_irrelevant(self):
        self.check_review("records_complete", "-", passed(self.plan)[::-1])

    def test_microvm_needs_kvm(self):
        plan = replace(self.plan, runtime="microvm")
        self.assertEqual(required_gates(plan)[1], "kvm")
        self.assertEqual(review(plan, passed(plan, 1), "run-1", 40)["gate"], "kvm")

    def test_microvm_complete_still_not_proof(self):
        plan = replace(self.plan, runtime="microvm")
        self.assertEqual(review(plan, passed(plan), "run-1", 40),
                         {"gate": "-", "result": "records_complete", "compatibility_proven": False})

    def test_container_nodes_share_guest(self):
        self.assertEqual(failure_domains([("a", "v", "h"), ("b", "v", "h")]),
                         {"nodes": 2, "guest_domains": 1, "host_domains": 1, "ha_proven": False})

    def test_vms_share_host(self):
        result = failure_domains([("a", "v1", "h"), ("b", "v2", "h")])
        self.assertEqual((result["guest_domains"], result["host_domains"]), (2, 1))

    def test_separate_hosts_not_ha_proof(self):
        result = failure_domains([("a", "v1", "h1"), ("b", "v2", "h2")])
        self.assertEqual(result["host_domains"], 2)
        self.assertFalse(result["ha_proven"])

    def test_invalid_placements(self):
        for nodes in [[], [("", "v", "h")], [("a", "v", "")], [("a", "", "h")],
                      [("a", "v", "h1"), ("b", "v", "h2")],
                      [("a", "v", "h"), ("a", "v2", "h")]]:
            with self.subTest(nodes=nodes), self.assertRaises(ValueError):
                failure_domains(nodes)

    def test_demo_ten_cases_never_claim_compatibility(self):
        result = demo()
        self.assertEqual(len(result["reviews"]), 10)
        self.assertTrue(all(not r["compatibility_proven"] for r in result["reviews"].values()))
        self.assertFalse(result["live_lab_executed"])

    def test_demo_three_topologies(self):
        result = demo()["placement"]
        self.assertEqual([(v["nodes"], v["guest_domains"], v["host_domains"]) for v in result.values()],
                         [(3, 1, 1), (3, 3, 1), (3, 3, 3)])
        self.assertTrue(all(not v["ha_proven"] for v in result.values()))


if __name__ == "__main__":
    unittest.main()
