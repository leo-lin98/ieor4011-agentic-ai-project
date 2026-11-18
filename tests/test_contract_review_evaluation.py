import unittest

from evaluation.retrieval.review_contracts import summarize


class ContractReviewEvaluationTests(unittest.TestCase):
    def test_coverage_excludes_events_outside_run_and_pool(self):
        rows = [
            {'outcome': 'matching', 'kalshi_in_retrieval_run': '1', 'polyus_in_retrieval_pool': '1', 'candidate_rank': '2'},
            {'outcome': 'matching', 'kalshi_in_retrieval_run': '1', 'polyus_in_retrieval_pool': '1', 'candidate_rank': ''},
            {'outcome': 'matching', 'kalshi_in_retrieval_run': '0', 'polyus_in_retrieval_pool': '1', 'candidate_rank': ''},
            {'outcome': 'matching', 'kalshi_in_retrieval_run': '1', 'polyus_in_retrieval_pool': '0', 'candidate_rank': ''},
            {'outcome': 'not_found'}, {'outcome': 'no_polymarket_match'},
        ]
        result = summarize(rows)
        self.assertEqual(result['reviewed_contracts'], 6)
        self.assertEqual(result['eligible_matching_contracts'], 2)
        self.assertEqual(result['matching_event_displayed'], 1)
        self.assertEqual(result['displayed_event_coverage'], .5)
        self.assertEqual(result['matching_source_not_in_run'], 1)
        self.assertEqual(result['matching_target_not_in_pool'], 1)
        self.assertEqual(result['outcomes']['not_found'], 1)

    def test_unknown_outcome_rejected(self):
        with self.assertRaises(ValueError):
            summarize([{'outcome': 'missing'}])


if __name__ == '__main__':
    unittest.main()
