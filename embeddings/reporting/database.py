"""Build a queryable SQLite report from saved embedding results and snapshots."""
import argparse
from contextlib import closing
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from .batches import iter_results
from .volume import event_volumes


def build_report(input_path, output_path, snapshots, profiles):
    """Replace the report atomically after all results have been imported."""
    source, output = Path(input_path).resolve(), Path(output_path).resolve()
    if output == source or output == Path(profiles).resolve():
        raise ValueError('Report database must be separate from input files')
    if output.exists():
        try:
            with closing(sqlite3.connect(f'{output.as_uri()}?mode=ro', uri=True)) as existing:
                marker = existing.execute("SELECT value FROM run_metadata WHERE key='retrieval_input'").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ValueError('Output exists and is not a reporting database') from exc
        if not marker:
            raise ValueError('Output exists and is not a reporting database')
    volumes, snapshot_ms = event_volumes(snapshots, profiles)
    results = iter_results(source)
    metadata = next(results)
    candidate_events = {e['event_uid']: e for e in metadata.get('candidate_events', [])}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.report-', suffix='.sqlite3', dir=output.parent)
    os.close(fd)
    try:
        with closing(sqlite3.connect(temp)) as db, db:
            db.executescript('''
                CREATE TABLE run_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE events (
                    event_uid TEXT PRIMARY KEY, title TEXT, category TEXT, subcategory TEXT,
                    total_volume REAL, markets_with_volume INTEGER NOT NULL,
                    market_count INTEGER NOT NULL
                );
                CREATE TABLE matches (
                    event_uid TEXT NOT NULL REFERENCES events(event_uid),
                    rank INTEGER NOT NULL, candidate_uid TEXT NOT NULL,
                    candidate_title TEXT, candidate_category TEXT, score REAL NOT NULL,
                    PRIMARY KEY (event_uid, rank)
                );
                CREATE INDEX matches_candidate ON matches(candidate_uid);
                CREATE INDEX events_category_volume ON events(category, total_volume DESC);
            ''')
            db.executemany('INSERT INTO run_metadata VALUES (?, ?)', [
                ('retrieval_metadata', json.dumps(metadata)),
                ('volume_snapshot_latest_ms', json.dumps(snapshot_ms)),
                ('volume_snapshots', json.dumps(str(Path(snapshots).resolve()))),
                ('profiles', json.dumps(str(Path(profiles).resolve()))),
                ('retrieval_input', json.dumps(str(source))),
            ])
            count = 0
            for result in results:
                event = result['query']
                volume = volumes.get(event['event_uid'], {'total': None, 'observed': 0, 'contracts': event.get('option_count', 0)})
                db.execute('INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)',
                           (event['event_uid'], event.get('title'), event.get('category'), event.get('subcategory'),
                            volume['total'], volume['observed'], volume['contracts']))
                for rank, match in enumerate(result['candidates'], 1):
                    candidate = match.get('event') or candidate_events.get(match.get('event_uid'), {})
                    uid = candidate.get('event_uid') or match['event_uid']
                    db.execute('INSERT INTO matches VALUES (?, ?, ?, ?, ?, ?)',
                               (event['event_uid'], rank, uid, candidate.get('title'), candidate.get('category'), match['score']))
                count += 1
        os.replace(temp, output)
    finally:
        if os.path.exists(temp): os.unlink(temp)
    return count


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input', help='Saved embedding results JSON')
    parser.add_argument('output', help='SQLite report to create or replace (not the embedding cache)')
    parser.add_argument('--volume-snapshots', required=True, help='Kalshi pricing snapshot directory')
    parser.add_argument('--profiles', default='data/market_profiles/market_profiles.parquet')
    args = parser.parse_args()
    # Never overwrite the vector cache with a report.
    if Path(args.output).resolve() == Path('data/embeddings/events.sqlite3').resolve():
        parser.error('Output must be separate from the embedding cache')
    count = build_report(args.input, args.output, args.volume_snapshots, args.profiles)
    print(f'Wrote {count:,} events to {args.output}')


if __name__ == '__main__':
    main()
