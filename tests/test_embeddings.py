import unittest
import numpy as np
from embeddings.prepare import text_for
from embeddings.search import configs, eligible, rank
from evaluation.retrieval.evaluate import evaluate

class RetrievalTests(unittest.TestCase):
    def test_text(self):
        self.assertEqual(text_for({'title':' Event ', 'market_title': None, 'yes_means':'Yes', 'summary':'omit'}), 'title: Event\nyes_means: Yes')
    def test_fallbacks(self):
        c, s, _ = configs()
        q = {'category':'Sports','tags':['Hockey']}
        self.assertFalse(eligible(q, {'category':'sports','subcategory':'FOOTBALL'}, c, s))
        self.assertTrue(eligible(q, {'category':'sports','subcategory':'unknown'}, c, s))
        self.assertTrue(eligible(q, {'category':'new'}, c, s))
        self.assertTrue(eligible({'category':'new'}, {'category':'politics'}, c, s))
    def test_rank(self):
        c, s, _ = configs()
        results = rank({'category':'unknown'}, [{'category':'unknown'}, {'category':'unknown'}], np.array([1.,0.]), np.array([[0.,1.],[1.,0.]]), 2, c, s)
        self.assertEqual(results[0][0], 1)
    def test_evaluation(self):
        data = {'metadata':{'candidate_ids':['p']},'results':[{'query':{'market_uid':'k'},'candidates':[{'market':{'market_uid':'p'}}]}]}
        report = evaluate(data, [{'kalshi_uid':'k','polyus_uid':'p','relationship':'equivalent'}])
        self.assertEqual(report['equivalent']['recall_at_5'], 1)
        with self.assertRaises(ValueError): evaluate(data, [{'kalshi_uid':'k','polyus_uid':'absent','relationship':'equivalent'}])
if __name__ == '__main__': unittest.main()
