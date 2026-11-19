"""Database-backed review and conservative scoring."""
import json
from . import storage

def run_view(db, run_id):
    run = db.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
    if not run: raise ValueError('Run not found')
    config = json.loads(run['config']); manifest = json.loads(run['manifest'])
    pairs = db.execute('''SELECT p.*,a.status,a.result,pr.label,pr.confidence FROM pairs p
      LEFT JOIN attempts a ON a.id=p.attempt_id LEFT JOIN predictions pr ON pr.id=p.prediction_id
      WHERE p.run_id=? ORDER BY p.position''', (run_id,)).fetchall()
    results=[]; matrix={}; counts={'failed':0,'refused':0,'missing_prediction':0,'missing_review':0,'changed_input':0,'conflicts':0,'incompatible':0,'scored':0,'correct':0}
    for p in pairs:
        status = 'success' if p['prediction_id'] else (p['status'] or 'missing')
        if status in ('failed','refused'): counts[status]+=1
        elif status != 'success': counts['missing_prediction']+=1
        reviews = [dict(r) for r in db.execute('SELECT * FROM reviews WHERE event_uid=? AND candidate_uid=? ORDER BY id', (p['event_uid'],p['candidate_uid']))]
        exact = [r for r in reviews if r['input'] is not None and r['input']==p['input']]
        # Explicit corrections supersede earlier judgments only in the same reviewed context.
        superseded = {r['supersedes'] for r in exact if r['supersedes']}
        superseded.update(r[0] for r in db.execute('SELECT resolved_id FROM review_resolutions WHERE review_id IN (SELECT id FROM reviews WHERE run_id=? AND event_uid=? AND candidate_uid=? AND input=?)', (run_id,p['event_uid'],p['candidate_uid'],p['input'])))
        active = [r for r in exact if r['id'] not in superseded]
        unique = {(r['label'],r['note'] or '',r['definition']) for r in active}
        state = 'missing_review'; selected = None
        if len(unique)>1: state='conflicts'
        elif active:
            selected=active[-1]
            # Historical reviews cannot be assumed to use any particular prompt definition.
            if selected['run_id'] != run_id: state='incompatible'
            else: state='reviewed'
        elif reviews: state='changed_input'
        if state != 'reviewed': counts[state]+=1
        if state=='reviewed' and status=='success' and selected['label'] in config['labels'] and p['label'] in config['labels']:
            human, prediction = selected['label'], p['label']; matrix.setdefault(human,{}).setdefault(prediction,0); matrix[human][prediction]+=1
            counts['scored']+=1; counts['correct']+=human==prediction
        results.append({'id':p['id'],'event_uid':p['event_uid'],'candidate_uid':p['candidate_uid'],'input':json.loads(p['input']) if p['input'] else None,'status':status,'label':p['label'],'state':state,'reviews':reviews})
    return {'id':run_id,'manifest':manifest,'config':config,'results':results,'counts':counts,'matrix':matrix}

def save_review(db, run_id, pair_id, label, note):
    from datetime import datetime, timezone
    run = db.execute('SELECT config FROM runs WHERE id=?', (run_id,)).fetchone()
    pair = db.execute('SELECT * FROM pairs WHERE id=? AND run_id=?', (pair_id,run_id)).fetchone()
    if not run or not pair or not pair['input']: raise ValueError('Pair has no classifier input')
    config=json.loads(run['config'])
    if label not in config['labels']: raise ValueError('Invalid label')
    previous = db.execute('SELECT id FROM reviews WHERE event_uid=? AND candidate_uid=? AND input=? AND run_id=? ORDER BY id DESC LIMIT 1', (pair['event_uid'],pair['candidate_uid'],pair['input'],run_id)).fetchone()
    # A new explicit review resolves all earlier judgments in this context for this run.
    value={'run':run_id,'pair':pair_id,'label':label,'note':note,'time':datetime.now(timezone.utc).isoformat()}
    data=json.loads(pair['input'])
    cur = db.execute('INSERT INTO reviews(fingerprint,event_uid,candidate_uid,input,event_title,candidate_title,label,note,reviewed_at,definition,run_id,prediction_id,supersedes,provenance) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      (storage.digest(value),pair['event_uid'],pair['candidate_uid'],pair['input'],data.get('kalshi',{}).get('title'),data.get('polymarket',{}).get('title'),label,note,value['time'],storage.digest(config),run_id,pair['prediction_id'],previous['id'] if previous else None,'interface'))

    db.execute('INSERT INTO review_resolutions SELECT ?,id FROM reviews WHERE event_uid=? AND candidate_uid=? AND input=? AND id<>?', (cur.lastrowid,pair['event_uid'],pair['candidate_uid'],pair['input'],cur.lastrowid))
