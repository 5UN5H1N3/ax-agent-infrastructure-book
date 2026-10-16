"""Tests of local fixtures and transformations, not a live telemetry pipeline."""

from dataclasses import replace
import unittest

from ch25_observability_contract import (
    Collector, Goal, RESULTS, STAGES, context, demo, event, fixtures,
    investigate, metric_labels, outcome_sli,
)


class ObservabilityTests(unittest.TestCase):
    def setUp(self):
        self.c = Collector(2)
        self.ctx = context("happy")
        self.e = event(self.ctx, "event-1", 1, 1, "job.accepted")
        self.goals = fixtures()
        self.ledger = {g.job: g for g in self.goals}

    def test_sli_acceptance_population_keeps_stuck_jobs(self):
        self.assertEqual(outcome_sli(self.goals, 0, 50, 60), {
            "eligible": 6, "due": 5, "not_due": 1, "good": 2, "bad": 3, "ratio": .4})

    def test_not_yet_due_not_bad(self):
        x = outcome_sli([Goal("j", 0, 100, "running")], 0, 10, 20)
        self.assertEqual((x["bad"], x["not_due"], x["ratio"]), (0, 1, None))

    def test_empty_population_not_100_percent(self):
        self.assertIsNone(outcome_sli([], 0, 10, 20)["ratio"])

    def test_boundary_deadline_matures(self):
        x = outcome_sli([Goal("j", 0, 20, "running")], 0, 10, 20)
        self.assertEqual((x["due"], x["bad"]), (1, 1))

    def test_verification_at_deadline_good(self):
        x = outcome_sli([Goal("j", 0, 20, "done", 20)], 0, 10, 20)
        self.assertEqual(x["good"], 1)

    def test_late_verification_stays_bad_for_deadline_sli(self):
        x = outcome_sli([Goal("j", 0, 20, "done", 21)], 0, 10, 30)
        self.assertEqual((x["good"], x["bad"]), (0, 1))

    def test_window_end_exclusive(self):
        x = outcome_sli([Goal("j", 10, 20, "done", 20)], 0, 10, 30)
        self.assertEqual(x["eligible"], 0)

    def test_window_start_inclusive(self):
        x = outcome_sli([Goal("j", 10, 20, "done", 20)], 10, 11, 30)
        self.assertEqual(x["good"], 1)

    def test_eligibility_is_explicit_fixture_not_event_sampling(self):
        x = outcome_sli([replace(self.goals[0], eligible=False)], 0, 50, 60)
        self.assertEqual(x["eligible"], 0)

    def test_duplicate_goal_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate goal"):
            outcome_sli([self.goals[0]] * 2, 0, 50, 60)

    def test_invalid_window_rejected(self):
        for a,b,n in [(10,10,20), (10,0,20), (0,30,20)]:
            with self.subTest(window=(a,b,n)), self.assertRaises(ValueError):
                outcome_sli([], a, b, n)

    def test_impossible_goal_times_rejected(self):
        for g in [Goal("j", 10, 9, "running"), Goal("j", 10, 20, "done", 9),
                  Goal("j", 0, 20, "done", 61)]:
            with self.subTest(goal=g), self.assertRaises(ValueError):
                outcome_sli([g], 0, 50, 60)

    def test_queue_drop_count_and_bound(self):
        self.assertTrue(self.c.offer(self.e))
        self.assertTrue(self.c.offer(replace(self.e, id="event-2")))
        self.assertFalse(self.c.offer(replace(self.e, id="event-3")))
        self.assertEqual((len(self.c.queue),self.c.attempted,self.c.dropped), (2,3,1))

    def test_flush_releases_queue_not_history(self):
        self.c.offer(self.e);self.c.flush()
        self.assertEqual(len(self.c.queue), 0)
        self.assertEqual(len(self.c.records), 1)

    def test_identical_duplicates_deduplicated(self):
        self.c.offer(self.e);self.c.offer(self.e);self.c.flush()
        self.assertEqual((len(self.c.records),self.c.duplicates), (1,1))

    def test_conflicting_id_not_silently_overwritten(self):
        self.c.offer(self.e);self.c.flush()
        self.c.offer(replace(self.e, name="tool.sent"))
        with self.assertRaisesRegex(ValueError, "conflicting event identity"):
            self.c.flush()
        self.assertEqual(next(iter(self.c.records.values())), self.e)

    def test_id_scoped_to_producer(self):
        other = replace(self.e, context=replace(self.ctx, producer="verifier"))
        self.c.offer(self.e);self.c.offer(other);self.c.flush()
        self.assertEqual(len(self.c.records), 2)

    def test_sequence_not_arrival_or_wall_clock_orders_stream(self):
        recent = replace(self.e, id="recent", sequence=4, occurred_at=2, name="tool.verified")
        old = replace(self.e, id="old", sequence=1, occurred_at=100, name="tool.sent")
        self.c.offer(recent);self.c.offer(old);self.c.flush()
        self.assertEqual(next(iter(self.c.latest_streams("happy").values())), recent)

    def test_activation_keeps_separate_stream(self):
        other = replace(self.e, id="event-2", context=context("happy", "activation-2"))
        self.c.offer(self.e);self.c.offer(other);self.c.flush()
        self.assertEqual(len(self.c.latest_streams("happy")), 2)

    def test_other_jobs_not_mixed(self):
        self.c.offer(self.e);self.c.flush()
        self.assertEqual(self.c.latest_streams("foreign"), {})

    def test_missing_ledger_is_unknown_even_with_verified_event(self):
        self.c.offer(replace(self.e, name="tool.verified"));self.c.flush()
        self.assertEqual(investigate("happy", None, self.c)["state"], "unknown")

    def test_unknown_job_not_found_not_failed(self):
        self.assertEqual(investigate("missing", self.ledger, self.c)["state"], "not_found")

    def test_ledger_verified_diagnostic_absence_is_gap(self):
        x = investigate("happy", self.ledger, self.c)
        self.assertEqual(x, {"state":"verified","next":"inspect_telemetry","gap":True})

    def test_diagnostic_verified_does_not_complete_running_ledger(self):
        self.c.offer(event(context("young"), "v", 1, 50, "tool.verified"));self.c.flush()
        self.assertEqual(investigate("young", self.ledger, self.c)["state"], "running")

    def test_approval_state_leads_to_approval_check(self):
        self.assertEqual(investigate("approval_wait", self.ledger, self.c)["next"],
                         "check_approval_deadline")

    def test_uncertain_state_leads_to_reconciliation_not_retry(self):
        self.assertEqual(investigate("reply_lost", self.ledger, self.c)["next"],
                         "reconcile_target")

    def test_bounded_attributes_only(self):
        e = event(self.ctx, "e", 1, 0, "tool.sent", {
            "stage":"publish", "result":"timeout", "duration_ms":100,
            "Authorization":"Bearer DEMO-ONLY", "prompt":"DEMO-ONLY",
            "ate.actor.uid":"forged", "job":"foreign", "trace_id":"forged"})
        self.assertEqual(dict(e.attributes), {"stage":"publish","result":"timeout","duration_ms":100})
        self.assertEqual(e.context, self.ctx)

    def test_arbitrary_strings_not_kept_in_enum_fields(self):
        e = event(self.ctx, "e", 1, 0, "tool.sent", {"stage":"DEMO-SECRET","result":"url"})
        self.assertEqual(e.attributes, ())

    def test_duration_rejects_bool_negative_and_unbounded(self):
        for v in [True, -1, 3600001, "50", float("nan")]:
            with self.subTest(value=v):
                self.assertEqual(event(self.ctx, "e", 1, 0, "tool.sent",
                                       {"duration_ms":v}).attributes, ())

    def test_unknown_event_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown event"):
            event(self.ctx, "e", 1, 0, "arbitrary.free.text")

    def test_trace_ids_validated(self):
        for value in ["0"*32, "f"*31, "F"*32, "z"*32]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                event(replace(self.ctx, trace_id=value), "e", 1, 0, "tool.sent")

    def test_span_ids_validated(self):
        with self.assertRaises(ValueError):
            event(replace(self.ctx, span_id="0"*16), "e", 1, 0, "tool.sent")

    def test_sequence_and_timestamp_validated(self):
        for seq,t in [(True,1),(-1,1),(1,True),(1,"time")]:
            with self.subTest(seq=seq,t=t), self.assertRaises(ValueError):
                event(self.ctx, "e", seq, t, "tool.sent")

    def test_invalid_capacity(self):
        for n in [0,-1,True]:
            with self.subTest(n=n), self.assertRaises(ValueError):
                Collector(n)

    def test_metric_values_and_keys_bounded(self):
        keys = {metric_labels(s,r) for s in STAGES for r in RESULTS}
        self.assertEqual(len(keys), 12)
        with self.assertRaises(ValueError):
            metric_labels("publish", "ok", job="happy")
        with self.assertRaises(ValueError):
            metric_labels("happy", "ok")

    def test_new_activation_new_trace_same_goal(self):
        a,b = context("happy"),context("happy", "activation-2")
        self.assertEqual(a.job,b.job)
        self.assertNotEqual(a.trace_id,b.trace_id)

    def test_different_goals_not_same_trace(self):
        self.assertNotEqual(context("happy").trace_id,context("young").trace_id)

    def test_demo_reproduces_denominator_bias_and_queue_loss(self):
        x=demo()
        self.assertEqual(x["sli"]["ratio"], .4)
        self.assertAlmostEqual(x["naive_completed_only_ratio"], 2/3)
        self.assertEqual(x["pipeline"], {"attempted":11,"dropped":1,"duplicates":1,"stored":9})
        self.assertEqual(x["latest_happy"], "tool.verified")


if __name__ == "__main__":
    unittest.main()
