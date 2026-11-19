import io
import os
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from collector.schemas import MARKET_CATALOG
from embeddings.catalog import Catalog, input_parts
from embeddings.run import CANDIDATES, Run, create_run, rank_batches, reserve_run, validate_candidates
from evaluation.retrieval import reviews
from evaluation.retrieval.browser import ReviewApp, review_handler, url
from evaluation.retrieval.evaluate import evaluate


def market(platform, contract, **overrides):
    return {
        "market_uid": f"{platform}:{contract}", "platform": platform,
        "native_market_id": contract, "event_uid": f"{platform}:event-{contract}",
        "event_title": "Test event", "market_title": "Test contract", "yes_title": " test  EVENT ",
        "category": " Sports ", "status_canonical": "open", "close_time_ms": 300,
        "catalog_pulled_ms": 100, "last_seen_ms": 100,
        "rules_primary": "Resolves Yes if the test passes.", "description": "Resolves Yes if the test passes.",
        **overrides,
    }


def save_catalog(root, kalshi, polyus):
    root.mkdir(exist_ok=True)
    for platform, rows in (("KALSHI", kalshi), ("POLYUS", polyus)):
        pq.write_table(pa.Table.from_pylist(rows, schema=MARKET_CATALOG), root / f"{platform}.parquet")


def browser_request(handler, target, data=None):
    body = urlencode(data).encode() if data else b""
    headers = ["Host: 127.0.0.1:8765", "Connection: close"]
    if body:
        headers.extend(["Content-Type: application/x-www-form-urlencoded", f"Content-Length: {len(body)}"])
    method = "POST" if body else "GET"
    raw = (f"{method} {target} HTTP/1.0\r\n" + "\r\n".join(headers) + "\r\n\r\n").encode()
    response = io.BytesIO()
    socket = SimpleNamespace(makefile=lambda *_: io.BytesIO(raw + body), sendall=response.write)
    handler(socket, ("127.0.0.1", 0), SimpleNamespace(server_port=8765))
    return response.getvalue().decode()


