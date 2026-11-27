"""CLI for the template-pair judge. See package docstring.

    python -m judge sports football/nfl                       # judge unreviewed template pairs
    python -m judge sports football/nfl --all                 # re-judge all (after a prompt / rubric / model change)
    python -m judge sports football/nfl --dry-run             # print the prompts, call nothing
    python -m judge sports football/nfl --show                # verdicts on file
    python -m judge sports football/nfl --label <id> agree|disagree --note "..." --by stefan
    python -m judge sports football/nfl --eval                # agreement between labels and verdicts, by prompt version
"""
import argparse
import importlib
import json
import sys
from datetime import datetime, timezone

from collector.config import DATA

from . import core, rubrics


def main():
    if sys.argv[1:2] == ["pairs"]:
        from .pairs import main as review_pairs

        return review_pairs(sys.argv[2:])

    import pandas as pd

    ap = argparse.ArgumentParser(prog="judge")
    ap.add_argument("category")
    ap.add_argument("subcategory", help="e.g. football/nfl, basketball/nba")
    ap.add_argument("--model", default=core.MODEL)
    ap.add_argument("--all", action="store_true", help="re-judge every template pair, not only unreviewed ones")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--dry-run", action="store_true", help="print the prompts; call nothing, write nothing")
    ap.add_argument("--show", action="store_true", help="print verdicts on file and exit")
    ap.add_argument("--label", nargs=2, metavar=("TEMPLATE_PAIR_ID", "agree|disagree"),
                    help="record a human label for the verdict on file (builds the eval set)")
    ap.add_argument("--note", default="")
    ap.add_argument("--by", default="human")
    ap.add_argument("--eval", action="store_true", help="agreement between human labels and verdicts")
    ap.add_argument("--workers", type=int, default=8, help="parallel model calls")
    a = ap.parse_args()

    rubric = rubrics.for_category(a.category, a.subcategory)
    cfg = importlib.import_module(f"candidates.{a.category}.{a.subcategory.replace('/', '.')}.config")
    out_dir = DATA / "candidate_pairs" / a.category / a.subcategory
    logs = core.log_dir(a.category, a.subcategory)
    templates = pd.read_parquet(out_dir / "template_pairs.parquet")
    # template_examples.parquet: one pair per template x direction for EVERY bet type (candidate_pairs.parquet only
    # holds the bet types enabled in the league config). Older runs lack it; fall back to candidate_pairs.
    ex_path = out_dir / "template_examples.parquet"
    pairs = pd.read_parquet(ex_path if ex_path.exists() else out_dir / "candidate_pairs.parquet")
    approvals = json.loads(cfg.APPROVALS.read_text()) if cfg.APPROVALS.exists() else {}
    pv = core.prompt_version(rubric)

    if a.show:
        for t in templates.itertuples():
            v = approvals.get(t.template_pair_id) or {}
            print(f"{t.template_pair_id}  {t.map_key:28s} n={t.n_pairs:<5d} {v.get('status', 'needs_review'):10s} "
                  f"opp_ok={v.get('opposite_side_ok')!s:5s} conf={v.get('confidence', '')!s:5s} by={v.get('reviewed_by', '')} "
                  f"{v.get('prompt_version', '')}")
            if v.get("human_review_question"):
                print(f"    human: {v['human_review_question']}")
        return

    if a.label:
        tpid, label = a.label
        if label not in ("agree", "disagree"):
            raise SystemExit("label must be agree or disagree")
        v = approvals.get(tpid)
        if not v:
            raise SystemExit(f"no verdict on file for {tpid}")
        core.append_jsonl(logs / "labels.jsonl", {
            "ts": datetime.now(timezone.utc).isoformat(), "template_pair_id": tpid, "map_key": v.get("map_key"),
            "label": label, "note": a.note, "by": a.by,
            "verdict": v.get("verdict"), "opposite_side_ok": v.get("opposite_side_ok"),
            "model": v.get("reviewed_by"), "prompt_version": v.get("prompt_version")})
        print(f"labelled {tpid}: {label} (verdict was {v.get('verdict')}, {v.get('prompt_version')})")
        return

    if a.eval:
        labels = core.read_jsonl(logs / "labels.jsonl")
        if not labels:
            print("no labels yet: python -m judge ... --label <template_pair_id> agree|disagree")
            return
        df = pd.DataFrame(labels)
        df["agree"] = df.label == "agree"
        print(df.groupby(["prompt_version", "verdict"]).agree.agg(["count", "mean"]).rename(columns={"mean": "agreement"}).to_string())
        print(f"\noverall: {df.agree.mean():.0%} agreement over {len(df)} labels")
        for r in df[~df.agree].itertuples():
            print(f"  disagree: {r.template_pair_id} {r.map_key} verdict={r.verdict} by {r.by}: {r.note}")
        return

    todo = []
    for t in templates.itertuples():
        prev = approvals.get(t.template_pair_id)
        if prev and not str(prev.get("reviewed_by", "")).startswith("llm:"):
            continue   # a person decided; the judge never overrides a human
        if prev and not a.all:
            continue
        if not (pairs.template_pair_id == t.template_pair_id).any():
            print(f"  {t.template_pair_id} {t.map_key}: no example pairs on file; re-run candidates first. Skipped.")
            continue
        todo.append(t)
    todo = todo[: a.limit] if a.limit else todo
    print(f"{len(templates)} template pairs, {len(todo)} to judge with {a.model} (rubric {rubric.NAME}, prompt {pv})")
    if not todo:
        return

    system = core.system_prompt(rubric, a.subcategory)
    vschema = core.schema(rubric)
    client = None if a.dry_run else core.openai_client()
    ex_rows = pairs[pairs.template_pair_id.isin([t.template_pair_id for t in todo])].groupby(["template_pair_id", "direction"]).head(1)
    rules = core.load_rules(set(ex_rows.kalshi_market_uid) | set(ex_rows.polyus_market_uid))

    counts = {"identical": 0, "not_identical": 0, "unsure": 0}
    payloads = {}
    for t in todo:
        rows = pairs[pairs.template_pair_id == t.template_pair_id]
        examples = {d: g.iloc[0] for d, g in rows.groupby("direction")}
        payloads[t.template_pair_id] = core.build_input(rubric, t, examples, a.category, a.subcategory, rules)
        if a.dry_run:
            print(f"\n===== {t.template_pair_id} {t.map_key} (n_pairs={t.n_pairs})\n--- system\n{system}\n--- user\n"
                  + json.dumps(payloads[t.template_pair_id], ensure_ascii=False, indent=1))
    if a.dry_run:
        return

    from concurrent.futures import ThreadPoolExecutor

    def one(t):
        try:
            return t, core.call_model(client, a.model, system, payloads[t.template_pair_id], vschema), None
        except Exception as e:  # noqa: BLE001
            return t, None, e

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        results = list(ex.map(one, todo))
    for t, res, err in results:
        if err is not None:
            print(f"  {t.template_pair_id} {t.map_key}: call failed: {err!r}")
            continue
        verdict, messages, usage = res
        approvals[t.template_pair_id] = core.approval_entry(t, verdict, a.model, pv)
        core.append_jsonl(logs / "verdicts.jsonl", {
            "ts": datetime.now(timezone.utc).isoformat(), "model": a.model, "prompt_version": pv, "rubric": rubric.NAME,
            "template_pair_id": t.template_pair_id, "map_key": t.map_key, "subcategory": a.subcategory,
            "request": messages, "response": verdict, "usage": usage})
        counts[verdict["verdict"]] += 1
        print(f"  {t.template_pair_id} {t.map_key:28s} n={t.n_pairs:<5d} -> {verdict['verdict']:13s} "
              f"opp_ok={verdict['opposite_side_ok']:14s} conf={verdict.get('confidence')}  "
              f"({usage['prompt_tokens']}+{usage['completion_tokens']} tok, {usage['seconds']}s)")
        if verdict.get("human_review_question"):
            print(f"      human: {verdict['human_review_question']}")

    core.write_approvals(cfg.APPROVALS, approvals)
    print(f"\n{counts}  -> {cfg.APPROVALS}\nRe-run `python -m candidates {a.category} {a.subcategory}` to apply.")


if __name__ == "__main__":
    main()
