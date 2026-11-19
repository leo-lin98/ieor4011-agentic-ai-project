"""SQLite storage and repeatable import for classification experiments."""
import hashlib
import json
import sqlite3
import uuid
from pathlib import Path

DEFAULT_DB = 'data/classification.sqlite3'

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

class Connection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()

def connect(path=DEFAULT_DB):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30, factory=Connection)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    version = db.execute('PRAGMA user_version').fetchone()[0]
    if version > 1:
        raise ValueError('Database schema is newer than this application')
    if version == 0:
        db.executescript('''
        CREATE TABLE samples(id TEXT PRIMARY KEY, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, manifest TEXT NOT NULL);
        CREATE TABLE manifests(source_id INTEGER PRIMARY KEY REFERENCES imports(id), content TEXT NOT NULL);
        CREATE TABLE runs(id TEXT PRIMARY KEY, created_at TEXT, manifest TEXT NOT NULL, config TEXT NOT NULL, provenance TEXT);
        CREATE TABLE pairs(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), position INTEGER NOT NULL, event_uid TEXT NOT NULL, candidate_uid TEXT NOT NULL, rank INTEGER, input TEXT, prediction_id INTEGER REFERENCES predictions(id), attempt_id INTEGER REFERENCES attempts(id), UNIQUE(run_id,position));
        CREATE TABLE predictions(id INTEGER PRIMARY KEY, cache_key TEXT NOT NULL, request TEXT NOT NULL, label TEXT, confidence REAL, usage TEXT, created_at TEXT, cacheable INTEGER NOT NULL DEFAULT 1);
        CREATE UNIQUE INDEX prediction_cache ON predictions(cache_key) WHERE cacheable=1;
        CREATE TABLE attempts(id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id), pair_id INTEGER NOT NULL REFERENCES pairs(id), cache_key TEXT, request TEXT, status TEXT NOT NULL, result TEXT, error TEXT, seconds REAL, usage TEXT, started_at TEXT, finished_at TEXT, prediction_id INTEGER REFERENCES predictions(id));
        CREATE TABLE reviews(id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL UNIQUE, event_uid TEXT NOT NULL, candidate_uid TEXT NOT NULL, input TEXT, event_title TEXT, candidate_title TEXT, label TEXT NOT NULL, note TEXT, reviewed_at TEXT, definition TEXT, run_id TEXT REFERENCES runs(id), prediction_id INTEGER REFERENCES predictions(id), supersedes INTEGER REFERENCES reviews(id), provenance TEXT);
        CREATE TABLE review_resolutions(review_id INTEGER REFERENCES reviews(id), resolved_id INTEGER REFERENCES reviews(id), PRIMARY KEY(review_id,resolved_id));
        CREATE TABLE review_sources(review_id INTEGER REFERENCES reviews(id), source_id INTEGER REFERENCES imports(id), PRIMARY KEY(review_id,source_id));
        CREATE TABLE imports(id INTEGER PRIMARY KEY, path TEXT NOT NULL, fingerprint TEXT NOT NULL, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, kind TEXT NOT NULL, outcome TEXT, UNIQUE(path,fingerprint));
        PRAGMA user_version=1;
        ''')
    return db

def create_run(db, manifest, config, provenance=None, run_id=None, created_at=None):
    run_id = run_id or str(uuid.uuid4())
    db.execute('INSERT INTO runs VALUES (?,?,?,?,?)', (run_id, created_at, canonical(manifest), canonical(config), provenance))
    position = 0
    for event in manifest['events']:
        for candidate in event['candidates']:
            db.execute('INSERT INTO pairs(run_id,position,event_uid,candidate_uid,rank) VALUES (?,?,?,?,?)',
                       (run_id, position, event['event_uid'], candidate['candidate_uid'], candidate.get('rank')))
            position += 1
    return run_id

def request_data(event_uid, candidate_uid, content, config):
    return {'event_uid': event_uid, 'candidate_uid': candidate_uid, 'input': content, 'config': config}

def cached(db, request):
    return db.execute('SELECT * FROM predictions WHERE cache_key=? AND cacheable=1', (digest(request),)).fetchone()