class RetrievalTests(unittest.TestCase):
    def test_reservations_are_increasing_and_thread_safe(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(reserve_run(root, None), "run-001")
            self.assertEqual(reserve_run(root, "run-999"), "run-999")
            with ThreadPoolExecutor(max_workers=8) as pool:
                allocated = list(pool.map(lambda _: reserve_run(root, ""), range(16)))
            self.assertEqual(sorted(allocated), [f"run-{n}" for n in range(1000, 1016)])
            with self.assertRaises(ValueError):
                reserve_run(root, "run-900")
            self.assertEqual(reserve_run(root, ""), "run-1016")

    def test_snapshot_filtering_and_deduplication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            query = market("KALSHI", "active")
            expired = market("KALSHI", "expired", close_time_ms=101)
            carried = market("KALSHI", "carried", last_seen_ms=99)
            conflict = market("KALSHI", "conflict", event_uid="KALSHI:active-event")
            sibling = market("KALSHI", "sibling", event_uid=conflict["event_uid"], category="Politics", status_canonical="closed")
            candidate = market("POLYUS", "unknown", catalog_pulled_ms=101, last_seen_ms=101, category=None)
            save_catalog(root, [query, expired, carried, conflict, sibling], [candidate])
            catalog = Catalog(root)
            self.assertEqual(catalog.as_of, 101)
            self.assertEqual(catalog.groups, {"KALSHI": {"KALSHI:active": True}, "POLYUS": {"POLYUS:unknown": None}})
            self.assertEqual(catalog.exclusion("KALSHI:conflict", "POLYUS:unknown"), "kalshi_category_conflict")
            self.assertEqual(catalog.categories["POLYUS"]["missing_event_count"], 1)
            title, rule = input_parts(query)
            self.assertEqual(title, "Event: Test event\nMarket: Test contract")
            self.assertEqual(rule, "Rules: Resolves Yes if the test passes.")

    def test_exact_ranking_breaks_ties_and_filters_before_top_k(self):
        candidates = [f"POLYUS:{i:02}" for i in range(15)]
        query = "KALSHI:q"
        uids = {"KALSHI": [query], "POLYUS": candidates}
        groups = {"KALSHI": {query: True}, "POLYUS": dict.fromkeys(candidates, True)}
        groups["POLYUS"][candidates[0]] = False
        groups["POLYUS"][candidates[1]] = None
        vectors = {"KALSHI": np.ones((1, 1)), "POLYUS": np.ones((15, 1))}
        rows, = rank_batches("run-001", uids, vectors, groups)
        self.assertEqual([row["polyus_market_uid"] for row in rows], candidates[1:11])
        self.assertEqual(list(rank_batches("run-001", {"KALSHI": [query], "POLYUS": []}, vectors, groups)), [])

    def test_candidate_validation_rejects_row_id_mismatch_and_accepts_empty_file(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "pairs.parquet"
            pq.write_table(pa.Table.from_pylist([], schema=CANDIDATES), path)
            validate_candidates(path, "run-001", 0)
            rows = [{"run_id": "run-002", "kalshi_market_uid": "KALSHI:q", "polyus_market_uid": "POLYUS:p",
                     "similarity_score": 1}]
            pq.write_table(pa.Table.from_pylist(rows, schema=CANDIDATES), path)
            with self.assertRaisesRegex(ValueError, "row run ID"):
                validate_candidates(path, "run-001", 1)

    @unittest.skipUnless(os.getenv("EMBEDDINGS_MODEL_SMOKE") == "1", "requires cached EmbeddingGemma")
    def test_cached_model_browser_and_frozen_benchmark(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, runs, db = root / "source", root / "runs", root / "reviews.sqlite"
            tail = "The closing clause survives the head-tail comparison."
            query = market("KALSHI", "q", rules_primary="word " * 2200 + tail,
                           market_url="https://kalshi.com/markets/q")
            polyus = [market("POLYUS", f"{i:02}", market_url="https://polymarket.us/event/test") for i in range(12)]
            polyus.append(market("POLYUS", "filtered", category="Politics"))
            save_catalog(source, [query], polyus)
            path = create_run(runs, source, None, "cpu", 2, "head")
            run = Run(runs, path.name)
            comparison_path = create_run(runs, run.catalog.directory, None, "cpu", 2, "head-tail")
            comparison = Run(runs, comparison_path.name)
            self.assertGreater(run.metadata["truncation"]["original_token_counts"]["KALSHI"]["KALSHI:q"], 2048)
            self.assertEqual(run.metadata["truncation"]["original_token_counts"],
                             comparison.metadata["truncation"]["original_token_counts"])
            self.assertEqual(len(run.results({"KALSHI:q"})), 10)
            handler = review_handler(ReviewApp(run, db))
            page = browser_request(handler, url(kalshi="q", polyus="11"))
            self.assertIn("200 OK", page)
            csrf = re.search(r'name="csrf" value="([^"]+)"', page)[1]

            def submit(action, **params):
                data = {"action": action, "csrf": csrf, "kalshi": "KALSHI:q", **params}
                response = browser_request(handler, "/", data)
                self.assertIn("303 See Other", response)
                location = re.search(r"Location: ([^\r\n]+)", response)[1]
                return browser_request(handler, location)

            self.assertIn("outside top 10", page)
            self.assertIn("Kalshi series page", page)
            self.assertIn("Polymarket US event page", page)
            self.assertIn(tail, page)
            self.assertIn("first-review", submit("create", name="first-review"))
            for contract in ("00", "11", "filtered"):
                page = submit("decision", benchmark="first-review", polyus=contract, manual=contract, label="match")
                self.assertIn("Add to benchmark", page)
                submit("add_pair", benchmark="first-review", polyus=contract, manual=contract,
                       independent="1" if contract != "00" else "")
            submit("freeze", benchmark="first-review")
            submit("decision", benchmark="first-review", polyus="11", label="no_match")
            report = evaluate(run, db, "first-review", comparison)
            self.assertEqual(report["run"]["eligible_pairs"], 2)
            self.assertEqual(report["run"]["pair_recall_at_10"], 0.5)
            self.assertEqual(report["run"]["query_hit_rate_at_10"], 1)
            self.assertEqual(report["run"]["independently_found_pairs"], 2)
            self.assertEqual(report["run"]["exclusions"], {"sports_mismatch": 1})
            self.assertEqual(report["run"]["truncation_affected"]["eligible_pairs"], 2)
            self.assertEqual(report["change"]["pair_recall_at_10"], 0)
            review = reviews.decisions(db, "KALSHI:q")["POLYUS:11"]
            self.assertEqual(reviews.review_state(review, query, polyus[11]), "no_match")
            updated = {**polyus[11], "description": "Updated settlement rule."}
            self.assertEqual(reviews.review_state(review, query, updated), "stale")


if __name__ == "__main__":
    unittest.main()
