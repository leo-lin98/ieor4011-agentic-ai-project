"""Serve a local, read-only browser for a SQLite embedding report."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import parse_qs, urlsplit

PAGE = Path(__file__).with_name('browser.html')
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


def query_events(db, params):
    def param(name, default=''):
        return params.get(name, [default])[0]
    conditions, values = [], []
    category = param('category', 'non-sports')
    if category == 'non-sports':
        conditions.append("e.category IS NOT NULL AND trim(e.category) <> '' AND lower(trim(e.category)) <> 'sports'")
    elif category == 'unknown':
        conditions.append("(e.category IS NULL OR trim(e.category) = '')")
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


def handler_for(database):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urlsplit(self.path)
            try:
                if parsed.path == '/':
                    self.respond(200, PAGE.read_bytes(), 'text/html; charset=utf-8')
                    return
                with closing(connect(database)) as db:
                    params = parse_qs(parsed.query, keep_blank_values=True)
                    if parsed.path == '/api/filters':
                        categories = [r[0] for r in db.execute("SELECT DISTINCT category FROM events WHERE category IS NOT NULL AND trim(category) <> '' ORDER BY category COLLATE NOCASE")]
                        row = db.execute("SELECT value FROM run_metadata WHERE key='volume_snapshot_latest_ms'").fetchone()
                        result = {'categories': categories, 'snapshot_ms': json.loads(row[0]) if row else None}
                    elif parsed.path == '/api/events':
                        result = query_events(db, params)
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
