"""Queue polling must stay cheap and safe after long histories or interruption."""
import json, math, sqlite3, tempfile, threading, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from studio_service import StudioService
from queue_estimates import timing_profile

SCHEMA='CREATE TABLE jobs(id TEXT PRIMARY KEY,project TEXT,chapter TEXT,shot TEXT,kind TEXT,payload TEXT,status TEXT,message TEXT,priority INTEGER,created REAL,started REAL,seconds REAL,attempt INTEGER,seed INTEGER)'

def fixture():
    service=StudioService.__new__(StudioService)
    service.cv=threading.Condition(threading.RLock());service.paused=True;service.current=None
    service.db=sqlite3.connect(':memory:');service.db.row_factory=sqlite3.Row;service.db.execute(SCHEMA)
    service.store=SimpleNamespace(root=Path('synthetic'))
    service.storage=SimpleNamespace(status=lambda:{'enabled':False})
    return service

def add(service,identity,kind='image',status='COMPLETE',seconds=2,created=1,payload=None):
    service.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (identity,'pr-'+'a'*16,'c',identity,kind,json.dumps(payload or {}),status,'Saved attempt',0,created,None,seconds,0,3))

class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.service=fixture();self.addCleanup(self.service.db.close)

    def test_interrupted_and_future_terminal_states_keep_queue_readable(self):
        add(self.service,'cloud','render-cloud','INTERRUPTED')
        add(self.service,'future','render-cloud','NEEDS_RECONCILIATION')
        state=self.service.snapshot()
        self.assertEqual(state['counts']['INTERRUPTED'],1)
        self.assertEqual(state['counts']['NEEDS_RECONCILIATION'],1)
        self.assertEqual(state['etaStatus'],'IDLE')
        self.assertEqual(len(state['jobs']),2)

    def test_newest_thirty_samples_per_profile_match_serial_semantics(self):
        for i in range(45):
            for model in ('first','second'):
                add(self.service,model+str(i),seconds=i+1,created=i,payload={'shotSnapshot':{'imageModel':model}})
        for value in (None,0,-1,float('inf')):add(self.service,'bad'+str(value),seconds=value,created=100)
        add(self.service,'failed',status='FAILED',seconds=900,created=101)
        state=self.service.snapshot()
        for model in ('first','second'):
            profile=timing_profile('image',{'shotSnapshot':{'imageModel':model}})
            self.assertEqual(state['etaSampleCountsByProfile'][profile],30)
            self.assertEqual(state['averageSecondsByKind'][profile],sum(range(16,46))/30)
        self.assertNotIn(timing_profile('image',{}),state['averageSecondsByKind'])
        self.assertTrue(all('payload' not in row for row in state['jobs']))

    def test_old_pending_job_stays_visible_after_history_window(self):
        add(self.service,'old-waiting',status='QUEUED',created=-1)
        for i in range(1200):add(self.service,str(i),created=i)
        state=self.service.snapshot()
        self.assertEqual(len(state['jobs']),1101)
        self.assertIn('old-waiting',{r['id'] for r in state['jobs']})
        self.assertEqual(state['counts']['COMPLETE'],1200)

    def test_nonimage_payload_is_not_parsed_or_exposed_during_polling(self):
        add(self.service,'narration','narration',payload={'unused':'x'*100000})
        with patch('studio_service.json.loads',side_effect=AssertionError('Unneeded payload parse')):
            state=self.service.snapshot()
        self.assertEqual(state['jobs'][0]['timingProfile'],'narration')
        self.assertNotIn('unused',str(state))

    def test_real_restart_marks_cloud_work_interrupted_without_requeueing_it(self):
        class Idle(StudioService):
            def worker(self):pass
        with tempfile.TemporaryDirectory() as root:
            with patch('studio_service.__file__',str(Path(root)/'studio_service.py')):
                def start():return Idle(root,SimpleNamespace(gpu='Test'),lambda:None,threading.Lock(),lambda:None,lambda:None,SimpleNamespace(control=lambda *_:None))
                first=start()
                first.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    ('cloud-test','pr-'+'a'*16,None,None,'render-cloud','{}','RUNNING','Rendering',0,1,1,0,0,None))
                first.db.commit();first.close()
                second=start()
                try:
                    state=second.snapshot()
                    self.assertEqual(state['counts']['INTERRUPTED'],1)
                    self.assertEqual(state['counts']['QUEUED'],0)
                    with self.assertRaisesRegex(ValueError,'not replayed'):second.control('retry','cloud-test')
                    self.assertEqual(second.snapshot()['counts']['QUEUED'],0)
                    names={r[1] for r in second.db.execute('PRAGMA index_list(jobs)')}
                    self.assertTrue({'jobs_created','jobs_status','jobs_project_kind_status'}<=names)
                finally:second.close()

if __name__=='__main__':unittest.main()
