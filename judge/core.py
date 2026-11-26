"""Domain-neutral part of the judge: prompt assembly, verdict schema, the model call, approvals and logs.

Everything category-specific comes from a rubric (judge/rubrics/). The verdict contract is the same for
every category, so candidates, the T2 adapter and the dashboard never need to know which rubric ran.
"""
import json
import os
import time
from datetime import datetime, timezone

from collector.auth import load_env
from collector.config import DATA

MODEL = "gpt-6-luna"
CORE_VERSION = "2026-11-25.4"

STATUS = {"identical": "approved", "not_identical": "rejected", "unsure": "unsure"}
OPP = {"yes": True, "no": False, "unsure": None, "not_applicable": None}


def prompt_version(rubric):
    return f"{CORE_VERSION}+{rubric.NAME}.{rubric.VERSION}"


def system_prompt(rubric, subcategory):
    dims = "\n".join(f"- {k}: {v}" for k, v in rubric.DIMENSIONS.items())
    notes = rubric.notes(subcategory)
    return f"""You are a rules auditor for a prediction-market arbitrage desk. Two exchanges, Kalshi and Polymarket US, each list a binary contract. Deterministic code has already established that the two contracts describe the SAME PROPOSITION and has stated the DIRECTION: "same" means YES on Kalshi pays when YES on Polymarket US pays; "opposite" means YES on Kalshi pays when NO on Polymarket US pays.

Your job is the part code cannot do: read both rulebooks and decide whether the two contracts RESOLVE IDENTICALLY, so that holding one and the opposite side of the other is a riskless hedge. You are not asked whether they are about the same event; that is given. You are asked whether there is any outcome where one pays and the other does not.

Check each dimension and record what EACH rulebook says, quoting or closely paraphrasing. If a rulebook is silent on a dimension, say "silent". Dimensions:
{dims}

{rubric.VERDICT_SCOPE}

{rubric.ACCEPTED_RISKS}
{("Category notes: " + notes) if notes else ""}
Verdict rules:
- "identical": within the scope above, every outcome pays the same way on both contracts (with the stated direction).
- "not_identical": there is an in-scope outcome where payouts differ, or the direction mapping fails. Name it.
- "unsure": the text leaves it genuinely unclear what one side pays on in an in-scope outcome. Say exactly what a human must check. Do not use "unsure" for accepted risks.
opposite_side_ok follows the same scope: "yes" when Kalshi YES = Polymarket US NO in every in-scope outcome; accepted risks never make it "no" or "unsure"; "not_applicable" when no opposite-direction pairs exist.
Confidence is your probability that the verdict is right. Use plain language; no markdown."""


def schema(rubric):
    return {
        "type": "object", "additionalProperties": False,
        "required": ["verdict", "opposite_side_ok", "confidence", "checklist", "reasoning", "human_review_question"],
        "properties": {
            "verdict": {"type": "string", "enum": ["identical", "not_identical", "unsure"]},
            "opposite_side_ok": {"type": "string", "enum": ["yes", "no", "unsure", "not_applicable"]},
            "confidence": {"type": "number"},
            "checklist": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["dimension", "kalshi", "polyus", "same", "note"],
                "properties": {
                    "dimension": {"type": "string", "enum": list(rubric.DIMENSIONS)},
                    "kalshi": {"type": "string"}, "polyus": {"type": "string"},
                    "same": {"type": "string", "enum": ["yes", "no", "unsure"]},
                    "note": {"type": "string"},
                }}},
            "reasoning": {"type": "string"},
            "human_review_question": {"type": "string"},
        },
    }


def _iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if ms == ms and ms is not None else None


def load_rules(uids):
    """{market_uid: rules text} from the current catalogs, for the example markets only."""
    from candidates import common as C
    out = {}
    for plat in ("KALSHI", "POLYUS"):
        want = [u for u in uids if u.startswith(plat + ":")]
        if not want:
            continue
        df = C.read_catalog_df(plat)
        df = df[df.market_uid.isin(want)]
        for r in df.itertuples():
            out[r.market_uid] = C.kalshi_rules(r) if plat == "KALSHI" else C.polyus_rules(r)
    return out


def build_input(rubric, t, examples, category, subcategory, rules):
    """The user message: one example pair per direction, each with its own two rulebooks, and the claim."""
    ex = []
    for d, r in examples.items():
        ex.append({"direction": d,
                   "kalshi": {"market_uid": r.kalshi_market_uid, "title": r.kalshi_title, "yes_pays_on": r.kalshi_yes,
                              "rules": rules.get(r.kalshi_market_uid) or t.kalshi_example},
                   "polyus": {"market_uid": r.polyus_market_uid, "title": r.polyus_title, "yes_side": r.polyus_yes,
                              "rules": rules.get(r.polyus_market_uid) or t.polyus_example},
                   "event_time": _iso(r.event_time_ms), "match_key": r.match_key})
    return {
        "category": category, "subcategory": subcategory, "bet_type": t.map_key, "template_pair_id": t.template_pair_id,
        "claim_by_code": rubric.claim(t, examples),
        "note": f"{int(t.n_pairs)} market pairs share these two rule templates; only names, dates, times and numbers "
                "differ between markets. Each example below carries the exact rules of its own two markets. Your verdict "
                "covers every pair with these templates.",
        "directions_present": sorted(examples),
        "example_pairs": ex,
    }


def openai_client():
    env = load_env()
    key = env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise SystemExit("no OpenAI key: add OPENAI_API_KEY to .env")
    from openai import OpenAI
    return OpenAI(api_key=key)


def call_model(client, model, system, payload, verdict_schema):
    messages = [{"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, indent=1)}]
    t0 = time.monotonic()
    try:
        r = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": "template_verdict", "strict": True, "schema": verdict_schema}})
    except Exception as e:  # noqa: BLE001  (models / SDKs without json_schema: fall back to a plain JSON object)
        if "json_schema" not in str(e) and "response_format" not in str(e):
            raise
        messages[0]["content"] += "\n\nRespond with one JSON object matching this schema exactly:\n" + json.dumps(verdict_schema)
        r = client.chat.completions.create(model=model, messages=messages, response_format={"type": "json_object"})
    out = json.loads(r.choices[0].message.content)
    usage = getattr(r, "usage", None)
    return out, messages, {"prompt_tokens": getattr(usage, "prompt_tokens", None),
                           "completion_tokens": getattr(usage, "completion_tokens", None),
                           "seconds": round(time.monotonic() - t0, 2), "response_id": getattr(r, "id", None)}


def approval_entry(t, verdict, model, pv):
    return {
        "status": STATUS[verdict["verdict"]], "opposite_side_ok": OPP[verdict["opposite_side_ok"]],
        "verdict": verdict["verdict"], "confidence": verdict.get("confidence"),
        "reasoning": verdict.get("reasoning"), "human_review_question": verdict.get("human_review_question") or None,
        "checklist": verdict.get("checklist"),
        "map_key": t.map_key, "n_pairs": int(t.n_pairs),
        "reviewed_by": f"llm:{model}", "prompt_version": pv, "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def log_dir(category, subcategory):
    return DATA / "judge" / category / subcategory


def append_jsonl(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_approvals(path, approvals):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(approvals, indent=1, ensure_ascii=False))
    os.replace(tmp, path)
