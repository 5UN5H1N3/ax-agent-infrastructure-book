from dataclasses import replace
import unittest

from ch28_capacity_model import Config, Trace, demo, phase_times, percentile, simulate, summarize


class CapacityModelTests(unittest.TestCase):
    def test_one_request(self):
        t = simulate([0])[0]
        self.assertEqual((t.assigned_ms, t.restore_started_ms, t.restored_ms,
                          t.ended_ms), (0, 0, 1000, 1500))

    def test_partition_of_sequential_path(self):
        for t in simulate(range(0, 10000, 500)):
            p = phase_times(t)
            self.assertEqual(sum(v for k, v in p.items() if k != 'e2e_ms'), p['e2e_ms'])

    def test_first_four_requests(self):
        self.assertEqual([t.ended_ms - t.sent_ms for t in simulate([0, 500, 1000, 1500])],
                         [1500, 2000, 2500, 3000])

    def test_single_worker_really_limits_service(self):
        self.assertEqual([t.ended_ms for t in simulate([0, 0, 0], Config(worker_slots=1))],
                         [1500, 3000, 4500])

    def test_extra_workers_do_not_fix_restore_rate(self):
        arrivals = list(range(0, 10000, 500))
        self.assertEqual([t.ended_ms for t in simulate(arrivals)],
                         [t.ended_ms for t in simulate(arrivals, Config(worker_slots=8))])

    def test_faster_restore_capacity(self):
        traces = simulate(range(0, 10000, 500), Config(restore_lanes=2))
        self.assertTrue(all(t.ended_ms - t.sent_ms == 1500 for t in traces))

    def test_worker_holds_slot_while_waiting_for_restore(self):
        traces = simulate([0, 0, 0], Config(worker_slots=2))
        self.assertEqual(traces[1].assigned_ms, 0)
        self.assertEqual(traces[1].restore_started_ms, 1000)
        self.assertEqual(traces[2].assigned_ms, 1500)

    def test_zero_restore_duration(self):
        self.assertEqual(simulate([0], Config(restore_ms=0))[0].ended_ms, 500)

    def test_equal_arrivals_preserve_input_order(self):
        self.assertEqual([t.request_id for t in simulate([0, 0, 0])], ['r1', 'r2', 'r3'])

    def test_admission_rejects_without_work(self):
        a, b = simulate([0, 1], Config(max_inflight=1))
        self.assertEqual(b.status, 'rejected')
        self.assertEqual(b.ended_ms, b.sent_ms)
        self.assertFalse(b.correct)
        self.assertEqual(a.ended_ms, 1500)

    def test_completion_releases_admission_before_same_time_arrival(self):
        self.assertTrue(all(t.status == 'completed' for t in
                            simulate([0, 1500], Config(max_inflight=1))))

    def test_rejection_does_not_delay_later_work(self):
        traces = simulate([0, 1, 1500], Config(max_inflight=1))
        self.assertEqual(traces[2].ended_ms, 3000)

    def test_baseline_summary(self):
        s = demo()['baseline']
        self.assertEqual((s['offered'], s['completed'], s['on_time_correct'],
                          s['completed_p95_ms'], s['goodput_rps']),
                         (20, 20, 3, 10500, .125))

    def test_all_requests_accounted_for_in_each_demo(self):
        for s in demo().values():
            self.assertEqual(s['offered'], s['completed'] + s['rejected'] + s['pending'])
            self.assertEqual(s['accepted'], s['completed'] + s['pending'])

    def test_goodput_excludes_invalid_output(self):
        good, bad = demo()['more_restore_capacity'], demo()['fast_but_invalid']
        self.assertEqual(bad['invalid_completed'], 1)
        self.assertEqual(good['completed_p95_ms'], bad['completed_p95_ms'])
        self.assertEqual(bad['on_time_correct'], good['on_time_correct'] - 1)

    def test_pending_does_not_disappear(self):
        s = demo()['cut_at_10s']
        self.assertEqual((s['completed'], s['pending']), (9, 11))
        self.assertEqual(s['pending_deadline_missed'] + s['pending_deadline_unresolved'], 11)

    def test_pending_future_outcome_not_counted(self):
        s = summarize(simulate([0], Config(execute_ms=10000)),
                      arrival_end_ms=1, end_ms=1000, deadline_ms=20000)
        self.assertEqual(s['pending_deadline_unresolved'], 1)
        self.assertEqual(s['on_time_correct'], 0)
        self.assertIsNone(s['completed_p95_ms'])

    def test_deadline_boundary_is_inclusive(self):
        s = summarize(simulate([0]), deadline_ms=1500)
        self.assertEqual(s['on_time_correct'], 1)

    def test_observation_boundary_is_inclusive(self):
        s = summarize(simulate([0]), arrival_end_ms=1, end_ms=1500)
        self.assertEqual((s['completed'], s['pending']), (1, 0))

    def test_rejected_requests_stay_in_success_denominator(self):
        s = summarize(simulate([0, 0], Config(max_inflight=1)))
        self.assertEqual(s['observed_success_fraction'], .5)
        self.assertEqual(s['completed_p95_ms'], 1500)

    def test_all_rejected_summary(self):
        t = Trace('r1', 0, 0, 0, 0, 0, False, 'rejected')
        s = summarize([t])
        self.assertEqual(s['on_time_correct'], 0)
        self.assertIsNone(s['completed_p95_ms'])

    def test_model_never_proves_production_capacity(self):
        for s in demo().values():
            self.assertFalse(s['production_capacity_proven'])
            self.assertFalse(s['live_benchmark_executed'])

    def test_percentile_raw_nearest_rank(self):
        self.assertEqual(percentile(range(1, 21), 95), 19)
        self.assertEqual(percentile([12], 99), 12)
        self.assertIsNone(percentile([], 95))

    def test_bad_configs(self):
        for name, values in {
            'worker_slots': [0, -1, True, 1.5],
            'restore_lanes': [0, -1, False, '2'],
            'restore_ms': [-1, True, float('inf')],
            'execute_ms': [0, -1, False, 2.5],
            'max_inflight': [0, -1, True, 2.5],
        }.items():
            for value in values:
                with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                    Config(**{name: value})

    def test_bad_arrivals(self):
        for value in ([], [-1], [True], [1.1], [1, 0], [float('nan')]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                simulate(value)

    def test_bad_validity(self):
        for value in ([], [1], [None], [True, False]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                simulate([0], validity=value)

    def test_bad_config_type(self):
        with self.assertRaises(ValueError):
            simulate([0], config={})

    def test_bad_trace_fields(self):
        base = simulate([0])[0]
        for changes in ({'request_id': ''}, {'sent_ms': -1}, {'assigned_ms': True},
                        {'restored_ms': 2000}, {'correct': 1}, {'status': 'error'},
                        {'status': 'rejected'}, {'ended_ms': 1.5}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(base, **changes)

    def test_bad_rejection_with_nonzero_duration(self):
        with self.assertRaises(ValueError):
            Trace('r', 0, 0, 0, 0, 1, False, 'rejected')

    def test_bad_summary_windows(self):
        for kwargs in ({'arrival_end_ms': 0}, {'end_ms': 0}, {'end_ms': 9999},
                       {'deadline_ms': True}, {'deadline_ms': -1}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                summarize(simulate([0]), **kwargs)

    def test_half_open_arrival_cohort(self):
        with self.assertRaises(ValueError):
            summarize(simulate([0, 10000]))

    def test_duplicate_ids_rejected(self):
        t = simulate([0])[0]
        with self.assertRaises(ValueError):
            summarize([t, t])

    def test_empty_summary(self):
        with self.assertRaises(ValueError):
            summarize([])

    def test_bad_percentiles(self):
        for p in (0, 101, True, 95.5):
            with self.subTest(p=p), self.assertRaises(ValueError):
                percentile([1], p)

    def test_bad_percentile_samples(self):
        with self.assertRaises(ValueError):
            percentile([float('nan')], 95)


if __name__ == '__main__':
    unittest.main()
