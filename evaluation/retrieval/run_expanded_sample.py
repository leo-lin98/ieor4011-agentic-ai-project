"""Rerank a reviewed event sample against every event in a saved profile snapshot."""
import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sentence_transformers import SentenceTransformer

from embeddings.cache import embed_events
from embeddings.prepare import build_events, load
from embeddings.search import configs, eligible


def run(reviews, profiles, cache, output, device):
    start = time.perf_counter()
    def progress(message):
        print(f'[{time.perf_counter()-start:.1f}s] {message}', flush=True)
    with open(reviews, newline='', encoding='utf-8-sig') as f:
        cases = list(csv.DictReader(f))
    if not cases or len({r['kalshi_uid'] for r in cases}) != len(cases):
        raise ValueError('Expected one unique Kalshi event per evaluation row')
    frame = load(profiles).astype(object).where(lambda d: pd.notna(d), None)
    events, issues = build_events(frame.to_dict('records'))
    wanted = {r['kalshi_uid'] for r in cases}
    queries = {e['event_uid']: e for e in events if e['platform'] == 'KALSHI' and e['event_uid'] in wanted}
    missing = wanted - queries.keys()
    if missing:
        raise ValueError(f'Query events absent from profile snapshot: {sorted(missing)}')
    candidates = [e for e in events if e['platform'] == 'POLYUS']
    if not candidates:
        raise ValueError('No Polymarket US candidates in profile snapshot')
    model_name = 'google/embeddinggemma-300m'
    revision = '57c266a740f537b4dc058e1b0cda161fd15afa75'
    model = SentenceTransformer(model_name, revision=revision, device=device, local_files_only=True)
    progress(f'Embedding {len(queries)} queries and {len(candidates)} candidates on {model.device}')
    qevents = [queries[r['kalshi_uid']] for r in cases]
    vectors_q = embed_events(qevents, model, model_name, revision, 'title', 'query', cache, progress)
    vectors_c = embed_events(candidates, model, model_name, revision, 'title', 'document', cache, progress)
    categories, sports, mapping_hash = configs()
    index_by_uid = {c['event_uid']: i for i, c in enumerate(candidates)}
    rows = []
    for row, query, vector in zip(cases, qevents, vectors_q):
        scores = vectors_c @ vector
        order_all = np.argsort(-scores, kind='stable')
        mask = np.fromiter((eligible(query, candidate, categories, sports) for candidate in candidates), dtype=bool, count=len(candidates))
        order_filtered = np.flatnonzero(mask)
        order_filtered = order_filtered[np.argsort(-scores[order_filtered], kind='stable')]
        ranks_all = {int(idx): rank for rank, idx in enumerate(order_all, 1)}
        ranks_filtered = {int(idx): rank for rank, idx in enumerate(order_filtered, 1)}
        def top(order):
            return [{'event_uid': candidates[int(idx)]['event_uid'], 'title': candidates[int(idx)]['title'], 'score': float(scores[idx])} for idx in order[:10]]
        targets = []
        for label in json.loads(row['related_events']):
            idx = index_by_uid.get(label['event_uid'])
            targets.append({'event_uid': label['event_uid'], 'title': label.get('title'), 'judgment': label.get('judgment'),
                            'score': float(scores[idx]) if idx is not None else None,
                            'rank_all': ranks_all.get(idx), 'rank_filtered': ranks_filtered.get(idx),
                            'in_pool': idx is not None})
        rows.append({'event_uid': query['event_uid'], 'title': query['title'], 'origin': row.get('case_origin'),
                     'review_status': row.get('review_status'), 'outcome': row['outcome'],
                     'targets': targets, 'top_10_all': top(order_all), 'top_10_filtered': top(order_filtered)})
    counts = {}
    for judgment in ('likely_exact', 'proposed_likely_exact', 'possible'):
        with_targets = [r for r in rows if any(t['judgment'] == judgment for t in r['targets'])]
        counts[judgment] = {'events_with_labels': len(with_targets),
                            'events_in_pool': sum(any(t['judgment'] == judgment and t['in_pool'] for t in r['targets']) for r in with_targets),
                            'top_10_all': sum(any(t['judgment'] == judgment and t['rank_all'] is not None and t['rank_all'] <= 10 for t in r['targets']) for r in with_targets),
                            'top_10_filtered': sum(any(t['judgment'] == judgment and t['rank_filtered'] is not None and t['rank_filtered'] <= 10 for t in r['targets']) for r in with_targets)}
    result = {'model': model_name, 'revision': revision, 'representation': 'title', 'profile_path': str(profiles),
              'profile_sha256': hashlib.sha256(Path(profiles).read_bytes()).hexdigest(), 'mapping_sha256': mapping_hash,
              'query_count': len(queries), 'candidate_count': len(candidates), 'profile_issues': issues,
              'metrics': counts, 'events': rows,
              'note': 'All is unfiltered; filtered uses current category/sports mappings. Proposed labels require review. Current profile snapshot contains open markets only.'}
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    progress(f'Wrote {output}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reviews', default='evaluation/retrieval/combined_event_evaluation.csv')
    parser.add_argument('--profiles', default='data/embeddings/evaluation-profile-snapshot.parquet')
    parser.add_argument('--cache', default='data/embeddings/events.sqlite3')
    parser.add_argument('--output', default='evaluation/retrieval/expanded_sample_results.json')
    parser.add_argument('--device', default='cpu')
    args = parser.parse_args()
    run(args.reviews, args.profiles, args.cache, args.output, args.device)


if __name__ == '__main__':
    main()
