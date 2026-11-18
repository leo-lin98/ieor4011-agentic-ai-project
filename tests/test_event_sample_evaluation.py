import unittest
import csv
import tempfile
from evaluation.retrieval.evaluate_event_sample import evaluate, load_cases


class EventSampleEvaluationTests(unittest.TestCase):
    def test_multiple_sets_keep_their_ids(self):
        rows = [dict(set_id=s, kalshi_uid=k, outcome='not_on_platform',
                     related_events='[]', suggestions='[]') for s, k in [('first', 'k1'), ('second', 'k2')]]
        with tempfile.NamedTemporaryFile(mode='w+', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
            f.flush()
            report = evaluate(load_cases(f.name))
        self.assertEqual(report['set_ids'], ['first', 'second'])
        self.assertIsNone(report['set_id'])
        self.assertEqual(report['events'], 2)

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

    def test_proposed_pairs_are_opt_in_and_outside_pool_are_not_misses(self):
        rows = [{'set_id': 's', 'kalshi_uid': 'k', 'outcome': 'in_collection_not_top10',
                 'related_events': [{'event_uid': 'p', 'judgment': 'proposed_likely_exact'}],
                 'suggestions': [], 'case_origin': 'assistant_handpicked'}]
        default = evaluate(rows, {'k': []}, set())
        self.assertEqual(default['metrics']['likely_exact']['events_with_labels'], 0)
        proposed = evaluate(rows, {'k': []}, set(), include_proposed=True)
        self.assertEqual(proposed['metrics']['likely_exact']['events_with_labels'], 1)
        self.assertEqual(proposed['metrics']['likely_exact']['events_with_targets_outside_pool'], 1)
        self.assertIsNone(proposed['metrics']['likely_exact']['event_hit_rate_at_10'])

    def test_missing_query_is_an_error(self):
        with self.assertRaises(ValueError):
            evaluate([{'set_id': 's', 'kalshi_uid': 'k', 'outcome': 'unreviewed', 'related_events': [], 'suggestions': []}], {}, set())


if __name__ == '__main__': unittest.main()
