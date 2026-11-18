from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from embeddings.reporting.database import build_report


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
                self.assertEqual(db.execute("SELECT total_volume FROM events WHERE event_uid='e3'").fetchone(), (None,))
                self.assertEqual(db.execute("SELECT value FROM run_metadata WHERE key='volume_snapshot_latest_ms'").fetchone(), ('2',))


if __name__ == '__main__':
    unittest.main()
