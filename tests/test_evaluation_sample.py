import sqlite3
import unittest
from embeddings.reporting.browser import sample_events


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE events(event_uid TEXT, title TEXT, category TEXT, total_volume REAL,
                            markets_with_volume INTEGER, market_count INTEGER);
        CREATE TABLE matches(event_uid TEXT, rank INTEGER, candidate_uid TEXT,
                             candidate_title TEXT, candidate_category TEXT, score REAL);
        CREATE TABLE run_metadata(key TEXT, value TEXT);
        INSERT INTO run_metadata VALUES('volume_snapshot_latest_ms','123');
        ''')
        self.db.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)', [
            (str(i), f'Event {i}', 'Sports' if i == 0 else 'Elections',
             None if i == 1 else (0 if i == 2 else i), 1, 1) for i in range(40)])
        self.db.execute("INSERT INTO matches VALUES ('3',1,'p','Candidate','politics',.9)")

    def tearDown(self):
        self.db.close()

    def test_weighted_sample_is_reproducible_and_eligible(self):
        first = sample_events(self.db, 123, 'volume', 'non-sports')
        second = sample_events(self.db, 123, 'volume', 'non-sports')
        self.assertEqual(first, second)
        self.assertEqual(first['eligible_events'], 37)
        self.assertEqual(len({e['event_uid'] for e in first['events']}), 20)
        self.assertTrue(all(e['category'] != 'Sports' and e['total_volume'] > 0 for e in first['events']))
        self.assertEqual(first['snapshot_ms'], 123)

    def test_uniform_can_include_missing_and_sports(self):
        sample = sample_events(self.db, 8, 'uniform', 'all', size=40)
        self.assertEqual(sample['eligible_events'], 40)
        self.assertTrue(any(e['total_volume'] is None for e in sample['events']))
        self.assertEqual(next(e for e in sample['events'] if e['event_uid'] == '3')['candidates'][0]['candidate_uid'], 'p')

    def test_sports_filter_and_volume_eligibility(self):
        self.db.executemany('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?)', [
            ('s1', 'Game 1', ' sports ', 5, 1, 1),
            ('s2', 'Game 2', 'SPORTS', 0, 1, 1),
            ('unknown', 'Unknown', None, 10, 1, 1),
        ])
        uniform = sample_events(self.db, 2, 'uniform', 'sports', size=3)
        self.assertEqual({e['event_uid'] for e in uniform['events']}, {'0', 's1', 's2'})
        weighted = sample_events(self.db, 2, 'volume', 'sports', size=1)
        self.assertEqual(weighted['eligible_events'], 1)
        self.assertEqual(weighted['events'][0]['event_uid'], 's1')
        self.assertEqual(weighted['category'], 'sports')

    def test_rejects_insufficient_or_invalid(self):
        with self.assertRaises(ValueError): sample_events(self.db, 1, 'volume', 'all', size=100)
        with self.assertRaises(ValueError): sample_events(self.db, 1, 'bogus', 'all')


if __name__ == '__main__':
    unittest.main()
