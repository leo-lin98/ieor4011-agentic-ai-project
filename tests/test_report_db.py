from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from embeddings.reporting.database import build_report
from embeddings.reporting.browser import evaluate_report, sample_events


class ReportDatabaseTests(unittest.TestCase):
    def test_import_filter_volume_and_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles = root / 'profiles.parquet'
            pd.DataFrame([
                {'market_uid': 'a', 'event_uid': 'e1', 'platform': 'KALSHI'},
                {'market_uid': 'b', 'event_uid': 'e1', 'platform': 'KALSHI'},
                {'market_uid': 'c', 'event_uid': 'e2', 'platform': 'KALSHI'},
            ]).to_parquet(profiles)
            snapshots = root / 'snapshots'
            snapshots.mkdir()
            pd.DataFrame([
                {'market_uid': 'a', 'tick_ts': 1, 'volume': 10},
                {'market_uid': 'a', 'tick_ts': 2, 'volume': 20},
                {'market_uid': 'c', 'tick_ts': 2, 'volume': 100},
            ]).to_parquet(snapshots / 'pricing.parquet')
            data = {'metadata': {'candidate_events': [{'event_uid': 'p', 'title': 'Candidate', 'category': 'Politics'}]},
                    'results': [
                        {'query': {'event_uid': 'e1', 'title': 'Election', 'category': 'Elections'},
                         'candidates': [{'event_uid': 'p', 'score': 0.9}]},
                        {'query': {'event_uid': 'e2', 'title': 'Game', 'category': 'Sports'}, 'candidates': []},
                        {'query': {'event_uid': 'e3', 'title': 'Unknown', 'category': None, 'option_count': 1}, 'candidates': []},
                    ]}
            source, output = root / 'results.json', root / 'report.sqlite3'
            source.write_text(json.dumps(data, separators=(',', ':')))
            self.assertEqual(build_report(source, output, snapshots, profiles), 3)
            with closing(sqlite3.connect(output)) as db:
                rows = db.execute("""SELECT e.event_uid, e.total_volume, e.markets_with_volume,
                    e.market_count, m.candidate_title FROM events e
                    LEFT JOIN matches m ON m.event_uid = e.event_uid AND m.rank = 1
                    WHERE e.category IS NOT NULL AND lower(trim(e.category)) <> 'sports'
                    ORDER BY e.total_volume IS NULL, e.total_volume DESC""").fetchall()
                self.assertEqual(rows, [('e1', 20.0, 1, 2, 'Candidate')])
                self.assertEqual(db.execute("SELECT market_uid, in_retrieval_pool FROM contracts WHERE market_uid='a'").fetchone(), ('a', 1))
                self.assertEqual(db.execute("SELECT total_volume FROM events WHERE event_uid='e3'").fetchone(), (None,))
                self.assertEqual(db.execute("SELECT value FROM run_metadata WHERE key='volume_snapshot_latest_ms'").fetchone(), ('2',))

    def test_polyus_first_report_reviews_and_pool(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles = root / 'profiles.parquet'
            pd.DataFrame([
                {'market_uid': 'p-market', 'event_uid': 'p', 'platform': 'POLYUS'},
                {'market_uid': 'excluded', 'event_uid': 'outside', 'platform': 'POLYUS'},
                {'market_uid': 'k-market', 'event_uid': 'k', 'platform': 'KALSHI'},
                {'market_uid': 'k-missed', 'event_uid': 'missed', 'platform': 'KALSHI'},
                {'market_uid': 'k-out', 'event_uid': 'out', 'platform': 'KALSHI'},
            ]).to_parquet(profiles)
            snapshots = root / 'snapshots'
            snapshots.mkdir()
            pd.DataFrame([{'market_uid': 'p-market', 'tick_ts': 5, 'volume': 12}]).to_parquet(snapshots / 'pricing.parquet')
            data = {'metadata': {'scope': 'non-sports', 'query_platform': 'POLYUS', 'candidate_platform': 'KALSHI',
                                 'candidate_ids': ['k', 'missed']},
                    'results': [{'query': {'event_uid': 'p', 'title': 'Rates', 'category': 'macro'},
                                 'candidates': [{'event': {'event_uid': 'k'}, 'score': 0.9}]}]}
            source, output = root / 'results.json', root / 'report.sqlite3'
            source.write_text(json.dumps(data, separators=(',', ':')))
            self.assertEqual(build_report(source, output, snapshots, profiles), 1)
            with closing(sqlite3.connect(output)) as db:
                db.row_factory = sqlite3.Row
                pool = {row[0] for row in db.execute('SELECT market_uid FROM contracts WHERE in_retrieval_pool')}
                self.assertEqual(pool, {'p-market', 'k-market', 'k-missed'})
                sample = sample_events(db, 3, 'volume', 'non-sports', size=1, platform='POLYUS')
                self.assertEqual((sample['events'][0]['event_uid'], sample['events'][0]['total_volume']), ('p', 12))
                reviews = [dict(set_id='s', query_platform='POLYUS', candidate_platform='KALSHI', query_uid='p',
                                outcome='in_collection_not_top10', suggestions=[],
                                related_events=[{'event_uid': 'missed', 'judgment': 'likely_exact'},
                                                {'event_uid': 'out', 'judgment': 'possible'}])]
                score = evaluate_report(db, reviews)
                self.assertEqual(score['metrics']['likely_exact']['event_hit_rate_at_3'], 0)
                self.assertEqual(score['metrics']['possible']['events_with_targets_outside_pool'], 1)
                reviews[0]['related_events'][0]['event_uid'] = 'k'
                self.assertEqual(evaluate_report(db, reviews)['metrics']['likely_exact']['event_hit_rate_at_10'], 1)


if __name__ == '__main__':
    unittest.main()
