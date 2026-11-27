import fcntl
import io
import os
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import unquote, urlencode

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from collector.schemas import MARKET_CATALOG
from embeddings.catalog import Catalog, input_parts
from embeddings.run import CANDIDATES, Run, create_run, rank_batches, reserve_run, resume_run, validate_candidates
from evaluation.retrieval import reviews
from evaluation.retrieval.browser import ReviewApp, market_card, review_handler, url
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
    def test_catalog_search_opens_and_reviews_unranked_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, db = root / "catalog", root / "reviews.sqlite"
            kalshi = market("KALSHI", "q")
            polyus = [
                market("POLYUS", "ranked", market_title="Ranked solar election"),
                market("POLYUS", "outside", event_title="Solar election", market_title="Found by search"),
                market("POLYUS", "filtered", category="Politics", yes_title="Solar election", market_title="Filtered result"),
                market("POLYUS", "inactive", status_canonical="closed", event_title="Solar election", market_title="Closed result"),
                market("POLYUS", "excluded", market_title="Unrelated"),
            ]
            save_catalog(source, [kalshi], polyus)
            run = SimpleNamespace(
                id="run-001", catalog=Catalog(source),
                results=lambda uids: [{"polyus_market_uid": "POLYUS:ranked", "similarity_score": 1.0}],
                metadata={"created_at": "2026-11-19T00:00:00+00:00",
                          "catalog": {"platforms": {"POLYUS": {"sha256": "snapshot"}}}},
            )
            app = ReviewApp(run, db)
            handler = review_handler(app)
            with patch("evaluation.retrieval.browser.SEARCH_PAGE_SIZE", 2):
                first = browser_request(handler, url(kalshi="q", keywords="Solar ELECTION"))
                panel = first.split('id="catalog-search"', 1)[1].split("</details>", 1)[0]
                self.assertIn("Showing 1–2 of 3 contracts", panel)
                self.assertNotIn("Ranked solar election", panel)
                self.assertNotIn("Unrelated", panel)
                self.assertIn("Filtered by sports category", panel)
                self.assertIn("was inactive", panel)
                more = unescape(re.search(r'<a href="([^"]+)">More results</a>', panel)[1])
                second = browser_request(handler, more.partition("#")[0])
                self.assertIn("Showing 3–3 of 3 contracts", second)
                self.assertIn("outside top 10", second)
                target = unescape(re.search(r'<a href="([^"]+)">Found by search</a>', second)[1])
                path, _, fragment = target.partition("#")
                page = browser_request(handler, path)
                self.assertEqual(unquote(fragment), "contract-POLYUS:outside")
                self.assertIn('id="contract-POLYUS:outside"', page)
                self.assertIn("Manual lookup · outside top 10", page)
                self.assertIn('value="Solar ELECTION"', page)
                submitted = browser_request(handler, "/", {
                    "action": "decision", "csrf": app.csrf, "kalshi": "q", "polyus": "outside",
                    "manual": "outside", "label": "match", "keywords": "Solar ELECTION", "search_page": "2",
                })
                self.assertIn("303 See Other", submitted)
                self.assertIn("keywords=Solar+ELECTION&search_page=2#review-", submitted)
                self.assertEqual(reviews.decisions(db, "KALSHI:q")["POLYUS:outside"]["label"], "match")

    def test_market_cards_collapse_rules_ids_and_links_by_default(self):
        for platform, rule_field in (("KALSHI", "rules_primary"), ("POLYUS", "description")):
            with self.subTest(platform=platform):
                card = market_card(market(
                    platform, "contract-01", yes_title="The stated outcome",
                    market_url="https://example.com/market", **{rule_field: "Complete settlement rule"},
                ))
                summary, disclosure = card.split('<details class="market-details"><summary>More details</summary>')
                for value in ("Test contract", "Test event", "The stated outcome"):
                    self.assertIn(value, summary)
                for value in ("contract-01", "Complete settlement rule", "https://example.com/market"):
                    self.assertNotIn(value, summary)
                    self.assertIn(value, disclosure)

    def test_review_queue_shuffles_and_skips_completed_contracts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, db = root / "catalog", root / "reviews.sqlite"
            kalshi = [market("KALSHI", contract) for contract in "abcde"]
            polyus = market("POLYUS", "p")
            save_catalog(source, kalshi, [polyus])
            run = SimpleNamespace(
                id="run-001", catalog=Catalog(source), results=lambda uids: [],
                metadata={"created_at": "2026-11-19T00:00:00+00:00",
                          "catalog": {"platforms": {"POLYUS": {"sha256": "snapshot"}}}},
            )
            app = ReviewApp(run, db)
            first, second, third, fourth, fifth = app.queries
            self.assertNotEqual(app.queries, sorted(app.queries))
            self.assertEqual(app.queries, ReviewApp(run, db).queries)

            def choose(action, uid, **data):
                return app.submit({"csrf": app.csrf, "action": action, "kalshi": uid, **data})

            choose("review_complete", second, complete="1")
            choose("market_absence", third, absent="1")
            choose("decision", fourth, polyus="p", label="no_match")
            page = app.render({"kalshi": first})
            self.assertIn("3 remaining", page)
            self.assertIn(f'href="{url(kalshi=fourth)}">Next</a>', page)
            self.assertIn("Review complete for this run.", app.render({"kalshi": second}))

            choose("review_complete", first, complete="1")
            self.assertIn(f'name="kalshi" value="{fourth}"', app.render({}))
            choose("review_complete", second, complete="0")
            self.assertIn(f'name="kalshi" value="{second}"', app.render({}))
            choose("market_absence", third, absent="0")
            self.assertIn(f'href="{url(kalshi=third)}">Next</a>', app.render({"kalshi": second}))
            self.assertIn(f'href="{url(kalshi=fifth)}">Next</a>', app.render({"kalshi": fourth}))

            alternate = SimpleNamespace(**{**vars(run), "metadata": {**run.metadata, "created_at": "another run"}})
            self.assertIn("5 remaining", ReviewApp(alternate, db).render({}))

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

    def test_resume_reuses_embeddings_rank_blocks_and_frozen_catalog(self):
        class TestEmbedder:
            dimension = 2
            limit = 8

            def __init__(self, device, batch_size, truncation, model):
                self.device = device or "cpu"
                self.model = model

            def settings(self):
                return {"device": self.device, "id": self.model}

            def encode(self, platform, markets, uids):
                if state["interrupt_encoding"] and len(calls) == 1:
                    raise KeyboardInterrupt
                calls.append((platform, tuple(uids)))
                return np.tile([1, 0], (len(uids), 1)), {uid: 9 for uid in uids}

        def interrupted_ranking(*args, **kwargs):
            rows = rank_batches(*args, **kwargs)
            yield next(rows)
            raise KeyboardInterrupt

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source, runs = root / "source", root / "runs"
            kalshi = [market("KALSHI", contract) for contract in "abc"]
            polyus = [market("POLYUS", "match"), market("POLYUS", "other")]
            save_catalog(source, kalshi, polyus)
            calls = []
            state = {"interrupt_encoding": True}
            with (patch("embeddings.run.Embedder", TestEmbedder),
                  patch("embeddings.run.EMBED_CHUNK", 2),
                  patch("embeddings.run.RANK_CHUNK", 1)):
                with self.assertRaises(KeyboardInterrupt):
                    create_run(runs, source, None, None, 2, "head", model="nomic")
                work = runs / ".run-001.checkpoint"
                self.assertTrue(work.is_dir())
                self.assertFalse((runs / "run-001").exists())
                with (work / ".lock").open("a") as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    with self.assertRaisesRegex(ValueError, "already running"):
                        resume_run(runs, "run-001")

                # Change the live catalog in place while the run is paused.
                save_catalog(source, [market("KALSHI", "new")], polyus)
                state["interrupt_encoding"] = False
                with patch("embeddings.run.rank_batches", interrupted_ranking):
                    with self.assertRaises(KeyboardInterrupt):
                        resume_run(runs, "run-001")
                expected_calls = [("KALSHI", ("KALSHI:a", "KALSHI:b")), ("KALSHI", ("KALSHI:c",)),
                                  ("POLYUS", ("POLYUS:match", "POLYUS:other"))]
                self.assertEqual(calls, expected_calls)
                with patch("embeddings.run.rank_batches", wraps=rank_batches) as rank:
                    path = resume_run(runs, "run-001")
                    self.assertEqual(rank.call_args.kwargs["skip"], 1)
                self.assertEqual(calls, expected_calls)
                self.assertFalse(work.exists())
                run = Run(runs, path.name)
                self.assertEqual(run.metadata["model"]["id"], "nomic")
                self.assertEqual(run.metadata["candidate_rows"], 6)
                self.assertEqual(run.metadata["truncation"]["market_counts"], {"KALSHI": 3, "POLYUS": 2})
                self.assertNotIn("KALSHI:new", run.catalog.markets["KALSHI"])

                comparison_path = create_run(runs, run.catalog.directory, None, None, 2, "head")
                comparison = Run(runs, comparison_path.name)
                self.assertEqual(run.metadata["catalog"], comparison.metadata["catalog"])
                for platform in ("KALSHI", "POLYUS"):
                    self.assertTrue(run.catalog.path(platform).samefile(comparison.catalog.path(platform)))
                self.assertEqual(len(list((runs / ".catalogs").glob("*.parquet"))), 2)
                uids = set(run.catalog.groups["KALSHI"])
                self.assertEqual(
                    run.results(uids), [dict(row, run_id=run.id) for row in comparison.results(uids)]
                )

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
                path, _, anchor = location.partition("#")
                page = browser_request(handler, path)
                if action in ("decision", "add_pair") and params.get("manual"):
                    self.assertEqual(unquote(anchor), f'review-POLYUS:{params["polyus"]}')
                    self.assertIn(f'id="{unquote(anchor)}"', page)
                return page

            self.assertIn("outside top 10", page)
            self.assertIn("Kalshi series page", page)
            self.assertIn("Polymarket US event page", page)
            self.assertIn(tail, page)
            self.assertIn("No corresponding Polymarket US market</button>", page)
            page = submit("market_absence", absent="1")
            self.assertIn("No corresponding Polymarket US market recorded for this snapshot.", page)
            snapshot = run.metadata["catalog"]["platforms"]["POLYUS"]["sha256"]
            self.assertIsNotNone(reviews.market_absence(db, query, snapshot))
            self.assertIsNone(reviews.market_absence(db, query, "different snapshot"))
            self.assertIsNone(reviews.market_absence(db, {**query, "rules_primary": "Changed."}, snapshot))
            page = submit("decision", polyus="11", manual="11", label="no_match")
            self.assertIn("Clear no-market finding", page)
            page = submit("market_absence", absent="0")
            self.assertNotIn("Clear no-market finding", page)
            submit("market_absence", absent="1")
            self.assertIn("first-review", submit("create", name="first-review"))
            for contract in ("00", "11", "filtered"):
                page = submit("decision", benchmark="first-review", polyus=contract, manual=contract, label="match")
                self.assertNotIn("Clear no-market finding", page)
                self.assertIn("Add to benchmark", page)
                submit("add_pair", benchmark="first-review", polyus=contract, manual=contract,
                       independent="1" if contract != "00" else "")
            response = browser_request(handler, "/", {
                "action": "market_absence", "csrf": csrf, "kalshi": "q", "absent": "1",
            })
            self.assertIn("400 Bad Request", response)
            self.assertIn("A matching Polymarket US pair is already saved", response)
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
