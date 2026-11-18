"""Queue polling must stay cheap and safe after long histories or interruption."""
import json, math, sqlite3, tempfile, threading, unittest
from collections import OrderedDict
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from cost_control import SpendLedger
from studio_deferred_qc import DeferredQC
from studio_execution import ExecutionJournal
from studio_service import StudioService
from queue_estimates import timing_profile

SCHEMA='CREATE TABLE jobs(id TEXT PRIMARY KEY,project TEXT,chapter TEXT,shot TEXT,kind TEXT,payload TEXT,status TEXT,message TEXT,priority INTEGER,created REAL,started REAL,seconds REAL,attempt INTEGER,seed INTEGER)'

def fixture():
    service=StudioService.__new__(StudioService)
    service.cv=threading.Condition(threading.RLock());service.paused=True;service.current=None
    service._retry_receipt_stamps=OrderedDict()
    service.db=sqlite3.connect(':memory:');service.db.row_factory=sqlite3.Row;service.db.execute(SCHEMA)
    service.db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
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

    def test_render_queue_keeps_job_elapsed_and_only_learns_from_verified_fresh_work(self):
        add(self.service,'old','render-full',seconds=.05)
        add(self.service,'restored','render-full',seconds=.07,payload={'_renderTimingScope':'reused-base'})
        add(self.service,'composite','render-full',seconds=71,payload={'_renderTimingScope':'includes-chapter-render'})
        add(self.service,'waiting','render-full','QUEUED',seconds=None)
        state=self.service.snapshot()
        self.assertEqual(state['etaStatus'],'INSUFFICIENT_HISTORY')
        self.assertEqual(next(j for j in state['jobs'] if j['id']=='old')['seconds'],.05)
        add(self.service,'new','render-full',seconds=6,payload={'_renderTimingScope':'fresh-assembly'})
        state=self.service.snapshot()
        self.assertEqual(state['etaSampleCountsByProfile']['render-full'],1)
        self.assertEqual(state['etaSeconds'],6)
        self.assertEqual(state['averageSecondsByKind']['render-full'],6)

    def test_old_pending_job_stays_visible_after_history_window(self):
        add(self.service,'old-waiting',status='QUEUED',created=-1)
        for i in range(1200):add(self.service,str(i),created=i)
        state=self.service.snapshot()
        self.assertEqual(len(state['jobs']),1101)
        self.assertIn('old-waiting',{r['id'] for r in state['jobs']})
        self.assertEqual(state['counts']['COMPLETE'],1200)

    def test_nonimage_payload_is_not_parsed_or_exposed_during_polling(self):
        add(self.service,'narration','narration',payload={'unused':'x'*100000})
        add(self.service,'legacy','render-full',payload={'unused':'x'*100000})
        with patch('studio_service.json.loads',side_effect=AssertionError('Unneeded payload parse')):
            state=self.service.snapshot()
        self.assertEqual({j['timingProfile'] for j in state['jobs']},{'narration','render-full'})
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

