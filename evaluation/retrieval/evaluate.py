"""Evaluate reviewed event counterparts, including full-pool ranks and filter misses."""
import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

def evaluate(data, cases):
    if data['metadata'].get('top_only'):
        raise ValueError('Full-rank evaluation requires results generated without --top-only')
    by_query = {r['query']['event_uid']: r for r in data['results']}
    pool = set(data['metadata']['candidate_ids'])
    groups = defaultdict(list)
    split_by_group = {}
    for row in cases:
        group, split = row.get('event_group', '').strip(), row.get('split', '').strip()
        if group and split:
            if group in split_by_group and split_by_group[group] != split:
                raise ValueError(f'Event group {group} occurs in multiple splits')
            split_by_group[group] = split
        relation = row['relationship'].strip().lower()
        if relation not in ('matching', 'equivalent', 'nonmatching', 'non-matching', 'unrelated'): continue
        q, target = row['kalshi_uid'], row['polyus_uid']
        if q not in by_query: raise ValueError(f'Query absent from results: {q}')
        if target not in pool: raise ValueError(f'Counterpart absent from candidate pool: {target}')
        result = by_query[q]
        ranked = result['ranked_candidate_ids']
        position = ranked.index(target) + 1 if target in ranked else None
        groups[split or 'unspecified'].append({'query': q, 'counterpart': target, 'relationship': relation,
                      'rank': position, 'excluded_by_filter': target in result['excluded_candidate_ids']})
    output = {}
    for split, rows in groups.items():
        positives = [r for r in rows if r['relationship'] in ('matching', 'equivalent')]
        output[split] = {'matching_count': len(positives), 'reviewed_count': len(rows),
                         'recall_at_5': sum(r['rank'] is not None and r['rank'] <= 5 for r in positives)/len(positives) if positives else None,
                         'recall_at_10': sum(r['rank'] is not None and r['rank'] <= 10 for r in positives)/len(positives) if positives else None,
                         'examples': rows}
    return output

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('results'); p.add_argument('cases'); a = p.parse_args()
    data = json.loads(Path(a.results).read_text())
    with open(a.cases, newline='') as f: print(json.dumps(evaluate(data, csv.DictReader(f)), indent=2))
if __name__ == '__main__': main()
