import unittest
from decimal import Decimal, getcontext
from ch30_platform_choice import Load, Option, OPTIONS, REQUIREMENTS, demo, monthly_cost, money, number, review


class RequirementReviewTests(unittest.TestCase):
    def test_all_supplied_pass(self):
        self.assertEqual(review(REQUIREMENTS, dict.fromkeys(REQUIREMENTS, True))["status"], "passes_supplied_checks")

    def test_missing_is_unknown(self):
        result = review(("a", "b"), {"a": True})
        self.assertEqual(result, {"status": "needs_evidence", "failed": [], "unknown": ["b"]})

    def test_explicit_unknown(self):
        self.assertEqual(review(("a",), {"a": None})["status"], "needs_evidence")

    def test_false_vetoes(self):
        self.assertEqual(review(("a", "b"), {"a": True, "b": False})["status"], "reject")

    def test_veto_and_unknown_both_preserved(self):
        self.assertEqual(review(("a", "b"), {"a": False}),
                         {"status": "reject", "failed": ["a"], "unknown": ["b"]})

    def test_order_follows_requirements(self):
        self.assertEqual(review(("b", "a"), {})["unknown"], ["b", "a"])

    def test_empty_not_vacuous_pass(self):
        with self.assertRaises(ValueError): review((), {})

    def test_duplicate_requirement(self):
        with self.assertRaises(ValueError): review(("a", "a"), {})

    def test_invalid_requirement_names(self):
        for value in (("",), (" ",), (1,), ["a"], "a"):
            with self.subTest(value=value), self.assertRaises(ValueError): review(value, {})

    def test_unknown_key_not_ignored(self):
        with self.assertRaises(ValueError): review(("a",), {"typo": True})

    def test_truthy_is_not_evidence(self):
        for value in (1, 0, "true", "false", [], {}):
            with self.subTest(value=value), self.assertRaises(ValueError): review(("a",), {"a": value})

    def test_checks_mapping_type(self):
        with self.assertRaises(ValueError): review(("a",), [])

    def test_inputs_not_changed(self):
        checks = {"a": True}
        review(("a", "b"), checks)
        self.assertEqual(checks, {"a": True})


class CostTests(unittest.TestCase):
    def test_pinned(self):
        self.assertEqual(monthly_cost(Load(), OPTIONS["pinned"])["total"], Decimal("29400"))

    def test_jobs(self):
        self.assertEqual(monthly_cost(Load(), OPTIONS["jobs"])["total"], Decimal("2200"))

    def test_ax(self):
        self.assertEqual(money(monthly_cost(Load(), OPTIONS["ax_data"])["total"]), "2929.33")

    def test_components(self):
        result = monthly_cost(Load(), OPTIONS["ax_data"])
        self.assertEqual(result["compute"], Decimal("1440"))
        self.assertEqual(money(result["transitions"]), "13.33")
        self.assertEqual(result["reserve"], Decimal("576"))
        self.assertEqual(result["storage"], Decimal("100"))
        self.assertEqual(result["platform"], Decimal("800"))

    def test_components_sum_before_rounding(self):
        result = monthly_cost(Load(), OPTIONS["ax_data"])
        self.assertEqual(sum(v for k, v in result.items() if k != "total"), result["total"])

    def test_small_population(self):
        self.assertEqual(money(monthly_cost(Load(sessions=10), OPTIONS["pinned"])["total"]), "789.00")
        self.assertEqual(money(monthly_cost(Load(sessions=10), OPTIONS["ax_data"])["total"]), "1391.53")

    def test_no_occupied_work(self):
        result = monthly_cost(Load(occupied_fraction="0"), OPTIONS["ax_data"])
        self.assertEqual(result["compute"], 0)
        self.assertGreater(result["total"], 0)

    def test_no_transitions(self):
        self.assertEqual(monthly_cost(Load(wakes_per_session=0), OPTIONS["ax_data"])["transitions"], 0)

    def test_full_occupancy_with_no_transition(self):
        self.assertEqual(monthly_cost(Load(occupied_fraction="1", wakes_per_session=0), OPTIONS["jobs"])["compute"], Decimal("28800"))

    def test_impossible_occupation_rejected(self):
        with self.assertRaises(ValueError): monthly_cost(Load(occupied_fraction="1"), OPTIONS["jobs"])

    def test_increasing_reserve_increases_estimate(self):
        a = monthly_cost(Load(), Option(False, "10", 20, "800"))["total"]
        b = monthly_cost(Load(), Option(False, "10", 21, "800"))["total"]
        self.assertEqual(b - a, Decimal("28.80"))

    def test_double_wakes_double_transition_only(self):
        a = monthly_cost(Load(), OPTIONS["jobs"])
        b = monthly_cost(Load(wakes_per_session=240), OPTIONS["jobs"])
        self.assertEqual(b["transitions"], a["transitions"] * 2)
        self.assertEqual(b["compute"], a["compute"])

    def test_zero_price(self):
        result = monthly_cost(Load(hourly_price="0", gib_month_price="0"), OPTIONS["ax_data"])
        self.assertEqual(result["total"], Decimal("800"))

    def test_invalid_numbers(self):
        for value in (True, False, -1, "-0.1", "NaN", "Infinity", "-Infinity", "bad", 0.05, None):
            with self.subTest(value=value), self.assertRaises(ValueError): number(value)

    def test_invalid_load_counts(self):
        for kwargs in ({"sessions": 0}, {"sessions": True}, {"sessions": 1.5},
                       {"wakes_per_session": -1}, {"wakes_per_session": False}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): Load(**kwargs)

    def test_invalid_load_duration(self):
        for kwargs in ({"month_hours": "0"}, {"month_hours": "-1"}, {"occupied_fraction": "1.01"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError): Load(**kwargs)

    def test_invalid_option(self):
        for args in ((1, "0", 0, "0"), (False, "-1", 0, "0"),
                     (False, "0", True, "0"), (False, "0", -1, "0"),
                     (False, "0", 0, "NaN"), (True, "1", 0, "0"), (True, "0", 1, "0")):
            with self.subTest(args=args), self.assertRaises(ValueError): Option(*args)

    def test_invalid_model_objects(self):
        with self.assertRaises(ValueError): monthly_cost({}, OPTIONS["jobs"])
        with self.assertRaises(ValueError): monthly_cost(Load(), {})

    def test_local_decimal_context(self):
        old = getcontext().prec
        try:
            getcontext().prec = 6
            result = monthly_cost(Load(), OPTIONS["ax_data"])
            self.assertEqual(getcontext().prec, 6)
            self.assertEqual(money(result["total"]), "2929.33")
        finally:
            getcontext().prec = old

    def test_round_half_up(self):
        self.assertEqual(money(Decimal("1.005")), "1.01")


class DemoTests(unittest.TestCase):
    def test_outcomes(self):
        result = demo()
        self.assertEqual(result["pinned"]["review"]["status"], "passes_supplied_checks")
        self.assertEqual(result["jobs"]["review"]["failed"], ["return_slo"])
        self.assertEqual(result["ax_data"]["review"]["unknown"], ["operations"])
        self.assertEqual(result["relaxed_jobs"]["status"], "passes_supplied_checks")
        self.assertEqual(result["ax_full_required"]["status"], "reject")

    def test_synthetic_never_claims_platform_validation(self):
        result = demo()
        self.assertTrue(result["synthetic_inputs"])
        self.assertFalse(result["platform_validated"])

    def test_deterministic(self):
        self.assertEqual(demo(), demo())


if __name__ == "__main__":
    unittest.main()
