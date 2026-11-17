"""Retrieve Polymarket events for a varied sample of Kalshi events."""
import argparse
import json
import time
from pathlib import Path
import numpy as np
import pandas as pd
from .prepare import load, build_events
from .cache import embed_events
from .search import configs, rank, eligible

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    p.add_argument('--output', default='data/embeddings/candidates.json')
    p.add_argument('--cache', default='data/embeddings/events.sqlite3')
    p.add_argument('--queries', type=int, default=10)
    p.add_argument('--query-id', action='append', help='Kalshi event ID; repeat to select specific events')
    p.add_argument('--candidates', type=int, default=None, help='Limit candidate events (default: all)')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--representation', choices=('title', 'title_options'), default='title')
    p.add_argument('--model', default='google/embeddinggemma-300m')
    p.add_argument('--revision', default='main')
    p.add_argument('--device', choices=('cpu', 'mps', 'cuda'))
    a = p.parse_args()
    if a.queries < 1 or a.top_k < 1 or (a.candidates is not None and a.candidates < 1): p.error('sizes must be positive')
    start = time.perf_counter()
    def progress(message): print(f'[{time.perf_counter()-start:,.1f}s elapsed] {message}', flush=True)
    progress('Loading profiles and grouping events...')
    df = load(a.profiles).astype(object).where(lambda x: pd.notna(x), None)
    events, issues = build_events(df.to_dict('records'))
    progress(f'Profile issues: {issues}')
    queries = [e for e in events if e['platform'] == 'KALSHI']
    candidates = [e for e in events if e['platform'] == 'POLYUS']
    if a.query_id:
        wanted = set(a.query_id); queries = [e for e in queries if e['event_uid'] in wanted]
        missing = wanted - {e['event_uid'] for e in queries}
        if missing: raise ValueError(f'Unknown query events: {sorted(missing)}')
    elif len(queries) > a.queries:
        queries = [queries[i] for i in np.linspace(0, len(queries)-1, a.queries, dtype=int)]
    if a.candidates: candidates = candidates[:a.candidates]
    if not queries or not candidates: raise ValueError('Both platforms must have events')
    from sentence_transformers import SentenceTransformer
    progress(f'Loading model {a.model}; {len(queries):,} queries and {len(candidates):,} candidates.')
    model = SentenceTransformer(a.model, revision=a.revision, device=a.device)
    resolved = getattr(getattr(model[0], 'auto_model', None), 'config', None)
    resolved = getattr(resolved, '_commit_hash', None) or getattr(model, '_model_config', {}).get('_commit_hash')
    # A moving branch without a resolved commit cannot safely identify model weights.
    if not resolved and a.revision in ('main', 'master'):
        raise ValueError('Could not resolve model revision; pass a pinned model commit with --revision for safe caching')
    revision = resolved or a.revision
    progress(f'Model device: {model.device}; revision: {revision}')
    q = embed_events(queries, model, a.model, revision, a.representation, 'query', a.cache, progress)
    c = embed_events(candidates, model, a.model, revision, a.representation, 'document', a.cache, progress)
    categories, sports, mapping_hash = configs()
    results = []
    for i, query in enumerate(queries):
        progress(f'Ranking {i+1:,}/{len(queries):,}: {query["event_uid"]}')
        matches = rank(query, candidates, q[i], c, len(candidates), categories, sports)
        results.append({'query': query, 'candidates': [{'event': candidates[j], 'score': score} for j, score in matches[:a.top_k]],
                        'ranked_candidate_ids': [candidates[j]['event_uid'] for j, _ in matches],
                        'excluded_candidate_ids': [e['event_uid'] for e in candidates if not eligible(query, e, categories, sports)]})
    payload = {'metadata': {'model': a.model, 'revision': a.revision, 'resolved_revision': revision, 'representation': a.representation,
                            'mapping_sha256': mapping_hash, 'candidate_ids': [x['event_uid'] for x in candidates], 'profile_issues': issues}, 'results': results}
    out = Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, default=lambda x: x.tolist() if hasattr(x, 'tolist') else str(x)))
    progress(f'Wrote {len(results):,} event queries to {out}.')
if __name__ == '__main__': main()