class DenseQueueRetryTest(unittest.TestCase):
    def setUp(self):
        self.service=fixture()
        self.addCleanup(self.service.db.close)
        temporary=tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder=Path(temporary.name)
        self.service.store.folder=lambda _project:self.folder
        self.service.sync_queue_states=lambda _:None
        self.service.execution_journal=ExecutionJournal(self.folder/'executions.sqlite3')
        self.addCleanup(self.service.execution_journal.close)

    def add_parent(self, identity, *descriptor_states, status='FAILED', created=1):
        add(self.service,identity,'produce-story',status,created=created)
        if not descriptor_states:
            return
        ledger=DeferredQC(self.folder,identity)
        try:
            for number, descriptor_state in enumerate(descriptor_states):
                task=ledger.enqueue({'shot':str(number)})
                if descriptor_state=='CLAIMED':
                    ledger.claim(task,'test-execution')
                elif descriptor_state!='PENDING':
                    ledger.finish(task,descriptor_state)
        finally:
            ledger.close()
        return ledger.path

    def jobs(self):
        return {job['id']:job for job in self.service.snapshot()['jobs']}

    def test_terminal_dense_attempts_keep_receipts_and_require_explicit_new_run(self):
        for descriptor_state in ('FAILED','STALE','CANCELLED'):
            status='CANCELLED' if descriptor_state=='CANCELLED' else 'FAILED'
            path=self.add_parent(descriptor_state,descriptor_state,'PENDING',status=status)
            before=path.read_bytes()
            job=self.jobs()[descriptor_state]
            self.assertRegex(job['retryGuidance'],'receipts.*Generate full video')
            self.assertEqual(job['status'],status)
            with self.assertRaisesRegex(ValueError,'receipts.*Generate full video'):
                self.service.control('retry',descriptor_state)
            self.assertEqual(self.jobs()[descriptor_state],job)
            self.assertEqual(path.read_bytes(),before)
            self.assertTrue(self.service.paused)

    def test_bulk_skips_terminal_parents_before_applying_the_queue_limit(self):
        self.add_parent('blocked','FAILED',created=1)
        self.add_parent('legacy',created=3)
        self.add_parent('recoverable','PENDING','COMPLETE',created=4)
        add(self.service,'failed-image',status='FAILED',created=2)
        add(self.service,'failed-cloud','render-cloud','FAILED',created=0)
        for number in range(998):
            add(self.service,'waiting-'+str(number),status='QUEUED',created=5)
        self.service.control('retry')
        jobs=self.jobs()
        self.assertEqual(jobs['blocked']['status'],'FAILED')
        self.assertEqual(jobs['failed-cloud']['status'],'FAILED')
        self.assertEqual(jobs['failed-image']['status'],'QUEUED')
        self.assertEqual(jobs['legacy']['status'],'QUEUED')
        self.assertEqual(jobs['recoverable']['status'],'FAILED')
        self.service.db.execute("UPDATE jobs SET status='COMPLETE' WHERE id='failed-image'")
        self.service.control('retry')
        self.assertEqual(self.jobs()['recoverable']['status'],'QUEUED')

    def test_bulk_leaves_paused_jobs_held_when_all_failures_require_a_new_run(self):
        self.add_parent('blocked','STALE')
        add(self.service,'waiting',status='QUEUED')
        queue=self.service.control('retry')
        self.assertTrue(queue['paused'])
        self.assertEqual(queue['counts']['FAILED'],1)
        self.assertEqual(queue['counts']['QUEUED'],1)

    def test_open_unknown_missing_or_unreadable_ledgers_keep_existing_retry(self):
        self.add_parent('open','PENDING','COMPLETE')
        self.add_parent('unknown','FAILED','UNKNOWN')
        self.add_parent('claimed','FAILED','CLAIMED')
        self.add_parent('legacy')
        self.add_parent('active','FAILED',status='RUNNING')
        missing_files=set(self.folder.iterdir())
        for identity in ('open','unknown','claimed','legacy','active'):
            self.assertNotIn('retryGuidance',self.jobs()[identity])
        self.assertEqual(set(self.folder.iterdir()),missing_files)
        path=self.add_parent('unreadable','FAILED')
        path.write_bytes(b'unreadable')
        self.assertNotIn('retryGuidance',self.jobs()['unreadable'])
        for identity in ('open','unknown','claimed','legacy','unreadable'):
            self.service.control('retry',identity)
            self.assertEqual(self.jobs()[identity]['status'],'QUEUED')

    def test_unknown_paid_execution_keeps_reconciliation_path(self):
        self.add_parent('blocked','FAILED')
        project=self.jobs()['blocked']['project']
        journal=self.service.execution_journal
        context=journal.admit('different-parent',project,None,None,'story',1,'test',{})
        journal.observe(context.identity,'submitting',{'operationId':'held','provider':'test'})
        journal.finish(context.identity,'FAILED')
        self.assertNotIn('retryGuidance',self.jobs()['blocked'])
        self.service.control('retry','blocked')
        self.assertEqual(self.jobs()['blocked']['status'],'QUEUED')
        self.assertEqual(journal.unresolved_project(project)[0]['id'],context.identity)

    def test_unsettled_api_receipt_preserves_retry_and_protects_ledger(self):
        self.add_parent('blocked','FAILED')
        path=self.folder/'api-cost-ledger.json'
        ledger=SpendLedger(path,1,allow_multiple=True)
        ledger.reserve({'model':'gpt-6-luna','input':[],'max_output_tokens':1},request_id='hold',owner='owner')
        ledger.mark_submitted('hold',owner='owner')
        ledger.mark_unknown('hold',owner='owner')
        original=path.read_bytes()
        lock=path.with_suffix('.json.lock')
        lock.unlink()
        self.assertNotIn('retryGuidance',self.jobs()['blocked'])
        self.service.control('retry')
        self.assertEqual(self.jobs()['blocked']['status'],'QUEUED')
        self.assertEqual(path.read_bytes(),original)
        self.assertFalse(lock.exists())

    def test_polling_reuses_settled_receipts_and_observes_external_updates(self):
        path=self.add_parent('blocked','FAILED')
        ledger_path=self.folder/'api-cost-ledger.json'
        ledger_path.write_text(json.dumps({'spentUSD':0,'pending':None,'receipts':[]}))
        for _ in range(3):
            self.assertIn('retryGuidance',self.jobs()['blocked'])
        with patch.object(DeferredQC,'requires_new_parent',wraps=DeferredQC.requires_new_parent) as read_qc, \
             patch.object(SpendLedger,'read_snapshot',wraps=SpendLedger.read_snapshot) as read_ledger:
            for _ in range(10):
                self.assertIn('retryGuidance',self.jobs()['blocked'])
            read_qc.assert_not_called()
            read_ledger.assert_not_called()
            ledger=SpendLedger(ledger_path,1,allow_multiple=True)
            ledger.reserve({'model':'gpt-6-luna','input':[],'max_output_tokens':1},request_id='hold',owner='owner')
            self.assertNotIn('retryGuidance',self.jobs()['blocked'])
            read_ledger.assert_called()
            ledger.settle(uncharged=True,request_id='hold',owner='owner')
            self.assertIn('retryGuidance',self.jobs()['blocked'])
            with closing(sqlite3.connect(path)) as connection, connection:
                connection.execute("UPDATE tasks SET status='UNKNOWN'")
            self.assertNotIn('retryGuidance',self.jobs()['blocked'])
            read_qc.assert_called()

    def test_polling_reads_live_wal_when_main_descriptor_file_is_unchanged(self):
        path=self.add_parent('blocked','FAILED')
        with closing(sqlite3.connect(path)) as connection:
            connection.execute('PRAGMA journal_mode=WAL')
        self.assertIn('retryGuidance',self.jobs()['blocked'])
        stamp=self.service._retry_receipt_stamp(path)
        with closing(sqlite3.connect(path)) as connection:
            with connection:
                connection.execute("UPDATE tasks SET status='UNKNOWN'")
            self.assertEqual(self.service._retry_receipt_stamp(path),stamp)
            self.assertNotIn('retryGuidance',self.jobs()['blocked'])

    def test_retry_controls_read_saved_state_even_after_polling_cache_hit(self):
        self.add_parent('blocked','FAILED')
        self.assertIn('retryGuidance',self.jobs()['blocked'])
        with patch.object(DeferredQC,'requires_new_parent',wraps=DeferredQC.requires_new_parent) as read_qc:
            with self.assertRaisesRegex(ValueError,'Generate full video'):
                self.service.control('retry','blocked')
            self.service.control('retry')
            self.assertEqual(read_qc.call_count,2)

if __name__=='__main__':unittest.main()
