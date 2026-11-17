"""Evaluate labeled counterparts against a retrieval output."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

def evaluate(data, cases):
    by_query = {r['query']['market_uid']: r['candidates'] for r in data['results']}
    pool = set(data['metadata']['candidate_ids'])
    groups = defaultdict(list)
    for row in cases:
        relation = row['relationship'].strip().lower()
        if relation not in ('equivalent', 'complementary'): continue
        q, target = row['kalshi_uid'], row['polyus_uid']
        if q not in by_query: raise ValueError(f'Query absent from results: {q}')
        if target not in pool: raise ValueError(f'Counterpart absent from candidate pool: {target}')
        rank = next((i for i, c in enumerate(by_query[q], 1) if c['market']['market_uid'] == target), None)
        groups[relation].append({'query': q, 'counterpart': target, 'rank': rank})
    return {name: {'count': len(rows), 'recall_at_5': sum(r['rank'] is not None and r['rank'] <= 5 for r in rows)/len(rows), 'recall_at_10': sum(r['rank'] is not None and r['rank'] <= 10 for r in rows)/len(rows), 'examples': rows} for name, rows in groups.items()}

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('results'); p.add_argument('cases'); a = p.parse_args()
    data = json.loads(Path(a.results).read_text())
    if any(len(r['candidates']) < 10 for r in data['results']):
        print('Warning: fewer than 10 results for at least one query; missing ranks may be filtered or beyond the returned list.')
    with open(a.cases, newline='') as f: print(json.dumps(evaluate(data, csv.DictReader(f)), indent=2))
if __name__ == '__main__': main()
