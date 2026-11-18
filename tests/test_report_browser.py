import sqlite3
import unittest

from embeddings.reporting.browser import query_events


class ReportBrowserTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            CREATE TABLE events(event_uid TEXT, title TEXT, category TEXT, subcategory TEXT,
                                total_volume REAL, markets_with_volume INTEGER, market_count INTEGER);
            CREATE TABLE matches(event_uid TEXT, rank INTEGER, candidate_title TEXT, candidate_uid TEXT, score REAL);
            INSERT INTO events VALUES ('a', 'Election', 'Elections', NULL, 20, 1, 2);
            INSERT INTO events VALUES ('b', 'Game', 'Sports', NULL, 100, 1, 1);
            INSERT INTO events VALUES ('c', 'Unknown', NULL, NULL, NULL, 0, 1);
            INSERT INTO events VALUES ('d', 'Rates', 'Economics', NULL, 30, 1, 1);
            INSERT INTO matches VALUES ('a', 1, 'Election candidate', 'p', .9);
        ''')

    def tearDown(self):
        self.db.close()

    def test_default_non_sports_and_volume_sort(self):
        result = query_events(self.db, {})
        self.assertEqual([row['event_uid'] for row in result['events']], ['d', 'a'])
        self.assertEqual(result['events'][1]['candidate_title'], 'Election candidate')

    def test_filter_search_minimum_and_unknown(self):
        result = query_events(self.db, {'category': ['all'], 'search': ['e'], 'minimum': ['25']})
        self.assertEqual([row['event_uid'] for row in result['events']], ['b', 'd'])
        result = query_events(self.db, {'category': ['unknown']})
        self.assertEqual([row['event_uid'] for row in result['events']], ['c'])
        self.assertEqual(query_events(self.db, {'category': ['category:Elections']})['total'], 1)

    def test_invalid_input(self):
        for params in ({'minimum': ['nan']}, {'minimum': ['-1']}, {'sort': ['e.title; drop table events']}, {'page': ['0']}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                query_events(self.db, params)


if __name__ == '__main__':
    unittest.main()
