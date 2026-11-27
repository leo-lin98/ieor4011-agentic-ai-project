"""Fetch and tier contract pairs, or score a report against human labels."""
import argparse
import csv
import hashlib
import json
from collections import Counter
from pathlib import Path
from urllib.parse import quote

import httpx

from collector.config import DATA, KALSHI_BASE, POLYUS_BASE

from . import core, tier_rules
from .pairs import DIRECTIONS, _load_catalogs, _market_evidence

GAMMA = "https://gamma-api.polymarket.com"
SIDES = ("kalshi", "polymarket")


def _documents(fields):
    documents = {}
    for key, value in fields.items():
        if isinstance(value, str) and value.strip():
            documents[key] = value
        elif isinstance(value, (list, dict)) and value:
            documents[key] = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return documents


def kalshi_market(event_response, ticker, source_url):
    event = event_response["event"]
    if not source_url.startswith(KALSHI_BASE + "/") and not source_url.startswith(
        "https://api.elections.kalshi.com/trade-api/v2/"
    ):
        raise ValueError("Expected an official Kalshi event endpoint")
    market = next((row for row in event_response.get("markets", event.get("markets", []))
                   if row["ticker"] == ticker), None)
    if market is None:
        raise ValueError(f"{ticker} was not found in the Kalshi event")
    return {
        "venue": "kalshi", "market_id": ticker, "side": "Yes", "source_url": source_url,
        "market_title": market.get("title"), "trading_end": market.get("close_time"),
        "documents": _documents({
            "rules_primary": market.get("rules_primary"), "rules_secondary": market.get("rules_secondary"),
            "settlement_sources": market.get("settlement_sources") or event.get("settlement_sources"),
        }),
    }


def gamma_market(event, slug, side, source_url):
    market = next((row for row in event.get("markets", []) if row["slug"] == slug), None)
    if market is None:
        raise ValueError(f"{slug} was not found in the Polymarket international event")
    outcomes = market.get("outcomes")
    if isinstance(outcomes, str):
        outcomes = json.loads(outcomes)
    if not isinstance(outcomes, list) or side not in outcomes or len(outcomes) != 2:
        raise ValueError(f"{side!r} is not a side of a two-outcome market at {slug}")
    return {
        "venue": "polymarket_international", "market_id": slug, "side": side, "source_url": source_url,
        "market_title": market.get("question"), "trading_end": market.get("endDate"),
        "documents": _documents({
            "description": market.get("description"),
            "resolutionSource": market.get("resolutionSource") or event.get("resolutionSource"),
        }),
    }


def fetch_pair(pair, get):
    """Fetch exact selected contracts via public, read-only API endpoints."""
    ticker = pair["kalshi_ticker"]
    event = pair["kalshi_event_ticker"]
    url = f"{KALSHI_BASE}/events/{quote(event, safe='')}"
    left = kalshi_market(get(url), ticker, url)
    slug = pair["polymarket_market_slug"]
    side = pair.get("polymarket_side", "Yes")
    venue = pair["polymarket_venue"]
    if venue == "polymarket_international":
        event_slug = quote(pair["polymarket_event_slug"], safe="")
        url = f"{GAMMA}/events/slug/{event_slug}"
        return left, gamma_market(get(url), slug, side, url)
    if venue != "polymarket_us" or side not in ("Yes", "No"):
        raise ValueError("Select an explicit Polymarket venue and supported contract side")
    url = f"{POLYUS_BASE}/v1/market/slug/{quote(slug, safe='')}"
    response = get(url)
    market = response.get("market", response)
    if market.get("slug") != slug:
        raise ValueError(f"Polymarket US response did not match {slug}")
    return left, {
        "venue": venue, "market_id": slug, "side": side, "source_url": url,
        "market_title": market.get("title"), "trading_end": market.get("endDate"),
        "documents": _documents({
            "description": market.get("description"),
            "resolutionSource": market.get("resolutionSource"),
        }),
    }


