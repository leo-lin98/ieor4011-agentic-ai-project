"""Recall against manually curated, frozen contract pairs."""

from collections import Counter

from embeddings.catalog import rule_fingerprint

from .reviews import read_benchmark


def measure(run, pairs, affected):
    kalshi_ids = {pair["kalshi_market_uid"] for pair in pairs}
    polyus_ids = {pair["polyus_market_uid"] for pair in pairs}
    fingerprints = {}
    for platform, uids in (("KALSHI", kalshi_ids), ("POLYUS", polyus_ids)):
        fingerprints.update({
            uid: rule_fingerprint(market) for uid, market in run.catalog.full_rows(platform, uids).items()
        })
    retrieved = {
        (row["kalshi_market_uid"], row["polyus_market_uid"]) for row in run.results(kalshi_ids)
    }
    eligible = set()
    exclusions = Counter()
    eligible_affected = set()
    for pair in pairs:
        kalshi, polyus = pair["kalshi_market_uid"], pair["polyus_market_uid"]
        if reason := run.catalog.exclusion(kalshi, polyus):
            exclusions[reason] += 1
            continue
        if (fingerprints[kalshi], fingerprints[polyus]) != (pair["kalshi_fingerprint"], pair["polyus_fingerprint"]):
            exclusions["rules_changed"] += 1
            continue
        eligible.add((kalshi, polyus))
        if kalshi in affected["KALSHI"] or polyus in affected["POLYUS"]:
            eligible_affected.add((kalshi, polyus))

    def scores(expected):
        queries = {k for k, _ in expected}
        found = expected & retrieved
        found_queries = {k for k, _ in found}
        return {
            "eligible_pairs": len(expected), "eligible_queries": len(queries),
            "retrieved_pairs": len(found), "queries_with_hit": len(found_queries),
            "pair_recall_at_10": len(found) / len(expected) if expected else None,
            "query_hit_rate_at_10": len(found_queries) / len(queries) if queries else None,
        }

    return {
        "run_id": run.id, "total_pairs": len(pairs), "total_queries": len(kalshi_ids),
        "independently_found_pairs": sum(pair["independent"] for pair in pairs),
        "exclusions": dict(exclusions), **scores(eligible),
        "truncation_affected": scores(eligible_affected),
    }


def evaluate(run, db_path, name, comparison=None):
    benchmark, pairs = read_benchmark(db_path, name)
    affected = {platform: set(uids) for platform, uids in
                run.metadata["truncation"]["original_token_counts"].items()}
    if comparison:
        if run.metadata["catalog"] != comparison.metadata["catalog"]:
            raise ValueError("Comparison runs must use the same frozen catalog")
        for platform, uids in comparison.metadata["truncation"]["original_token_counts"].items():
            affected[platform].update(uids)
    baseline = measure(run, pairs, affected)
    report = {"benchmark": benchmark, "run": baseline}
    if comparison:
        alternate = measure(comparison, pairs, affected)

        def delta(before, after):
            return {
                key: after[key] - before[key] if before[key] is not None and after[key] is not None else None
                for key in ("pair_recall_at_10", "query_hit_rate_at_10")
            }

        report["comparison"] = alternate
        report["change"] = {
            **delta(baseline, alternate),
            "truncation_affected": delta(baseline["truncation_affected"], alternate["truncation_affected"]),
        }
    return report
