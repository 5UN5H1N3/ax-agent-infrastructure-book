import unittest
from dataclasses import replace

from ch21_inference_measurements import Trace, demo, percentile, request_metrics, simulate, summarize


class MeasurementTests(unittest.TestCase):
    def trace(self):
        return Trace('r1', 0, 100, (300, 500, 800, 1100), 1200, True)

    def test_request_metrics(self):
        self.assertEqual(request_metrics(self.trace()), {
            'queue_ms': 100, 'ttft_ms': 300, 'e2e_ms': 1200,
            'tpot_ms': 800/3, 'itl_ms': (200, 300, 300)})

    def test_one_token(self):
        metrics = request_metrics(replace(self.trace(), token_ms=(300,)))
        self.assertIsNone(metrics['tpot_ms'])
        self.assertEqual(metrics['itl_ms'], ())

    def test_no_tokens_on_failure(self):
        metrics = request_metrics(replace(self.trace(), status='error', token_ms=()))
        self.assertIsNone(metrics['ttft_ms'])
        self.assertIsNone(metrics['tpot_ms'])

    def test_percentile_nearest_rank(self):
        self.assertEqual(percentile([4, 1, 3, 2], 50), 2)
        self.assertEqual(percentile([4, 1, 3, 2], 95), 4)
        self.assertEqual(percentile([7], 99), 7)
        self.assertIsNone(percentile([], 95))

    def test_bad_percentile(self):
        for p in [0, -1, 101, float('nan'), float('inf')]:
            with self.subTest(p=p), self.assertRaises(ValueError):percentile([1], p)
        with self.assertRaises(ValueError):percentile([float('nan')], 95)

    def test_bad_timestamps(self):
        for changes in [{'sent_ms': -1}, {'sent_ms': True}, {'ended_ms': 1.5},
                        {'started_ms': 1300}, {'token_ms': (400, 300)},
                        {'token_ms': (1300,)}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.trace(), **changes)

    def test_bad_envelope(self):
        for changes in [{'request_id': ''}, {'valid': 'yes'}, {'status': 'unknown'},
                        {'token_ms': ()}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(self.trace(), **changes)

    def test_equal_timestamp_resolution(self):
        metrics = request_metrics(replace(self.trace(), token_ms=(300, 300)))
        self.assertEqual(metrics['itl_ms'], (0,))

    def test_closed_arrivals(self):
        traces = simulate((0, 1000, 2000, 3000))
        self.assertEqual([t.started_ms-t.sent_ms for t in traces], [0]*4)
        self.assertEqual([t.ended_ms for t in traces], [1000, 2000, 3000, 4000])

    def test_open_arrivals(self):
        traces = simulate((0, 250, 500, 750))
        self.assertEqual([t.started_ms-t.sent_ms for t in traces], [0, 750, 1500, 2250])
        self.assertEqual([request_metrics(t)['ttft_ms'] for t in traces], [200, 950, 1700, 2450])

    def test_idle_gap(self):
        traces = simulate((0, 5000), (True, True))
        self.assertEqual([t.started_ms for t in traces], [0, 5000])

    def test_bad_arrivals(self):
        for arrivals in [(), (0,), (0, 1.5, 2, 3), (0, 2, 1, 3)]:
            with self.subTest(arrivals=arrivals), self.assertRaises(ValueError):simulate(arrivals)

    def test_goodput_excludes_invalid_and_late(self):
        report = summarize(simulate((0, 250, 500, 750)), 0, 4000, 2000)
        self.assertEqual(report['transport_ok'], 4)
        self.assertEqual(report['invalid_completed'], 1)
        self.assertEqual(report['on_time_valid'], 2)
        self.assertEqual(report['goodput_rps'], 0.5)

    def test_deadline_boundary(self):
        self.assertEqual(summarize([self.trace()], 0, 1200, 1200)['on_time_valid'], 1)
        self.assertEqual(summarize([self.trace()], 0, 1200, 1199)['on_time_valid'], 0)

    def test_failed_attempts_retained(self):
        traces = [self.trace(), replace(self.trace(), request_id='r2', status='error'),
                  Trace('r3', 0, 0, (), 0, False, 'rejected')]
        report = summarize(traces, 0, 1200, 2000)
        self.assertEqual(report['attempts'], 3)
        self.assertEqual(report['failed'], 2)
        self.assertEqual(report['transport_ok'], 1)
        self.assertEqual(report['on_time_valid'], 1)

    def test_all_failed(self):
        t = Trace('r1', 0, 0, (), 1000, False, 'cancelled')
        report = summarize([t], 0, 1000, 2000)
        self.assertIsNone(report['e2e_p95_ms'])
        self.assertEqual(report['goodput_rps'], 0)

    def test_window_includes_drain(self):
        traces = simulate((0, 250, 500, 750))
        with self.assertRaises(ValueError):summarize(traces, 0, 1000, 2000)
        with self.assertRaises(ValueError):summarize(traces, 1, 4000, 2000)

    def test_invalid_window(self):
        for start, end, deadline in [(0,0,1), (1,0,1), (0,1200,0), (0.0,1200,1)]:
            with self.subTest(start=start, end=end, deadline=deadline), self.assertRaises(ValueError):
                summarize([self.trace()], start, end, deadline)
        with self.assertRaises(ValueError):summarize([], 0, 1200, 2000)

    def test_duplicate_id(self):
        with self.assertRaises(ValueError):summarize([self.trace()]*2, 0, 1200, 2000)

    def test_common_throughput_different_goodput(self):
        output = demo()
        self.assertEqual(output['closed']['output_tokens_per_s'], 4)
        self.assertEqual(output['open']['output_tokens_per_s'], 4)
        self.assertEqual(output['closed']['goodput_rps'], 0.75)
        self.assertEqual(output['open']['goodput_rps'], 0.5)
        self.assertEqual(output['open']['e2e_p95_ms'], 3250)


if __name__ == '__main__':
    unittest.main()