def catalog_pair(pair, catalogs):
    result = []
    for side, uid_key, venue in (
        ("kalshi", "kalshi_market_uid", "kalshi"), ("polyus", "polyus_market_uid", "polymarket_us"),
    ):
        uid = pair[uid_key]
        if not uid.startswith("KALSHI:" if side == "kalshi" else "POLYUS:"):
            raise ValueError(f"Unexpected catalog venue for {uid}")
        row = catalogs.get(uid)
        if row is None:
            raise ValueError(f"Missing catalog market {uid}")
        expected_hash = pair.get("kalshi_rules_hash" if side == "kalshi" else "polyus_rules_hash")
        if expected_hash and expected_hash != row.get("rules_hash"):
            raise ValueError(f"Rules changed since proposal for {uid}")
        evidence = _market_evidence(row, side)
        result.append({
            "venue": venue, "market_id": uid, "side": "Yes", "source_url": row.get("market_url"),
            "market_title": row.get("market_title"), "trading_end": row.get("close_time_ms"),
            "documents": _documents(evidence["rules"]),
        })
    return tuple(result)


def _market_inputs(pair, catalogs, get):
    if "markets" in pair:
        left, right = (pair["markets"][side] for side in SIDES)
    elif "kalshi_market_uid" in pair:
        left, right = catalog_pair(pair, catalogs)
    else:
        left, right = fetch_pair(pair, get)
    if left["venue"] != "kalshi" or right["venue"] not in ("polymarket_us", "polymarket_international"):
        raise ValueError("Expected a Kalshi contract and an explicitly identified Polymarket venue")
    for market in (left, right):
        source = market["documents"]
        primary = "rules_primary" if market["venue"] == "kalshi" else "description"
        if not isinstance(source, dict) or not isinstance(source.get(primary), str) or not source[primary].strip():
            raise ValueError(f"Missing primary settlement rules for {market['market_id']}")
    return left, right


def _pairs(path):
    rows = json.loads(path.read_text())
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Expected a JSON array of pairs")
    ids = [str(row["pair_id"]).strip() for row in rows]
    if not all(ids) or len(ids) != len(set(ids)):
        raise ValueError("Pair IDs must be nonempty and unique")
    return [{**row, "pair_id": pair_id} for row, pair_id in zip(rows, ids)]


