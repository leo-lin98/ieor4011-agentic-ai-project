"""Review non-sports contract pairs and produce an evidence-backed human review queue."""
import argparse
import hashlib
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from . import core
from .rubrics.non_sports import for_pair

DIRECTIONS = {"identical": "same", "complementary": "opposite"}
RULE_FIELDS = {
    "kalshi": ("rules_primary", "rules_secondary", "settlement_sources", "early_close_condition"),
    "polyus": ("description", "rules_disclaimer", "event_description", "resolution_source"),
}
CONTEXT = (
    "category", "event_uid", "event_title", "market_title", "market_question", "yes_title", "no_title",
    "yes_side_label", "no_side_label", "market_url", "contract_terms_url", "contract_url",
    "close_time_ms", "expected_expiration_ms", "latest_expiration_ms", "rules_hash",
    "catalog_event_options",
)


def _market_evidence(row, side):
    return {"market_uid": row["market_uid"], **{key: row.get(key) for key in CONTEXT if row.get(key) is not None},
            "rules": {key: row[key] for key in RULE_FIELDS[side] if row.get(key)}}


def _validate_verdict(verdict, rubric, direction):
    if not isinstance(verdict, dict):
        raise ValueError("Expected an object verdict")
    checklist = verdict.get("checklist")
    if not isinstance(checklist, list) or any(not isinstance(item, dict) for item in checklist):
        raise ValueError("Expected a checklist")
    dimensions = [item.get("dimension") for item in checklist]
    if (any(not isinstance(dimension, str) for dimension in dimensions)
            or len(dimensions) != len(rubric.DIMENSIONS) or set(dimensions) != set(rubric.DIMENSIONS)):
        raise ValueError("Checklist must cover each rubric dimension exactly once")
    material = set()
    for item in checklist:
        if item.get("same") not in ("yes", "no", "unsure") or any(
            not isinstance(item.get(key), str) or not item[key].strip() for key in ("kalshi", "polyus", "note")
        ):
            raise ValueError("Checklist item lacks a valid finding or supporting evidence")
        scenario = item.get("payout_scenario")
        if not isinstance(scenario, str):
            raise ValueError("Checklist item lacks the payout scenario field")
        if item.get("scope") not in (("ordinary", "exceptional") if scenario.strip() else ("informational",)):
            raise ValueError("Checklist item scope conflicts with its payout scenario")
        if scenario.strip():
            if item["same"] == "yes":
                raise ValueError("A matching dimension cannot have a payout divergence scenario")
            material.add(item["same"])
    outcome = verdict.get("verdict")
    if not isinstance(outcome, str) or outcome not in core.STATUS:
        raise ValueError("Invalid verdict")
    ordinary = verdict.get("ordinary_mapping")
    if (not isinstance(ordinary, dict) or ordinary.get("verdict") not in tuple(core.STATUS)
            or not isinstance(ordinary.get("reasoning"), str) or not ordinary["reasoning"].strip()):
        raise ValueError("Verdict lacks an ordinary-outcome mapping and rationale")
    if (outcome == "not_identical" and "no" not in material
            or outcome == "unsure" and "unsure" not in material):
        raise ValueError("Adverse verdict lacks a supporting payout scenario")
    expected = {"identical": "yes", "not_identical": "no", "unsure": "unsure"}[outcome]
    if verdict.get("opposite_side_ok") != (expected if direction == "opposite" else "not_applicable"):
        raise ValueError("Verdict conflicts with the side mapping")
    if any(not isinstance(verdict.get(key), str) for key in ("reasoning", "human_review_question")):
        raise ValueError("Verdict lacks reasoning or a human review question field")
    confidence = verdict.get("confidence")
    if type(confidence) not in (int, float) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError("Confidence must be a number between zero and one")


