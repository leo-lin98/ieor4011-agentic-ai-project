"""Test the non-sports handoff using real Parquet and simulated model calls."""
import argparse
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

from judge.pipeline import run_pipeline


class JudgePipelineTests(unittest.TestCase):
    def test_run_passes_only_this_runs_proposals_and_frozen_catalog_to_judge(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ("embeddings/embed.py", "proposer/__main__.py"):
                entry = root / name
                entry.parent.mkdir()
                entry.touch()
            data = root / "data"
            profiles = data / "market_profiles/market_profiles.parquet"
            profiles.parent.mkdir(parents=True)
            pq.write_table(pa.Table.from_pylist([
                {"platform": "KALSHI", "event_uid": "KALSHI:A", "category": "Economics"},
                {"platform": "KALSHI", "event_uid": "KALSHI:SPORT", "category": "Sports"},
                {"platform": "POLYUS", "event_uid": "POLYUS:B", "category": "macro"},
            ]), profiles)
            catalog = data / "market_catalog/current"
            catalog.mkdir(parents=True)
            for platform in ("KALSHI", "POLYUS"):
                pq.write_table(pa.table({"market_uid": [platform + ":1"]}), catalog / f"{platform}.parquet")
            args = argparse.Namespace(
                proposer_root=root, data_dir=None, cache=root / "cache.sqlite3", output_dir=root / "runs",
                query_id=["KALSHI:A"], embedding_model="test", judge_model="test",
                concurrency=2, dry_run=False, queries=None,
                live=False, tier=False, structured=True, check_order=True,
            )

            def stage(command, **kwargs):
                if command[2] == "collector":
                    self.assertEqual(command[3:], ["catalog", "--no-stats-sweep"])
                    shutil.copytree(data, kwargs["env"]["IEOR_COLLECTOR_DATA_DIR"])
                elif command[2] == "embeddings.embed":
                    filtered = pq.read_table(command[command.index("--profiles") + 1])
                    self.assertEqual(filtered["event_uid"].to_pylist(), ["KALSHI:A", "POLYUS:B"])
                    self.assertEqual(command[-2:], ["--queries", "2"] if args.live else ["--query-id", "KALSHI:A"])
                    Path(command[command.index("--output") + 1]).write_text("{}")
                elif command[1] == "-c":
                    output = Path(command[3])
                    pq.write_table(pa.Table.from_pylist([
                        {"pair_id": "new", "source": "proposer"},
                        {"pair_id": "unrelated", "source": "manual"},
                    ]), output / "match_flags.parquet")
                else:
                    expected_mode = ["judge", "tier", "run"] if args.live else ["judge", "pairs"]
                    self.assertEqual(command[2:2 + len(expected_mode)], expected_mode)
                    if not args.live:
                        self.assertIn("--structured", command)
                        self.assertIn("--check-order", command)
                    input_flag = "--pairs" if args.live else "--input"
                    path = Path(command[command.index(input_flag) + 1])
                    self.assertEqual(json.loads(path.read_text()), [{"pair_id": "new", "source": "proposer"}])
                    self.assertEqual(Path(command[command.index("--catalog-dir") + 1]), path.parent / "catalog")
                    json.dump([] if args.live else {"human_review_queue": []}, kwargs["stdout"])

            for live in (False, True):
                with self.subTest(live=live):
                    args.live, args.structured, args.check_order = live, not live, not live
                    args.query_id, args.queries = (None, 2) if live else (["KALSHI:A"], None)
                    with patch.dict(os.environ, {"OPENAI_API_KEY": "mock"}), patch(
                        "judge.pipeline.subprocess.run", side_effect=stage
                    ) as runner:
                        run = run_pipeline(args)
                    self.assertEqual(runner.call_count, 4 if live else 3)
                    self.assertTrue((run / "reviews.json").is_file())
                    self.assertFalse((data / "match_flags").exists())
                    self.assertFalse((run / "reviews.pending").exists())