def record(db, run_id, pair_id, request, result, force=False, started_at=None, finished_at=None):
    key = digest(request) if request is not None else None
    prediction_id = None
    if result['status'] == 'success' and request is not None:
        old = cached(db, request)
        # Preserve different historical or deliberately repeated outcomes as separate predictions.
        if old and not force and old['label'] == result.get('label') and old['confidence'] == result.get('confidence'):
            prediction_id = old['id']
        else:
            cur = db.execute('INSERT INTO predictions(cache_key,request,label,confidence,usage,created_at,cacheable) VALUES(?,?,?,?,?,?,?)',
                             (key, canonical(request), result.get('label'), result.get('confidence'), canonical(result.get('usage')), finished_at, int(old is None)))
            prediction_id = cur.lastrowid
    cur = db.execute('INSERT INTO attempts(run_id,pair_id,cache_key,request,status,result,error,seconds,usage,started_at,finished_at,prediction_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
        (run_id, pair_id, key, canonical(request) if request is not None else None, result['status'], canonical(result), result.get('error'), result.get('seconds'), canonical(result.get('usage')), started_at, finished_at, prediction_id))
    db.execute('UPDATE pairs SET input=?,prediction_id=?,attempt_id=? WHERE id=?', (canonical(request['input']) if request else None, prediction_id, cur.lastrowid, pair_id))
    return cur.lastrowid

def import_file(db, path):
    from .__main__ import LEGACY_LABELS
    path = Path(path).resolve(); raw = path.read_bytes(); fingerprint = hashlib.sha256(raw).hexdigest()
    existing = db.execute('SELECT outcome FROM imports WHERE path=? AND fingerprint=?', (str(path), fingerprint)).fetchone()
    if existing:
        return json.loads(existing['outcome'])
    data = json.loads(raw)
    kind = 'run' if 'manifest' in data and 'results' in data else ('reviews' if 'reviews' in data else 'manifest')
    cur = db.execute('INSERT INTO imports(path,fingerprint,kind) VALUES(?,?,?)', (str(path), fingerprint, kind)); source_id = cur.lastrowid
    report = {'source': str(path), 'kind': kind, 'runs': 0, 'queries': 0, 'pairs': 0, 'statuses': {}, 'reviews': 0, 'duplicates': 0, 'missing': {}}
    if kind == 'run':
        manifest = data['manifest']; config = {k: data.get(k) for k in ('model','instructions','variant')}; config['labels'] = data.get('labels', LEGACY_LABELS); config['settings'] = {}
        run_id = 'import-' + fingerprint
        if not db.execute('SELECT 1 FROM runs WHERE id=?', (run_id,)).fetchone():
            create_run(db, manifest, config, str(path), run_id)
            pairs = db.execute('SELECT * FROM pairs WHERE run_id=? ORDER BY position', (run_id,)).fetchall()
            for result in data['results']:
                matches = [p for p in pairs if p['event_uid']==result['event_uid'] and p['candidate_uid']==result['candidate_uid']]
                if len(matches) != 1: raise ValueError(f'Ambiguous or missing pair in {path}: {result}')
                request = request_data(result['event_uid'], result['candidate_uid'], result['input'], config) if result.get('input') is not None and all(config.get(k) is not None for k in ('model','instructions','variant')) else None
                record(db, run_id, matches[0]['id'], request, result)
        else: report['duplicates'] += 1
        report.update(runs=1, queries=len(manifest['events']), pairs=sum(len(e['candidates']) for e in manifest['events']))
        for result in data['results']: report['statuses'][result['status']] = report['statuses'].get(result['status'], 0)+1
        report['missing'] = {'pair_results': report['pairs']-len(data['results']), 'original_timestamps': len(data['results'])}
    elif kind == 'manifest':
        report.update(queries=len(data['events']), pairs=sum(len(e['candidates']) for e in data['events']))
        # Preserve standalone manifests even if no run exists.
        db.execute('INSERT INTO manifests VALUES(?,?)', (source_id, canonical(data)))
    else:
        for review in data['reviews'].values():
            fp = digest(review); existed = db.execute('SELECT id FROM reviews WHERE fingerprint=?', (fp,)).fetchone()
            if existed: rid = existed['id']; report['duplicates'] += 1
            else:
                cur = db.execute('INSERT INTO reviews(fingerprint,event_uid,candidate_uid,input,event_title,candidate_title,label,note,reviewed_at,definition,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                    (fp, review['event_uid'], review['candidate_uid'], canonical(review.get('input')) if review.get('input') is not None else None, review.get('event_title'), review.get('candidate_title'), review['label'], review.get('note'), review.get('reviewed_at'), 'legacy' if review['label'] in LEGACY_LABELS else None, canonical(review)))
                rid = cur.lastrowid
            db.execute('INSERT OR IGNORE INTO review_sources VALUES(?,?)', (rid,source_id)); report['reviews'] += 1
            if not review.get('reviewed_at'): report['missing']['reviewed_at'] = report['missing'].get('reviewed_at',0)+1
    db.execute('UPDATE imports SET outcome=? WHERE id=?', (canonical(report), source_id))
    return report

def conflicts(db):
    return [dict(r) for r in db.execute('''SELECT event_uid,candidate_uid,input,group_concat(id) review_ids,count(DISTINCT label || char(0) || coalesce(note,'')) judgments FROM reviews WHERE supersedes IS NULL GROUP BY event_uid,candidate_uid,input HAVING judgments>1''')]
