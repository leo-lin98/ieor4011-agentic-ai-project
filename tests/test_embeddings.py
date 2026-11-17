import tempfile
import unittest
from pathlib import Path
import numpy as np
from embeddings.prepare import build_events, event_text
from embeddings.cache import embed_events
from embeddings.search import configs, eligible, rank
from embeddings.report import render
from evaluation.retrieval.evaluate import evaluate

class Model:
    def __init__(self): self.calls = 0
    def encode_query(self, texts, **kwargs):
        self.calls += len(texts); return np.array([[1., 0.] for _ in texts])
    encode_document = encode_query

class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.rows = [dict(platform='KALSHI', event_uid='k', market_uid='1', title='Election', category='politics', market_title='A', yes_means='A', rules='secret'),
                     dict(platform='KALSHI', event_uid='k', market_uid='2', title='Election', category='sports', market_title='B', yes_means='B'),
                     dict(platform='POLYUS', event_uid='p', market_uid='3', title='Election', category='politics', market_title='A', yes_means='A')]
    def test_grouping(self):
        events, issues = build_events(self.rows)
        self.assertEqual([e['option_count'] for e in events], [2, 1])
        self.assertIsNone(events[0]['category']); self.assertEqual(issues['conflicting_category'], 1)
        self.assertNotIn('secret', event_text(events[0], 'title_options'))
        self.assertEqual(event_text(events[0], 'title'), 'Election')
        self.assertEqual(event_text(events[0], 'title_options'), event_text(build_events(list(reversed(self.rows)))[0][0], 'title_options'))
    def test_cache(self):
        events, _ = build_events(self.rows); model = Model()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'cache.sqlite'
            def run(revision='rev', rep='title'): return embed_events(events, model, 'model', revision, rep, 'query', path, lambda _: None)
            run(); run(); self.assertEqual(model.calls, 2)
            events[0]['title'] = 'Changed'; run(); self.assertEqual(model.calls, 3)
            run('new'); self.assertEqual(model.calls, 5)
            run('new', 'title_options'); self.assertEqual(model.calls, 7)
    def test_fallbacks_and_rank(self):
        c, s, _ = configs(); q = {'category':'Sports','tags':['Hockey']}
        self.assertFalse(eligible(q, {'category':'sports','subcategory':'FOOTBALL'}, c, s))
        self.assertTrue(eligible(q, {'category':None}, c, s))
        self.assertTrue(eligible({'category':None}, {'category':'politics'}, c, s))
        self.assertEqual(rank({}, [{}, {}], np.array([1.,0.]), np.array([[0.,1.],[1.,0.]]), 2, c, s)[0][0], 1)
    def test_evaluation_and_report(self):
        events, _ = build_events(self.rows)
        data = {'metadata': {'candidate_ids':['p']}, 'results':[{'query':events[0], 'candidates':[{'event':events[1], 'score':.9}], 'ranked_candidate_ids':['p'], 'excluded_candidate_ids':[]}]}
        cases = [{'kalshi_uid':'k','polyus_uid':'p','relationship':'matching','split':'test'}]
        self.assertEqual(evaluate(data, cases)['test']['recall_at_5'], 1)
        data['results'][0]['ranked_candidate_ids'] = []; data['results'][0]['excluded_candidate_ids'] = ['p']
        self.assertTrue(evaluate(data, cases)['test']['examples'][0]['excluded_by_filter'])
        html = render(data); self.assertIn('Event similarity', html); self.assertIn('Full rules', html)
if __name__ == '__main__': unittest.main()
