"""Serve a local, read-only browser for a SQLite embedding report."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import random
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

from embeddings.search import configs
from evaluation.retrieval.evaluate_event_sample import evaluate, query_uid, validate_cases
from .results import retrieval_direction

PAGE = Path(__file__).with_name('browser.html')
CONTRACT_PAGE = Path(__file__).with_name('contracts.html')
SORTS = {
    'volume': 'e.total_volume IS NULL, e.total_volume DESC, e.event_uid',
    'volume_asc': 'e.total_volume IS NULL, e.total_volume ASC, e.event_uid',
    'title': 'e.title COLLATE NOCASE, e.event_uid',
    'score': 'm.score IS NULL, m.score DESC, e.event_uid',
}


def connect(path):
    db = sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=ro', uri=True)
    db.row_factory = sqlite3.Row
    return db


def report_metadata(db):
    row = db.execute("SELECT value FROM run_metadata WHERE key='retrieval_metadata'").fetchone()
    return json.loads(row[0]) if row else {}


def report_context(db):
    row = db.execute("SELECT value FROM run_metadata WHERE key='retrieval_context'").fetchone()
    return json.loads(row[0]) if row else report_metadata(db)


def category_filter(column, category, platform):
    if platform:
        mappings, _, _ = configs()
        lookup = mappings[platform.lower()]
        labels = [key for key, value in lookup.items() if category == 'unknown' or category == value]
        match = f"lower(trim(coalesce({column}, ''))) IN ({','.join('?' for _ in labels)})"
        return ('NOT ' + match if category == 'unknown' else match), labels
    # Legacy reports used a sports versus nonempty-category split.
    if category == 'sports':
        return f"lower(trim({column})) = 'sports'", []
    if category == 'unknown':
        return f"({column} IS NULL OR trim({column}) = '')", []
    return f"{column} IS NOT NULL AND trim({column}) <> '' AND lower(trim({column})) <> 'sports'", []


def query_events(db, params, platform=None):
    def param(name, default=''):
        return params.get(name, [default])[0]
    conditions, values = [], []
    category = param('category', 'non-sports')
    if category in ('non-sports', 'sports', 'unknown'):
        condition, values = category_filter('e.category', category, platform)
        conditions.append(condition)
    elif category.startswith('category:'):
        conditions.append('e.category = ? COLLATE NOCASE')
        values.append(category[len('category:'):])
    elif category != 'all':
        raise ValueError('Unknown category filter')
    search = param('search').strip()
    if search:
        conditions.append("(instr(lower(coalesce(e.title, '')), lower(?)) > 0 OR instr(lower(e.event_uid), lower(?)) > 0)")
        values.extend([search, search])
    minimum = param('minimum').strip()
    if minimum:
        try:
            amount = float(minimum)
        except ValueError as exc:
            raise ValueError('Minimum volume must be a nonnegative number') from exc
        if not math.isfinite(amount) or amount < 0:
            raise ValueError('Minimum volume must be a nonnegative number')
        conditions.append('e.total_volume >= ?')
        values.append(amount)
    sort = param('sort', 'volume')
    if sort not in SORTS:
        raise ValueError('Unknown sort order')
    try:
        page = int(param('page', '1'))
    except ValueError as exc:
        raise ValueError('Invalid page number') from exc
    if page < 1 or page > 1000000:
        raise ValueError('Invalid page number')
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''
    join = ' FROM events e LEFT JOIN matches m ON m.event_uid = e.event_uid AND m.rank = 1'
    total = db.execute('SELECT count(*)' + join + where, values).fetchone()[0]
    rows = db.execute('SELECT e.*, m.candidate_title, m.candidate_uid, m.score' + join + where +
                      ' ORDER BY ' + SORTS[sort] + ' LIMIT 50 OFFSET ?', [*values, (page - 1) * 50]).fetchall()
    return {'total': total, 'page': page, 'page_size': 50, 'events': [dict(row) for row in rows]}


def sample_events(db, seed, method='volume', category='non-sports', size=20, platform=None):
    """Sample saved query events without replacement, with reproducible ordering."""
    if method not in ('volume', 'uniform') or category not in ('all', 'sports', 'non-sports'):
        raise ValueError('Invalid sampling method or category')
    if not 1 <= size <= 100:
        raise ValueError('Sample size must be between 1 and 100')
    where, values = [], []
    if category != 'all':
        condition, values = category_filter('category', category, platform)
        where.append(condition)
    if method == 'volume':
        where.append('total_volume > 0')
    clause = ' WHERE ' + ' AND '.join(where) if where else ''
    rows = [dict(row) for row in db.execute('SELECT * FROM events' + clause + ' ORDER BY event_uid', values)]
    if method == 'volume':
        rows = [row for row in rows if math.isfinite(row['total_volume'])]
    if len(rows) < size:
        raise ValueError(f'Only {len(rows)} eligible events; {size} are required')
    rng = random.Random(seed)
    if method == 'uniform':
        selected = rng.sample(rows, size)
    else:
        # Exponential race gives sequential probability proportional to volume.
        selected = sorted(rows, key=lambda row: math.log1p(-rng.random()) / row['total_volume'], reverse=True)[:size]
    for row in selected:
        row['candidates'] = [dict(candidate) for candidate in db.execute(
            'SELECT rank, candidate_uid, candidate_title, candidate_category, score FROM matches WHERE event_uid = ? ORDER BY rank LIMIT 10',
            (row['event_uid'],))]
    snapshot = db.execute("SELECT value FROM run_metadata WHERE key='volume_snapshot_latest_ms'").fetchone()
    return {'seed': seed, 'method': method, 'category': category, 'eligible_events': len(rows),
            'snapshot_ms': json.loads(snapshot[0]) if snapshot else None, 'events': selected}


def evaluate_report(db, rows):
    direction = validate_cases(rows)
    metadata = report_metadata(db)
    if direction != retrieval_direction(metadata):
        raise ValueError('The evaluation and report use different retrieval directions')
    results = {}
    for case in rows:
        uid = query_uid(case)
        if db.execute('SELECT 1 FROM events WHERE event_uid = ?', (uid,)).fetchone():
            results[uid] = [dict(row) for row in db.execute(
                'SELECT candidate_uid FROM matches WHERE event_uid = ? ORDER BY rank LIMIT 10', (uid,))]
    pool = set(metadata['candidate_ids']) if 'candidate_ids' in metadata else None
    return evaluate(rows, results, pool, allow_missing_queries=True)


def handler_for(database):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.path != '/api/evaluate':
                self.respond(404, b'Not found', 'text/plain; charset=utf-8')
                return
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024 * 1024:
                    raise ValueError('Invalid evaluation request size')
                rows = json.loads(self.rfile.read(size))
                with closing(connect(database)) as db:
                    result = evaluate_report(db, rows)
                self.respond(200, json.dumps(result, allow_nan=False).encode(), 'application/json; charset=utf-8')
            except ValueError as exc:
                self.respond(400, json.dumps({'error': str(exc)}).encode(), 'application/json; charset=utf-8')
            except (sqlite3.Error, OSError):
                self.respond(500, b'{"error":"Could not read the reporting database"}', 'application/json; charset=utf-8')

        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                if parsed.path in ('/', '/contracts'):
                    page = PAGE if parsed.path == '/' else CONTRACT_PAGE
                    self.respond(200, page.read_bytes(), 'text/html; charset=utf-8')
                    return
                if parsed.path in ('/browser.css', '/browser.js'):
                    asset = Path(__file__).with_name(parsed.path[1:])
                    kind = 'text/css; charset=utf-8' if parsed.path.endswith('.css') else 'text/javascript; charset=utf-8'
                    self.respond(200, asset.read_bytes(), kind)
                    return
                with closing(connect(database)) as db:
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    metadata = report_context(db)
                    query_platform, candidate_platform = retrieval_direction(metadata)
                    # Apply mapped categories to new runs and retain the filters for legacy reports.
                    platform_filter = query_platform if 'scope' in metadata else None
                    if parsed.path == '/api/filters':
                        categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM events WHERE category IS NOT NULL AND trim(category) <> '' ORDER BY category COLLATE NOCASE")]
                        row = db.execute("SELECT value FROM run_metadata WHERE key='volume_snapshot_latest_ms'").fetchone()
                        has_volume = db.execute('SELECT 1 FROM events WHERE total_volume > 0 LIMIT 1').fetchone() is not None
                        result = {'categories': categories, 'snapshot_ms': json.loads(row[0]) if row else None,
                                  'query_platform': query_platform, 'candidate_platform': candidate_platform,
                                  'scope': metadata.get('scope', 'non-sports'), 'has_volume': has_volume}
                    elif parsed.path == '/api/events':
                        result = query_events(db, params, platform_filter)
                    elif parsed.path == '/api/sample':
                        try:
                            seed = int(params.get('seed', [''])[0])
                        except ValueError as exc:
                            raise ValueError('Invalid sample seed') from exc
                        if seed < 0 or seed > 4294967295:
                            raise ValueError('Invalid sample seed')
                        try:
                            size = int(params.get('size', ['20'])[0])
                        except ValueError as exc:
                            raise ValueError('Invalid sample size') from exc
                        result = sample_events(db, seed, params.get('method', ['uniform'])[0],
                                               params.get('category', ['non-sports'])[0], size, platform_filter)
                        result.update(query_platform=query_platform, candidate_platform=candidate_platform)
                    elif parsed.path == '/api/contracts':
                        term = params.get('search', [''])[0].strip()
                        event_uid = params.get('event_uid', [''])[0].strip()
                        platform = params.get('platform', ['KALSHI'])[0]
                        if platform not in ('KALSHI', 'POLYUS') or (not event_uid and len(term) < 2):
                            raise ValueError('Enter at least two characters and select a platform')
                        columns = "SELECT market_uid, platform, event_uid, title, market_title, yes_means, rules, market_url, category, in_retrieval_pool FROM contracts WHERE platform = ? AND "
                        if event_uid:
                            rows = db.execute(columns + 'event_uid = ? ORDER BY market_uid LIMIT 101', (platform, event_uid)).fetchall()
                        else:
                            rows = db.execute(columns + """(instr(lower(market_uid), lower(?)) > 0
                                OR instr(lower(coalesce(event_uid, '')), lower(?)) > 0
                                OR instr(lower(coalesce(title, '')), lower(?)) > 0
                                OR instr(lower(coalesce(market_title, '')), lower(?)) > 0
                                OR instr(lower(coalesce(yes_means, '')), lower(?)) > 0)
                                ORDER BY market_uid LIMIT 101""", (platform, term, term, term, term, term)).fetchall()
                        result = {'contracts': [dict(r) for r in rows[:100]], 'truncated': len(rows) > 100}
                    elif parsed.path == '/api/collection-events':
                        term = params.get('search', [''])[0].strip()
                        if len(term) < 2:
                            raise ValueError('Enter at least two characters')
                        rows = db.execute("""SELECT event_uid, min(title) AS title,
                            max(in_retrieval_pool) AS in_retrieval_pool, count(*) AS contract_count
                            FROM contracts WHERE platform = ? AND event_uid IS NOT NULL
                            AND (instr(lower(event_uid), lower(?)) > 0
                                 OR instr(lower(coalesce(title, '')), lower(?)) > 0)
                            GROUP BY event_uid ORDER BY title COLLATE NOCASE, event_uid LIMIT 101""",
                                          (candidate_platform, term, term)).fetchall()
                        result = {'events': [dict(row) for row in rows[:100]], 'truncated': len(rows) > 100}
                    elif parsed.path == '/api/contract-pair':
                        source = params.get('source', [''])[0].strip()
                        target = params.get('target', [''])[0].strip()
                        row = db.execute('''SELECT c.market_uid, c.event_uid, c.title, c.market_title,
                            c.in_retrieval_pool, m.rank FROM contracts c
                            LEFT JOIN contracts k ON k.market_uid = ? AND k.platform = 'KALSHI'
                            LEFT JOIN matches m ON
                                m.event_uid = CASE WHEN ? = 'KALSHI' THEN k.event_uid ELSE c.event_uid END
                                AND m.candidate_uid = CASE WHEN ? = 'KALSHI' THEN c.event_uid ELSE k.event_uid END
                            WHERE c.market_uid = ? AND c.platform = 'POLYUS' ''',
                                         (source, query_platform, query_platform, target)).fetchone()
                        result = {'target': dict(row) if row else None}
                    elif parsed.path == '/api/matches':
                        uid = params.get('event_uid', [''])[0]
                        result = {'matches': [dict(r) for r in db.execute('SELECT rank, candidate_uid, candidate_title, candidate_category, score FROM matches WHERE event_uid = ? ORDER BY rank', (uid,))]}
                    else:
                        self.respond(404, b'Not found', 'text/plain; charset=utf-8')
                        return
                self.respond(200, json.dumps(result, allow_nan=False).encode(), 'application/json; charset=utf-8')
            except ValueError as exc:
                self.respond(400, json.dumps({'error': str(exc)}).encode(), 'application/json; charset=utf-8')
            except (sqlite3.Error, OSError):
                self.respond(500, b'{"error":"Could not read the reporting database"}', 'application/json; charset=utf-8')

        def respond(self, status, data, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', nargs='?', default='data/embeddings/report.sqlite3')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not Path(args.database).is_file():
        parser.error(f'Report database not found: {args.database}. Build it with python -m embeddings.reporting.database first.')
    with closing(connect(args.database)) as db:
        try:
            db.execute('SELECT event_uid FROM events LIMIT 1')
            db.execute('SELECT event_uid FROM matches LIMIT 1')
        except sqlite3.Error as exc:
            parser.error(f'Not a valid reporting database: {exc}')
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.database))
    print(f'Open http://127.0.0.1:{server.server_port}/ (press Ctrl+C to stop)', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
