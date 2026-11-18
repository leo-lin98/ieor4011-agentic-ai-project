import tempfile
import unittest
from pathlib import Path
import pandas as pd
from embeddings.reporting.volume import event_volumes

class VolumeTests(unittest.TestCase):
    def test_latest_market_values_and_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pd.DataFrame([{'market_uid': 'a', 'event_uid': 'k', 'platform': 'KALSHI'}, {'market_uid': 'b', 'event_uid': 'k', 'platform': 'KALSHI'}, {'market_uid':'c','event_uid':'x','platform':'KALSHI'}]).to_parquet(root / 'profiles.parquet')
            snapshots = root / 'snapshots'; snapshots.mkdir()
            pd.DataFrame([{'market_uid':'a','tick_ts':1,'volume':10}, {'market_uid':'a','tick_ts':2,'volume':20}, {'market_uid':'b','tick_ts':2,'volume':None}]).to_parquet(snapshots / 'pricing.parquet')
            volumes, timestamp = event_volumes(snapshots, root / 'profiles.parquet')
            self.assertEqual(volumes['k'], {'total':20., 'observed':1, 'contracts':2})
            self.assertIsNone(volumes['x']['total'])
            self.assertEqual(timestamp, 2)
if __name__ == '__main__': unittest.main()
