"""Verify evidence grounding, deterministic comparisons, and order checking."""
import unittest

from judge import extraction
from judge.pairs import review_pair
from test_non_sports_judge import CATALOGS, PAIR


def fields(**quotes):
    return {"fields": [
        {"field": name, "status": "stated" if name in quotes else "unstated",
         "interpretation": quotes.get(name, ""), "quotes": (
             [{"document_id": "rule_1", "quote": quotes[name]}] if name in quotes else []
         )}
        for name in extraction.FIELDS
    ]}


def payout(amount, text, reason):
    return {"amount_per_dollar": amount, "reasoning": reason,
            "quotes": [{"document_id": "rule_1", "quote": text}]}


class StructuredJudgeTests(unittest.TestCase):
    def test_rejects_unverified_quote(self):
        with self.assertRaisesRegex(ValueError, "Unverified quote"):
            extraction.validate(fields(threshold="above 4.1%"), {"rule_1": "above 4.2%"})

    def test_normalizes_thresholds_and_time_offsets(self):
        left = fields(threshold="above 4.10%", cutoff="before 2026-12-31T18:00:00-05:00")
        right = fields(threshold="at least 4.1%", cutoff="before 2026-12-31T23:00:00Z")
        l, r = [extraction.validate(record, {"rule_1": " ".join(
            item["interpretation"] for item in record["fields"]
        )}) for record in (left, right)]
        checks = extraction.compare_terms(l, r, "same")
        self.assertEqual(checks["threshold"]["status"], "different_literal")
        self.assertEqual(checks["cutoff"]["status"], "same_literal")
        self.assertEqual(extraction.compare_terms(l, r, "opposite")["threshold"]["status"], "not_compared")

    def _compare_scenarios(self, amounts, reverse_amounts):
        comparisons = []

        def call(model, system, payload, schema):
            if list(payload) == ["documents"]:
                return fields(outcome=payload["documents"]["rule_1"]), [], {}
            if "scenarios" not in payload:
                return {"ordinary_yes": "Both published readings cross the threshold.",
                        "ordinary_no": "Both published readings fall short.",
                        "additional_scenarios": [{"facts": "The initial and revised readings differ.",
                                                   "scope": "exceptional", "dimension": "release_and_revision"}]}, [], {}
            venues = payload["venues"]
            comparisons.append(payload)
            self.assertEqual(payload["direction"], "same")
            ordered = list(venues) == ["kalshi", "polyus"]
            values = amounts if ordered else reverse_amounts
            cases = payload["scenarios"] if ordered else reversed(payload["scenarios"])
            return {"assessments": [
                {"scenario_id": case["scenario_id"], **{
                    side: payout(amount, venues[side]["documents"]["rule_1"],
                                 "First explanation." if ordered else "Rephrased explanation.")
                    for side, amount in zip(("kalshi", "polyus"), values[case["scenario_id"]])
                }}
                for case in cases
            ]}, [], {}
        result = review_pair(PAIR, CATALOGS, model="mock", judge=call, structured=True, check_order=True)
        self.assertEqual([list(payload["venues"]) for payload in comparisons],
                         [["kalshi", "polyus"], ["polyus", "kalshi"]])
        self.assertEqual(comparisons[0]["scenarios"], comparisons[1]["scenarios"])
        return result

    def test_changed_assessment_on_same_facts_stays_unresolved(self):
        amounts = {"s1": ("1", "1"), "s2": ("0", "0"), "s3": ("0", None)}
        result = self._compare_scenarios(amounts, {**amounts, "s3": ("0", "1")})
        self.assertEqual(result["order_check"]["status"], "disagrees")
        self.assertEqual(result["order_check"]["disagreed_scenarios"], ["s3"])
        self.assertEqual(result["scenario_results"][2]["status"], "unresolved")
        self.assertEqual(result["verdict"]["verdict"], "unsure")
        self.assertEqual(result["ordinary_mapping"]["verdict"], "identical")
        self.assertEqual(result["status"], "needs_human_review")

    def test_rewording_and_equivalent_decimal_formats_agree(self):
        amounts = {"s1": ("1", "1"), "s2": ("0", "0"), "s3": ("0", "0.50")}
        result = self._compare_scenarios(amounts, {**amounts, "s3": ("0.0", "0.5")})
        self.assertEqual(result["order_check"]["status"], "agrees")
        self.assertEqual(result["scenario_results"][2]["status"], "payouts_differ")
        self.assertEqual(result["verdict"]["verdict"], "not_identical")

    def test_missing_payout_does_not_confirm_split_even_when_orders_agree(self):
        amounts = {"s1": ("1", "1"), "s2": ("0", "0"), "s3": ("0", None)}
        result = self._compare_scenarios(amounts, amounts)
        self.assertEqual(result["order_check"]["status"], "agrees")
        self.assertEqual(result["scenario_results"][2]["status"], "unresolved")
        self.assertEqual(result["verdict"]["verdict"], "unsure")

    def test_failed_grounding_prevents_comparison(self):
        def call(model, system, payload, schema):
            self.assertEqual(list(payload), ["documents"])
            return fields(outcome="Unsupported payout condition"), [], {}

        result = review_pair(PAIR, CATALOGS, model="mock", judge=call, structured=True)
        self.assertEqual(result["status"], "needs_human_review")
        self.assertNotIn("verdict", result)
        self.assertIn("Unverified quote", result["error_detail"])