def _write(path, result):
    if path is None:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write a complete report before publishing it.
    from tempfile import NamedTemporaryFile

    with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            json.dump(result, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    tmp.replace(path)


def run(args):
    rows = _pairs(args.pairs)[:args.limit]
    if args.cache:
        args.cache.parent.mkdir(parents=True, exist_ok=True)
    catalogs = _load_catalogs([row for row in rows if "kalshi_market_uid" in row and "markets" not in row],
                              args.catalog_dir)
    client = None

    def call(model, system, payload, schema):
        nonlocal client
        if client is None:
            client = core.openai_client()
        from openai import AuthenticationError, OpenAIError

        try:
            return core.call_model(client, model, system, payload, schema)[0]
        except AuthenticationError as error:
            raise SystemExit("OpenAI rejected the configured API credential.") from error
        except OpenAIError as error:
            raise ValueError(f"Model call failed ({type(error).__name__})") from error

    results = []
    with httpx.Client(timeout=20) as http:
        responses = {}

        def get(url):
            if url not in responses:
                response = http.get(url)
                response.raise_for_status()
                responses[url] = response.json()
            return responses[url]

        for pair in rows:
            header = {"pair_id": pair["pair_id"], "category": pair.get("category")}
            try:
                direction = DIRECTIONS[pair.get("relationship", "identical")]
                left, right = _market_inputs(pair, catalogs, get)
                frozen = {"kalshi": left, "polymarket": right}
                header.update({
                    "relationship": pair.get("relationship", "identical"), "markets": frozen,
                    "evidence_id": hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest(),
                })
                if args.fetch_only:
                    result = {**header, "status": "fetched"}
                else:
                    result = {**header, **tier_rules.judge_pair(
                        left, right, direction=direction, model=args.model, cache=args.cache,
                        call=call, code_only=args.code_only,
                    )}
            except (ValueError, KeyError, TypeError, httpx.HTTPError) as error:
                result = {**header, "status": "error", "tier": None, "needs_human": True, "error": str(error)}
            results.append(result)
    _write(args.output, results)


def score_rows(labels, predictions, notes=None):
    """Score reviewer tiers only; report unscored positives separately."""
    ids = [str(row["pair_id"]) for row in predictions]
    if len(ids) != len(set(ids)):
        raise ValueError("Predictions contain duplicate pair IDs")
    by_id = dict(zip(ids, predictions))
    matrix = {actual: dict.fromkeys(tier_rules.TIERS, 0) for actual in tier_rules.TIERS}
    missed = []
    unscored = []
    scored = 0
    positives = 0
    true_positives = 0
    positive_labels = {"Material", "Blocking"}
    unscored_positive = 0
    for pair_id, label in labels.items():
        row = by_id.get(pair_id, {})
        predicted = row.get("tier")
        if predicted not in tier_rules.TIERS or row.get("status") == "incomplete":
            unscored.append(pair_id)
            unscored_positive += label in positive_labels
            continue
        matrix[label][predicted] += 1
        scored += 1
        if label in positive_labels:
            positives += 1
            true_positives += predicted in positive_labels
            if predicted not in positive_labels:
                missed.append({
                    "pair_id": pair_id, "human_tier": label, "predicted_tier": predicted,
                    "checks_triggered": [finding["check"] for finding in row.get("findings", [])],
                    "reviewer_note": (notes or {}).get(pair_id),
                })
    return {
        "status": "no_human_labels" if not labels else "scored" if scored else "no_scored_predictions",
        "human_labeled": len(labels), "scored": scored, "unscored_pair_ids": unscored,
        "unscored_material_or_blocking": unscored_positive, "confusion_matrix": matrix,
        "material_or_blocking_recall": (
            true_positives / (positives + unscored_positive) if positives + unscored_positive else None
        ),
        "material_or_blocking_caught": true_positives, "material_or_blocking_scored": positives,
        "misses": missed,
    }


def read_labels(path):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        header = stream.readline()
        stream.seek(0)
        rows = csv.DictReader(stream, delimiter="\t" if "\t" in header else ",")
        if "Your tier" not in (rows.fieldnames or []) or not {"#", "pair_id"}.intersection(rows.fieldnames or []):
            raise ValueError("Labels need a # or pair_id column and a Your tier column")
        labels, notes = {}, {}
        for row in rows:
            label = (row["Your tier"] or "").strip().title()
            if not label:
                continue
            pair_id = (row.get("pair_id") or row.get("#") or "").strip()
            if label not in tier_rules.TIERS or not pair_id or pair_id in labels:
                raise ValueError(f"Invalid or duplicate human label for pair {pair_id!r}")
            labels[pair_id] = label
            notes[pair_id] = (row.get("Your notes") or "").strip() or None
        return labels, notes


def main(argv=None):
    parser = argparse.ArgumentParser(prog="judge tier", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser("run")
    review.add_argument("--pairs", type=Path, required=True)
    review.add_argument("--catalog-dir", type=Path)
    review.add_argument("--output", type=Path)
    review.add_argument("--model", default=core.MODEL)
    review.add_argument("--cache", type=Path, default=DATA / "judge" / "tier-extractions.sqlite3")
    review.add_argument("--limit", type=int)
    modes = review.add_mutually_exclusive_group()
    modes.add_argument("--fetch-only", action="store_true")
    modes.add_argument("--code-only", action="store_true", help="Extract fields and report the code-only tier")
    evaluator = commands.add_parser("score")
    evaluator.add_argument("--labels", type=Path, required=True)
    evaluator.add_argument("--predictions", type=Path, required=True)
    evaluator.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "score":
            labels, notes = read_labels(args.labels)
            report = score_rows(labels, _pairs(args.predictions), notes)
            _write(args.output, report)
        else:
            if args.limit is not None and args.limit < 1:
                raise ValueError("--limit must be positive")
            run(args)
    except (OSError, TypeError, KeyError, ValueError) as error:
        parser.error(str(error))
