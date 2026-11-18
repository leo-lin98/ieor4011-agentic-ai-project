"""Select and classify saved embedding candidates. See README for usage."""
import argparse
import json
import os
import random
import sqlite3
import time
from pathlib import Path

LEGACY_LABELS = ['exact event match', 'related event', 'unrelated', 'uncertain']
LABELS = ['same outcome', 'direct outcome relationship', 'no direct relationship', 'insufficient information']
INSTRUCTIONS = (
    'Classify the relationship between two events, not individual contracts. '
    'Same outcome: both ask the same underlying question about the same occurrence. '
    'Direct outcome relationship: the questions differ, but a specific outcome of either event '
    'necessarily constrains a specific outcome of the other event. Consider either direction. '
    'No direct relationship: no such direct constraint is apparent; a shared topic, state, '
    'election cycle, party, or statistical correlation alone does not establish one. '
    'Separate House district races are no direct relationship merely because they share a state or election. '
    'Do not infer a direct relationship that requires aggregating many separate events. '
    'Insufficient information: the event text does not provide enough information to determine the relationship. '
    'Do not infer that contracts match or that an arbitrage opportunity exists.'
)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False))
    os.replace(temp, path)


def select(database, count, seed, category):
    with sqlite3.connect(f'{Path(database).resolve().as_uri()}?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = [dict(r) for r in db.execute('SELECT * FROM events ORDER BY event_uid')]
        if category != 'all':
            rows = [r for r in rows if (r['category'] or '').lower() == category.lower()]
        if count < 1 or count > len(rows):
            raise ValueError(f'Requested {count} events; {len(rows)} available')
        events = random.Random(seed).sample(rows, count)
        for event in events:
            event['candidates'] = [dict(r) for r in db.execute('SELECT * FROM matches WHERE event_uid=? ORDER BY rank LIMIT 10', (event['event_uid'],))]
    return {'seed': seed, 'category': category, 'events': events}


def contexts(profiles, ids):
    import pandas as pd
    df = pd.read_parquet(profiles)
    df = df[df.event_uid.isin(ids)]
    result = {}
    for uid, group in df.groupby('event_uid'):
        result[uid] = {}
        for field in ('event_subtitle', 'event_description'):
            values = sorted({str(x).strip() for x in group[field].dropna() if str(x).strip()}) if field in group else []
            result[uid][field] = values[0] if len(values) == 1 else None
            result[uid][field + '_status'] = 'available' if len(values) == 1 else ('conflicting' if values else 'missing')
    return result


def run(args):
    from dotenv import load_dotenv
    from openai import OpenAI
    load_dotenv()
    if not os.environ.get('OPENAI_API_KEY'):
        raise ValueError('Set OPENAI_API_KEY in .env or the environment')
    manifest = json.loads(Path(args.manifest).read_text())
    pairs = [(e, c) for e in manifest['events'] for c in e['candidates']]
    if len(pairs) > args.max_pairs:
        raise ValueError(f'{len(pairs)} pairs exceed --max-pairs {args.max_pairs}; no requests sent')
    output = Path(args.output)
    if output.exists():
        run_data = json.loads(output.read_text())
        if run_data['manifest'] != manifest or run_data['variant'] != args.variant or run_data['model'] != args.model:
            raise ValueError('Existing run configuration differs; choose a new output file')
    else:
        instructions = Path(args.instructions).read_text() if args.instructions else INSTRUCTIONS
        run_data = {'manifest': manifest, 'variant': args.variant, 'model': args.model, 'instructions': instructions, 'labels': LABELS, 'results': []}
        save(output, run_data)
    choices = run_data.get('labels', LEGACY_LABELS)
    ids = {e['event_uid'] for e, c in pairs} | {c['candidate_uid'] for e, c in pairs}
    ctx = contexts(args.profiles, ids) if args.variant == 'context' else {}
    client = OpenAI(max_retries=0)
    print(f'{len(manifest["events"])} events, {len(pairs)} pairs', flush=True)
    for i, (event, candidate) in enumerate(pairs, 1):
        key = (event['event_uid'], candidate['candidate_uid'])
        previous = next((r for r in run_data['results'] if (r['event_uid'], r['candidate_uid']) == key), None)
        if previous and (previous['status'] == 'success' or not args.retry_failed):
            continue
        content = {'kalshi': {'title': event['title']}, 'polymarket': {'title': candidate['candidate_title']}}
        if args.variant == 'context':
            content['kalshi']['context'] = ctx.get(key[0], {})
            content['polymarket']['context'] = ctx.get(key[1], {})
        item = {'event_uid': key[0], 'candidate_uid': key[1], 'input': content}
        start = time.monotonic()
        try:
            response = client.decisions.create(model=args.model, input=json.dumps(content), questions=[{'type': 'choice', 'name': 'relationship', 'instructions': run_data['instructions'], 'choices': [{'value': x} for x in choices]}])
            answer = response.answers[0]
            item.update(status='refused' if answer.type == 'refusal' else 'success', label=getattr(answer, 'choice', None), confidence=getattr(answer, 'confidence', None), usage=response.usage.model_dump() if getattr(response, 'usage', None) else None)
        except Exception as exc:
            item.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        item['seconds'] = time.monotonic() - start
        if previous:
            run_data['results'].remove(previous)
        run_data['results'].append(item)
        save(output, run_data)
        print(f'{i}/{len(pairs)}: {item["status"]}', flush=True)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('select')
    s.add_argument('--database', default='data/embeddings/report.sqlite3')
    s.add_argument('--events', type=int, default=1)
    s.add_argument('--seed', type=int, default=42)
    s.add_argument('--category', default='all')
    s.add_argument('--output', required=True)
    s.add_argument('--preview', action='store_true')
    r = sub.add_parser('run')
    r.add_argument('manifest')
    r.add_argument('--output', required=True)
    r.add_argument('--max-pairs', type=int, default=10)
    r.add_argument('--variant', choices=['titles', 'context'], default='titles')
    r.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    r.add_argument('--model', default='gpt-6-luna')
    r.add_argument('--instructions')
    r.add_argument('--retry-failed', action='store_true')
    args = p.parse_args()
    try:
        if args.command == 'select':
            data = select(args.database, args.events, args.seed, args.category)
            print(f'{len(data["events"])} events, {sum(len(e["candidates"]) for e in data["events"])} pairs')
            if not args.preview:
                if Path(args.output).exists():
                    raise ValueError('Manifest already exists; choose another output')
                save(args.output, data)
        else:
            run(args)
    except (ValueError, OSError) as exc:
        p.error(str(exc))

if __name__ == '__main__':
    main()
