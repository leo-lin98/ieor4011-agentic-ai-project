"""Generate candidate JSON for a bounded profile sample."""
import argparse
import json
from pathlib import Path
import pandas as pd
from .prepare import load, text_for
from .search import configs, rank

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--profiles", default="data/market_profiles/market_profiles.parquet")
    p.add_argument("--output", default="data/embeddings/candidates.json")
    p.add_argument("--queries", type=int, default=10)
    p.add_argument("--candidates", type=int, default=1000)
    p.add_argument("--top-k", type=int, default=10)
    p.add_argument("--model", default="google/embeddinggemma-300m")
    p.add_argument("--revision", default="main")
    p.add_argument("--include-rules", action="store_true")
    args = p.parse_args()
    if min(args.queries, args.candidates, args.top_k) < 1: p.error("sample sizes and top-k must be positive")
    from sentence_transformers import SentenceTransformer
    df = load(args.profiles).where(lambda x: pd.notna(x), None)
    queries = df[df.platform == "KALSHI"].head(args.queries).to_dict("records")
    candidates = df[df.platform == "POLYUS"].head(args.candidates).to_dict("records")
    if not queries or not candidates: raise ValueError("Both platforms must have profiles")
    model = SentenceTransformer(args.model, revision=args.revision)
    q = model.encode_query([text_for(x, args.include_rules) for x in queries], normalize_embeddings=True, show_progress_bar=True)
    c = model.encode_document([text_for(x, args.include_rules) for x in candidates], normalize_embeddings=True, show_progress_bar=True)
    categories, sports, mapping_hash = configs()
    results = [{"query": query, "candidates": [{"market": candidates[i], "score": score} for i, score in rank(query, candidates, q[j], c, args.top_k, categories, sports)]} for j, query in enumerate(queries)]
    payload = {"metadata": {"model": args.model, "revision": args.revision, "resolved_revision": getattr(model, "_model_config", {}).get("_commit_hash"), "fields": ["title", "market_title", "yes_means"] + (["rules"] if args.include_rules else []), "mapping_sha256": mapping_hash, "candidate_ids": [x["market_uid"] for x in candidates]}, "results": results}
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=lambda x: x.tolist() if hasattr(x, "tolist") else str(x)))
    print(f"Wrote {len(results)} queries to {out}")
if __name__ == "__main__": main()
