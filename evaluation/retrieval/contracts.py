"""Evaluate a lexical contract baseline inside independently labeled event pairs."""
import argparse
import csv
import json
from difflib import SequenceMatcher
from pathlib import Path
from embeddings.prepare import text_for

def evaluate(data, cases):
    pairs = {}
    for result in data['results']:
        for candidate in result['candidates']:
            pairs[(result['query']['event_uid'], candidate['event']['event_uid'])] = (result['query'], candidate['event'])
    examples = []
    for row in cases:
        if row['relationship'].strip().lower() not in ('matching', 'equivalent'): continue
        pair = (row['kalshi_event_uid'], row['polyus_event_uid'])
        if pair not in pairs: raise ValueError(f'Event pair absent from displayed candidates: {pair}')
        query, target = pairs[pair]
        source = next((c for c in query['contracts'] if c['market_uid'] == row['kalshi_market_uid']), None)
        if source is None: raise ValueError(f'Unknown query contract: {row["kalshi_market_uid"]}')
        ranked = sorted(target['contracts'], key=lambda c: (-SequenceMatcher(None, text_for(source).casefold(), text_for(c).casefold()).ratio(), str(c['market_uid'])))
        ids = [c['market_uid'] for c in ranked]
        if row['polyus_market_uid'] not in ids: raise ValueError(f'Unknown candidate contract: {row["polyus_market_uid"]}')
        examples.append({'query': row['kalshi_market_uid'], 'counterpart': row['polyus_market_uid'], 'rank': ids.index(row['polyus_market_uid']) + 1})
    return {'count': len(examples), 'recall_at_5': sum(r['rank'] <= 5 for r in examples)/len(examples) if examples else None,
            'recall_at_10': sum(r['rank'] <= 10 for r in examples)/len(examples) if examples else None, 'examples': examples}

def main():
    p = argparse.ArgumentParser(description=__doc__); p.add_argument('results'); p.add_argument('cases'); a = p.parse_args()
    with open(a.cases, newline='') as f: print(json.dumps(evaluate(json.loads(Path(a.results).read_text()), csv.DictReader(f)), indent=2))
if __name__ == '__main__': main()
