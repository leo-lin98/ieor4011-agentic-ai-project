"""Persistent, content-addressed event vectors."""
import hashlib
import io
import sqlite3
from pathlib import Path
import numpy as np
from .prepare import event_text

def embed_events(events, model, model_name, revision, representation, role, cache_path, progress=print):
    path = Path(cache_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    texts = [event_text(e, representation) for e in events]
    keys = [(e['platform'], e['event_uid'], hashlib.sha256(t.encode()).hexdigest(), model_name, revision, representation, role) for e, t in zip(events, texts)]
    vectors = [None] * len(events)
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE IF NOT EXISTS embeddings (platform TEXT, event_id TEXT, fingerprint TEXT, model TEXT, revision TEXT, representation TEXT, role TEXT, vector BLOB NOT NULL, PRIMARY KEY(platform,event_id,fingerprint,model,revision,representation,role))')
        missing = []
        for i, key in enumerate(keys):
            row = db.execute('SELECT vector FROM embeddings WHERE platform=? AND event_id=? AND fingerprint=? AND model=? AND revision=? AND representation=? AND role=?', key).fetchone()
            if row:
                vectors[i] = np.load(io.BytesIO(row[0]), allow_pickle=False)
            else: missing.append(i)
        progress(f'{role}: reused {len(events)-len(missing):,} cached vectors; generating {len(missing):,}.')
        # Commit batches so interrupted large runs can resume.
        for start in range(0, len(missing), 64):
            batch = missing[start:start + 64]
            encode = model.encode_query if role == 'query' else model.encode_document
            generated = encode([texts[i] for i in batch], normalize_embeddings=True, show_progress_bar=True)
            for i, vector in zip(batch, generated):
                vector = np.asarray(vector, dtype=np.float32)
                buf = io.BytesIO(); np.save(buf, vector, allow_pickle=False)
                db.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?,?,?,?,?,?)', (*keys[i], buf.getvalue()))
                vectors[i] = vector
            db.commit()
            progress(f'{role}: generated {min(start + len(batch), len(missing)):,}/{len(missing):,}.')
    return np.stack(vectors) if vectors else np.empty((0, 0))
