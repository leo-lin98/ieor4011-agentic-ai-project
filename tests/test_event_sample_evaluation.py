import unittest
from evaluation.retrieval.evaluate_event_sample import evaluate


class EventSampleEvaluationTests(unittest.TestCase):
    def test_event_level_hits_multiple_labels_and_pool_exclusions(self):
        rows = [
            {'set_id': 's', 'kalshi_uid': 'k1', 'outcome': 'found_top10',
             'related_events': [{'event_uid': 'p1', 'judgment': 'likely_exact'},
                                {'event_uid': 'p2', 'judgment': 'likely_exact'},
                                {'event_uid': 'p3', 'judgment': 'possible'}],
             'suggestions': [{'candidate_uid': 'p1'}]},
            {'set_id': 's', 'kalshi_uid': 'k2', 'outcome': 'not_on_platform',
             'related_events': [], 'suggestions': []},
            {'set_id': 's', 'kalshi_uid': 'k3', 'outcome': 'found_top10',
             'related_events': [{'event_uid': 'outside', 'judgment': 'likely_exact'}], 'suggestions': []},
        ]
        results = {'k1': [{'candidate_uid': 'p2'}, {'candidate_uid': 'p3'}], 'k2': [], 'k3': []}
        report = evaluate(rows, results, {'p1', 'p2', 'p3'})
        exact = report['metrics']['likely_exact']
        self.assertEqual(exact['events_with_labels'], 2)
        self.assertEqual(exact['events_with_targets_in_pool'], 1)
        self.assertEqual(exact['events_with_targets_outside_pool'], 1)
        self.assertEqual(exact['events_with_any_target_in_top_10'], 1)
        self.assertEqual(report['metrics']['possible']['events_with_any_target_in_top_10'], 1)
        self.assertEqual(report['outcomes']['not_on_platform'], 1)

    def test_missing_query_is_an_error(self):
        with self.assertRaises(ValueError):
            evaluate([{'set_id': 's', 'kalshi_uid': 'k', 'outcome': 'unreviewed', 'related_events': [], 'suggestions': []}], {}, set())


if __name__ == '__main__': unittest.main()