def review_pair(pair, catalogs, *, model, judge=None, include_sports=False):
    """Build a review or queue entry. A missing judge produces a dry-run payload."""
    k_uid, p_uid = pair["kalshi_market_uid"], pair["polyus_market_uid"]
    if not k_uid.startswith("KALSHI:") or not p_uid.startswith("POLYUS:"):
        raise ValueError("Expected Kalshi and Polymarket US market IDs")
    relationship = pair["relationship"]
    result = {
        "pair_id": pair.get("pair_id") or f"{k_uid}__{p_uid}",
        "kalshi_market_uid": k_uid, "polyus_market_uid": p_uid,
        "relationship": relationship, "direction": DIRECTIONS.get(relationship),
    }

    def queue(reason, **extra):
        return {**result, **extra, "status": "needs_human_review", "review_reasons": [reason]}

    if relationship not in DIRECTIONS:
        return queue("Unsupported proposed relationship. Review the contract legs and payout mapping manually.")
    k, p = catalogs.get(k_uid), catalogs.get(p_uid)
    if not k or not p:
        return queue("One or both markets are missing from the current catalogs.")
    try:
        rubric = for_pair(k.get("category"), p.get("category"), include_sports=include_sports)
    except ValueError:
        return {**result, "status": "use_sports_judge"}

    payload = {
        "direction": result["direction"],
        "kalshi": _market_evidence(k, "kalshi"),
        "polyus": _market_evidence(p, "polyus"),
    }
    supplemental = pair.get("supplemental_terms", {})
    if not isinstance(supplemental, dict) or supplemental.keys() - {"kalshi", "polyus"}:
        raise ValueError("supplemental_terms must map kalshi or polyus to attributed excerpts")
    for side, excerpts in supplemental.items():
        if not isinstance(excerpts, list) or any(
            not isinstance(excerpt, dict)
            or any(not isinstance(excerpt.get(key), str) or not excerpt[key].strip()
                   for key in ("source_url", "text", "provenance"))
            for excerpt in excerpts
        ):
            raise ValueError("Each supplemental excerpt needs source_url, text, and provenance")
        if excerpts:
            payload[side]["supplemental_terms"] = excerpts
    version = f"pairs.6+{rubric.NAME}.{rubric.VERSION}"
    result.update({"rubric": rubric.NAME, "prompt_version": version, "model": model, "evidence": payload})
    result["evidence_id"] = hashlib.sha256(
        json.dumps([version, payload], sort_keys=True).encode()
    ).hexdigest()
    for side, row in (("kalshi", k), ("polyus", p)):
        proposed = pair.get(f"{side}_rules_hash")
        if proposed and proposed != row.get("rules_hash"):
            return queue(f"{side} rules changed or the current hash is unavailable. Refresh the proposal.")
    if any(not (row.get(key) or "").strip() for row, key in ((k, "rules_primary"), (p, "description"))):
        return queue("Primary settlement rules are missing for one or both venues.")

    system = core.system_prompt(rubric, None, pair_review=True)
    schema = core.schema(rubric, pair_review=True)
    if judge is None:
        return {**result, "status": "dry_run", "system": system, "schema": schema}
    try:
        verdict, _, usage = judge(model, system, payload, schema)
        _validate_verdict(verdict, rubric, result["direction"])
    except Exception as error:
        return queue("Judge call failed or returned an invalid verdict.", error_type=type(error).__name__)
    reasons = [item["dimension"] for item in verdict["checklist"] if item["payout_scenario"].strip()]
    if verdict["verdict"] != "identical":
        reasons.append(verdict["verdict"])
    if reasons and verdict["human_review_question"].strip():
        reasons.append(verdict["human_review_question"])
    return {
        **result, "verdict": verdict, "usage": usage, "review_reasons": reasons,
        "ordinary_mapping": verdict["ordinary_mapping"],
        "status": "needs_human_review" if reasons else "no_difference_found",
    }


