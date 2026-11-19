import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from propose.classification import storage, review
from propose.classification.__main__ import run_database

class DatabaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.path=str(self.root/'db.sqlite')
        self.manifest={'events':[{'event_uid':'k','title':'K','candidates':[{'candidate_uid':'p','candidate_title':'P','rank':1}]}]}
        (self.root/'sample.json').write_text(json.dumps(self.manifest))
        self.args=SimpleNamespace(database=self.path, resume=None, sample=None, manifest=str(self.root/'sample.json'), max_pairs=10, model='test', instructions=None, variant='titles', profiles='', retry_failed=False, force=False)

    def test_cache_force_retry_and_resume(self):
        calls=[]
        class Decisions:
            def create(self, **kw):
                calls.append(kw)
                if len(calls)==1: raise RuntimeError('failed')
                return SimpleNamespace(answers=[SimpleNamespace(type='choice',choice='same outcome',confidence=.8)],usage=None)
        with patch.dict('os.environ',{'OPENAI_API_KEY':'test'}), patch('openai.OpenAI',return_value=SimpleNamespace(decisions=Decisions())):
            run_database(self.args)
            with storage.connect(self.path) as db: run_id=db.execute('SELECT id FROM runs').fetchone()[0]
            self.args.resume=run_id; self.args.retry_failed=True; run_database(self.args)
            self.args.resume=None; run_database(self.args) # cache hit, no call
            self.assertEqual(len(calls),2)
            self.args.force=True; run_database(self.args)
            self.assertEqual(len(calls),3)
        with storage.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM attempts').fetchone()[0],3)
            self.assertEqual(db.execute('SELECT count(*) FROM predictions').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM attempts WHERE status='failed'").fetchone()[0],1)
            req=json.loads(db.execute('SELECT request FROM predictions LIMIT 1').fetchone()[0]); self.assertIsNotNone(storage.cached(db,req))
            req['config']['model']='other'; self.assertIsNone(storage.cached(db,req))

    def test_import_idempotence_conflicts_and_resolution(self):
        source=self.root/'review.json'; inp={'kalshi':{'title':'K'},'polymarket':{'title':'P'}}
        def write(label): source.write_text(json.dumps({'reviews':{'x':{'event_uid':'k','candidate_uid':'p','input':inp,'label':label}}}))
        write('same outcome')
        with storage.connect(self.path) as db:
            storage.import_file(db,source); storage.import_file(db,source); write('no direct relationship'); storage.import_file(db,source)
            self.assertEqual(db.execute('SELECT count(*) FROM reviews').fetchone()[0],2)
            self.assertEqual(len(storage.conflicts(db)),1)
            self.assertEqual(db.execute('SELECT count(*) FROM reviews WHERE reviewed_at IS NOT NULL').fetchone()[0],0)
            config={'model':'test','instructions':'prompt','variant':'titles','labels':['same outcome','no direct relationship'],'settings':{}}
            run=storage.create_run(db,self.manifest,config); pair=db.execute('SELECT id FROM pairs').fetchone()[0]
            storage.record(db,run,pair,storage.request_data('k','p',inp,config),{'status':'success','label':'same outcome'})
            self.assertEqual(review.run_view(db,run)['counts']['conflicts'],1)
            review.save_review(db,run,pair,'same outcome','checked')
            result=review.run_view(db,run)
            self.assertEqual(result['counts']['scored'],1); self.assertEqual(result['counts']['correct'],1)
            review.save_review(db,run,pair,'no direct relationship','correction')
            self.assertEqual(review.run_view(db,run)['counts']['correct'],0)
            self.assertEqual(db.execute('SELECT count(*) FROM reviews').fetchone()[0],4)
