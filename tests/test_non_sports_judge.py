"""Contract-level rubric selection and review routing."""
import unittest

from judge.pairs import review_pair, review_questions
from judge.rubrics.non_sports import for_pair

PAIR = {
    "kalshi_market_uid": "KALSHI:CPI", "polyus_market_uid": "POLYUS:1",
    "relationship": "identical", "kalshi_rules_hash": "first",
}
CATALOGS = {
    "KALSHI:CPI": {
        "market_uid": "KALSHI:CPI", "category": "Economics",
        "rules_primary": "Use the first BLS release.", "rules_hash": "first",
    },
    "POLYUS:1": {
        "market_uid": "POLYUS:1", "category": "Macro",
        "description": "Use the revised BLS release.",
    },
}


def judge(verdict, findings, *, material=True, scope="ordinary"):
    def call(model, system, evidence, schema):
        checklist = [
            {"dimension": dimension, "same": findings.get(dimension, "yes"),
             "kalshi": "Use the first BLS release.", "polyus": "Use the revised BLS release.",
             "note": "Compare the settlement rules.",
             "scope": scope if material and findings.get(dimension) in ("no", "unsure") else "informational",
             "payout_scenario": (
                 "If the first release crosses the threshold and the revision does not, "
                 "Kalshi pays YES while Polymarket US pays NO."
                 if material and findings.get(dimension) in ("no", "unsure") else ""
             )}
            for dimension in schema["properties"]["checklist"]["items"]["properties"]["dimension"]["enum"]
        ]
        mapping = {"identical": "yes", "not_identical": "no", "unsure": "unsure"}
        return ({
            "verdict": verdict,
            "opposite_side_ok": mapping[verdict] if evidence["direction"] == "opposite" else "not_applicable",
            "confidence": 0.8, "reasoning": "Compare release vintages.", "human_review_question": "",
            "ordinary_mapping": {"verdict": verdict if scope == "ordinary" else "identical",
                                 "reasoning": "Compare the ordinary outcome."},
            "checklist": checklist,
        }, [], {})
    return call


class NonSportsJudgeTests(unittest.TestCase):
    def test_sports_pair_review_is_opt_in(self):
        catalogs = {uid: {**row, "category": "Sports"} for uid, row in CATALOGS.items()}
        self.assertEqual(review_pair(PAIR, catalogs, model="mock")["status"], "use_sports_judge")
        report = review_pair(PAIR, catalogs, model="mock", include_sports=True)
        self.assertEqual(report["status"], "dry_run")
        self.assertEqual(report["rubric"], "sports_pair")
        self.assertIn("game_and_period", report["system"])

    def test_shared_sports_exception_keeps_both_pair_findings(self):
        catalogs = {
            uid: {**row, "category": "Sports", "event_uid": "test-game",
                  "rules_secondary": "If canceled, settle to a fair price."}
            for uid, row in CATALOGS.items()
        }
        call = judge("unsure", {"exceptional_outcomes": "unsure"}, scope="exceptional")
        reviews = [review_pair({**PAIR, "pair_id": pid}, catalogs, model="mock",
                               judge=call, include_sports=True) for pid in ("same", "opposite")]
        self.assertEqual(reviews[0]["ordinary_mapping"]["verdict"], "identical")
        questions = review_questions(reviews)
        self.assertEqual(len(questions), 1)
        self.assertEqual(questions[0]["affected_pairs"], ["same", "opposite"])
        self.assertEqual(len(questions[0]["findings"]), 2)

    def test_specialized_and_common_rubrics(self):
        self.assertEqual(for_pair("Elections", "Politics").NAME, "politics")
        self.assertEqual(for_pair("Crypto", "Digital Assets").NAME, "crypto")
        self.assertEqual(for_pair("Climate and Weather", "Climate").NAME, "general")
        self.assertEqual(for_pair("Economics", "Crypto").NAME, "general")

    def test_material_difference_overrides_clean_verdict_for_routing(self):
        result = review_pair(PAIR, CATALOGS, model="mock",
                             judge=judge("identical", {"release_and_revision": "no"}))
        self.assertEqual(result["status"], "needs_human_review")
        self.assertIn("release_and_revision", result["review_reasons"])
        self.assertEqual(result["rubric"], "economics")
        self.assertEqual(result["evidence"]["polyus"]["rules"]["description"], "Use the revised BLS release.")

    def test_uncertain_complementary_pair(self):
        result = review_pair({**PAIR, "relationship": "complementary"}, CATALOGS, model="mock",
                             judge=judge("unsure", {"outcome_and_mapping": "unsure"}))
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["direction"], "opposite")

    def test_clean_verdict_is_a_finding(self):
        result = review_pair(PAIR, CATALOGS, model="mock", judge=judge("identical", {}))
        self.assertEqual(result["status"], "no_difference_found")

    def test_informational_uncertainty_stays_in_the_checklist(self):
        result = review_pair(PAIR, CATALOGS, model="mock",
                             judge=judge("identical", {"resolution_source": "unsure"}, material=False))
        self.assertEqual(result["status"], "no_difference_found")
        self.assertTrue(any(item["same"] == "unsure" for item in result["verdict"]["checklist"]))

    def test_adverse_verdict_without_scenario_is_queued_as_invalid(self):
        result = review_pair(PAIR, CATALOGS, model="mock",
                             judge=judge("not_identical", {"threshold": "no"}, material=False))
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["error_type"], "ValueError")

    def test_stale_and_missing_inputs_skip_model(self):
        def unexpected(*args):
            self.fail("Incomplete evidence should skip the model")

        for pair, catalogs in (({**PAIR, "kalshi_rules_hash": "old"}, CATALOGS), (PAIR, {}),
                               (PAIR, {**CATALOGS, "POLYUS:1": {"market_uid": "POLYUS:1"}})):
            with self.subTest(pair=pair, catalogs=catalogs):
                result = review_pair(pair, catalogs, model="mock", judge=unexpected)
                self.assertEqual(result["status"], "needs_human_review")

    def test_malformed_response_is_queued(self):
        result = review_pair(PAIR, CATALOGS, model="mock", judge=lambda *args: ({}, [], {}))
        self.assertEqual(result["status"], "needs_human_review")
        self.assertEqual(result["error_type"], "ValueError")