def review_questions(reviews):
    """Group shared sports exception questions while retaining each pair's scenario."""
    questions = {}
    for review in reviews:
        if review["status"] != "needs_human_review":
            continue
        pair_id = review["pair_id"]
        verdict = review.get("verdict", {})
        findings = [item for item in verdict.get("checklist", []) if item["payout_scenario"].strip()]
        if not findings:
            questions[(pair_id, None)] = {"affected_pairs": [pair_id], "reasons": review["review_reasons"]}
            continue
        for item in findings:
            dimension = item["dimension"]
            key = (pair_id, dimension)
            if review["rubric"] == "sports_pair" and dimension == "exceptional_outcomes" and item["scope"] == "exceptional":
                k = review["evidence"]["kalshi"]
                p = review["evidence"]["polyus"]
                secondary = k["rules"].get("rules_secondary")
                if k.get("event_uid") and secondary and p["rules"].get("description"):
                    key = ("sports_exception", k["event_uid"], p["market_uid"],
                           secondary, p["rules"]["description"])
            question = questions.setdefault(key, {
                "dimension": dimension, "scope": item["scope"], "affected_pairs": [], "findings": [],
            })
            if pair_id not in question["affected_pairs"]:
                question["affected_pairs"].append(pair_id)
            question["findings"].append({"pair_id": pair_id, **item})
    return list(questions.values())


def _load_catalogs(pairs, catalog_dir=None):
    import pyarrow.parquet as pq

    from collector.store import catalog_current_path

    markets = {}
    for platform, field in (("KALSHI", "kalshi_market_uid"), ("POLYUS", "polyus_market_uid")):
        ids = sorted({pair[field] for pair in pairs})
        path = catalog_current_path(platform) if catalog_dir is None else catalog_dir / f"{platform}.parquet"
        if ids and path.exists():
            rows = pq.read_table(path, filters=[("market_uid", "in", ids)]).to_pylist()
            option_rows = [
                row for row in rows
                if platform == "POLYUS" and row.get("event_uid")
                and "displayed options" in (row.get("description") or "").lower()
            ]
            if option_rows:
                events = sorted({row["event_uid"] for row in option_rows})
                options_by_event = {}
                options = pq.read_table(path, columns=["event_uid", "market_uid", "market_title"],
                                        filters=[("event_uid", "in", events)])
                for option in options.to_pylist():
                    if option["market_title"]:
                        options_by_event.setdefault(option["event_uid"], []).append({
                            "market_uid": option["market_uid"], "market_title": option["market_title"],
                        })
                for row in option_rows:
                    row["catalog_event_options"] = sorted(
                        options_by_event.get(row["event_uid"], []), key=lambda option: option["market_uid"]
                    )
            markets.update({row["market_uid"]: row for row in rows})
    return markets


def main(argv=None):
    parser = argparse.ArgumentParser(prog="judge pairs", description=__doc__)
    parser.add_argument("--input", type=Path, help="JSON array of pair rows. Defaults to proposer rows in T2.")
    parser.add_argument("--catalog-dir", type=Path,
                        help="Directory with KALSHI.parquet and POLYUS.parquet for a frozen review.")
    parser.add_argument("--model", default=core.MODEL)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-sports", action="store_true",
                        help="Review sports pairs with contract-level payout scenarios")
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    try:
        if args.input:
            pairs = json.loads(args.input.read_text())
        else:
            from collector.flags import load_flags
            pairs = [row for row in load_flags() if row.get("source") == "proposer"]
        if not isinstance(pairs, list):
            raise ValueError("Input must be a JSON array of pair rows")
        pairs = pairs[:args.limit]
        catalogs = _load_catalogs(pairs, args.catalog_dir)
        client = None

        def judge(model, system, payload, schema):
            nonlocal client
            if client is None:
                client = core.openai_client()
            return core.call_model(client, model, system, payload, schema)

        reviews = [review_pair(pair, catalogs, model=args.model, judge=None if args.dry_run else judge,
                               include_sports=args.include_sports)
                   for pair in pairs]
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.error(str(error))
    print(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "human_review_queue": [r for r in reviews if r["status"] == "needs_human_review"],
        "human_review_questions": review_questions(reviews),
        "other_results": [r for r in reviews if r["status"] != "needs_human_review"],
    }, indent=2))
