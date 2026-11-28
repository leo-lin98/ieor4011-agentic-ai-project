from contextlib import redirect_stderr, redirect_stdout
import hashlib
from io import StringIO
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from pathlib import Path
import numpy as np
import pandas as pd
from embeddings.embed import main
from embeddings.prepare import build_events, event_text
from embeddings.cache import embed_events
from embeddings.reporting.results import iter_results
from embeddings.search import configs, eligible, rank, sport

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
    def test_sports_and_non_sports_categories(self):
        c, s, _ = configs()
        self.assertFalse(eligible({'category': 'Sports'}, {'category': 'culture'}, c, s))
        self.assertFalse(eligible({'category': 'Entertainment'}, {'category': 'sports'}, c, s))
        self.assertTrue(eligible({'category': 'Entertainment'}, {'category': 'finance'}, c, s))
        self.assertTrue(eligible({'category': 'Sports'}, {'category': 'new category'}, c, s))
        self.assertTrue(eligible({'category': 'new category'}, {'category': 'sports'}, c, s))
    def test_expanded_sports_mapping(self):
        c, s, _ = configs()
        self.assertTrue(eligible({'category': 'sports', 'tags': ['Golf']},
                                 {'category': 'sports', 'subcategory': 'GOLF'}, c, s))
        self.assertFalse(eligible({'category': 'sports', 'tags': ['Golf']},
                                  {'category': 'sports', 'subcategory': 'MMA'}, c, s))
        self.assertEqual(sport({'subcategory': 'EFOOTBALL', 'tags': ['Esports']}, s, 'POLYUS'), 'esports')
        self.assertIsNone(sport({'subcategory': 'COMBAT'}, s, 'POLYUS'))
        self.assertIsNone(sport({'subcategory': 'EFOOTBALL', 'tags': ['Football']}, s, 'POLYUS'))
        self.assertTrue(eligible({'category': 'sports', 'tags': ['Boxing']},
                                 {'category': 'sports', 'subcategory': 'COMBAT'}, c, s))


class RetrievalCommandTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.profiles = self.root / 'profiles.parquet'
        self.output = self.root / 'results.json'
        self.role_calls = []

    @staticmethod
    def row(platform, uid, category, **fields):
        return dict(platform=platform, event_uid=uid, category=category, title=uid,
                    market_uid=uid + ':market', market_title='Option', yes_means='Yes', **fields)

    def run_retrieval(self, rows, *options):
        calls = self.role_calls

        class MockModel:
            device = 'cpu'

            def __init__(self, *args, **kwargs):
                pass

            def encode_query(self, titles, **kwargs):
                calls.append(('query', titles))
                return np.tile([1.0, 0.0], (len(titles), 1))

            def encode_document(self, titles, **kwargs):
                calls.append(('document', titles))
                return np.tile([0.6, 0.8], (len(titles), 1))

        pd.DataFrame(rows).to_parquet(self.profiles)
        args = ['embeddings.embed', '--profiles', str(self.profiles), '--output', str(self.output),
                '--cache', str(self.root / 'vectors.sqlite'), '--revision', 'a' * 40, *options]
        with (patch.dict(sys.modules, sentence_transformers=SimpleNamespace(SentenceTransformer=MockModel)),
              patch.object(sys, 'argv', args), redirect_stdout(StringIO())):
            main()
        records = iter_results(self.output)
        return next(records), list(records)

    def test_default_direction_scope_and_top_ten(self):
        rows = [self.row('KALSHI', f'k{i:02}', 'financials') for i in range(11)]
        query_ids = [f'p{i:02}' for i in range(12)]
        rows += [self.row('POLYUS', uid, 'macro') for uid in query_ids]
        rows += [self.row('KALSHI', 'k-sports', 'sports'), self.row('KALSHI', 'k-missing', None),
                 self.row('KALSHI', 'k-conflict', 'sports'), self.row('POLYUS', 'p-unmapped', 'unknown')]
        rows += [dict(rows[-2], market_uid='conflicting-market', category='politics'),
                 dict(rows[11], market_uid='sibling-market', rules='Sibling rules')]
        metadata, results = self.run_retrieval(rows)
        self.assertEqual((metadata['query_platform'], metadata['candidate_platform']), ('POLYUS', 'KALSHI'))
        self.assertEqual((metadata['scope'], metadata['top_k']), ('non-sports', 10))
        self.assertEqual(metadata['input_snapshot']['sha256'], hashlib.sha256(self.profiles.read_bytes()).hexdigest())
        self.assertEqual(metadata['category_counts']['POLYUS']['unmapped'], 1)
        self.assertEqual(metadata['category_counts']['KALSHI']['missing'], 1)
        self.assertEqual(metadata['category_counts']['KALSHI']['conflicting'], 1)
        self.assertEqual([r['query']['event_uid'] for r in results], query_ids)
        self.assertEqual([c['rules'] for c in results[0]['query']['contracts']], [None, 'Sibling rules'])
        for result in results:
            self.assertEqual([match['event']['event_uid'] for match in result['candidates']],
                             [f'k{i:02}' for i in range(10)])
            self.assertAlmostEqual(result['candidates'][0]['score'], 0.6)
            self.assertEqual(len(result['ranked_candidate_ids']), 11)
        self.assertEqual(self.role_calls, [('query', query_ids),
                                          ('document', [f'k{i:02}' for i in range(11)])])
        _, sample = self.run_retrieval(rows, '--queries', '2')
        self.assertEqual([r['query']['event_uid'] for r in sample], [query_ids[0], query_ids[-1]])
        self.assertEqual(len(self.role_calls), 2)

    def test_sports_filter_handles_both_directions_and_compact_matches(self):
        rows = [self.row('KALSHI', 'hockey', 'sports', tags=['Hockey']),
                 self.row('POLYUS', 'nhl', 'sports', subcategory='HOCKEY'),
                 self.row('POLYUS', 'nfl', 'sports', subcategory='FOOTBALL')]
        metadata, results = self.run_retrieval(rows, '--scope', 'sports', '--top-only')
        self.assertEqual([e['event_uid'] for e in metadata['candidate_events']], ['hockey'])
        self.assertEqual([r['query']['event_uid'] for r in results], ['nfl', 'nhl'])
        self.assertEqual(results[0]['candidates'], [])
        self.assertEqual([match['event_uid'] for match in results[1]['candidates']], ['hockey'])
        metadata, results = self.run_retrieval(rows, '--query-platform', 'KALSHI', '--scope', 'sports')
        self.assertEqual(results[0]['ranked_candidate_ids'], ['nhl'])
        self.assertEqual(results[0]['excluded_candidate_ids'], ['nfl'])
        self.assertEqual(self.role_calls[-2:], [('query', ['hockey']), ('document', ['nfl', 'nhl'])])

    def test_query_scope_validation_and_empty_candidate_pool(self):
        rows = [self.row('POLYUS', 'p-macro', 'macro'), self.row('POLYUS', 'p-missing', None),
                 self.row('KALSHI', 'k-sports', 'sports')]
        for uid in ('p-missing', 'k-sports'):
            stderr = StringIO()
            with redirect_stderr(stderr), self.assertRaises(SystemExit):
                self.run_retrieval(rows, '--query-id', uid)
            self.assertIn('unavailable for POLYUS in scope non-sports', stderr.getvalue())
        _, results = self.run_retrieval(rows, '--query-id', 'p-macro')
        self.assertEqual(results[0]['candidates'], [])
        self.assertEqual(self.role_calls, [])
        _, results = self.run_retrieval(rows, '--scope', 'all', '--query-id', 'p-missing')
        self.assertEqual(results[0]['ranked_candidate_ids'], ['k-sports'])

if __name__ == '__main__': unittest.main()
