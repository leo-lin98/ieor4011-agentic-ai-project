"""T7 -> T2: turn auto_confirmed candidate pairs into active match_flags rows (DECISIONS D3 T2, D11).

    python -m candidates.flag                       # every candidate set under data/candidate_pairs
    python -m candidates.flag sports/football/nfl sports/basketball/nba
    python -m candidates.flag --dry-run

Idempotent. Rows written by this adapter carry source = "candidates:<set>". On each run:
  - every auto_confirmed pair in the chosen sets is upserted as an ACTIVE flag (relationship: identical for
    direction=same, complementary for direction=opposite; both combos spelled out in proposer_explanation);
  - adapter flags whose pair is no longer auto_confirmed in the current output (game settled, left the close
    window, template rejected on re-judge) are set INACTIVE;
  - flags from other sources (manual, future lanes) are never touched.
Active flags are what the collector's book_tick pulls T4 depth for.
"""
import argparse
import json

import pandas as pd

from arb.legs import combos_for_row
from collector import flags as F
from collector.config import DATA, ROOT
from collector.util import now_ms

SRC = "candidates:"


def _judge_summary(source, template_pair_id):
    path = ROOT / "candidates" / source / "template_approvals.json"
    if not path.exists():
        return None
    v = (json.loads(path.read_text()) or {}).get(template_pair_id) or {}
    if not v:
        return None
    return f"{v.get('verdict', v.get('status'))} conf={v.get('confidence')} by {v.get('reviewed_by')} ({v.get('prompt_version')})"


def run(sources=None, write=True):
    root = DATA / "candidate_pairs"
    paths = sorted(root.glob("**/candidate_pairs.parquet"))
    if sources:
        paths = [p for p in paths if str(p.parent.relative_to(root)) in set(sources)]
    now = now_ms()
    wanted = {}
    for path in paths:
        source = str(path.parent.relative_to(root))
        df = pd.read_parquet(path)
        for r in df[df.status == "auto_confirmed"].itertuples():
            legs = combos_for_row(r)   # raises if the direction does not yield two riskless combos
            wanted[r.pair_id] = {
                "pair_id": r.pair_id, "kalshi_market_uid": r.kalshi_market_uid, "polyus_market_uid": r.polyus_market_uid,
                "market_identical": True, "relationship": "identical" if r.direction == "same" else "complementary",
                "proposer_explanation": " | ".join(c.describe() for c in legs),
                "judge_verdict": _judge_summary(source, r.template_pair_id),
                "numeric_checks_passed": True,                 # matched on league, game, teams, bet type, line
                "rules_review_status": "template_approved", "human_review_status": None,
                "kalshi_rules_hash": r.kalshi_rules_hash, "polyus_rules_hash": r.polyus_rules_hash,
                "flagged_ts": now, "updated_ts": now, "is_active": True,
                "source": SRC + source,
                "note": f"{r.league} {r.map_key} direction={r.direction} key={r.match_key} template={r.template_pair_id}",
            }
    existing = F.load_flags()
    by_id = {f["pair_id"]: f for f in existing}
    added = updated = deactivated = unchanged = 0
    for pid, row in wanted.items():
        prev = by_id.get(pid)
        if prev is None:
            by_id[pid] = row
            added += 1
        elif not prev.get("is_active") or prev.get("kalshi_rules_hash") != row["kalshi_rules_hash"] \
                or prev.get("polyus_rules_hash") != row["polyus_rules_hash"] or prev.get("judge_verdict") != row["judge_verdict"]:
            row["flagged_ts"] = prev.get("flagged_ts") or now
            by_id[pid] = row
            updated += 1
        else:
            unchanged += 1
    for pid, f in by_id.items():
        if str(f.get("source") or "").startswith(SRC) and pid not in wanted and f.get("is_active"):
            if sources and f["source"][len(SRC):] not in set(sources):
                continue   # a set we did not refresh this run: leave it
            f["is_active"] = False
            f["updated_ts"] = now
            f["note"] = (f.get("note") or "") + " | deactivated: no longer auto_confirmed"
            deactivated += 1
    rows = list(by_id.values())
    if write:
        F.save_flags(rows)
    active = sum(1 for f in rows if f["is_active"])
    return dict(sets=[str(p.parent.relative_to(root)) for p in paths], wanted=len(wanted), added=added, updated=updated,
                unchanged=unchanged, deactivated=deactivated, active_total=active, total=len(rows),
                markets_to_stream=len({u for f in rows if f["is_active"] for u in (f["kalshi_market_uid"], f["polyus_market_uid"])}))


def main():
    ap = argparse.ArgumentParser(prog="candidates.flag")
    ap.add_argument("sources", nargs="*", help="candidate sets, e.g. sports/football/nfl (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    r = run(a.sources or None, write=not a.dry_run)
    print(("DRY RUN  " if a.dry_run else "") + json.dumps(r, indent=1))


if __name__ == "__main__":
    main()
