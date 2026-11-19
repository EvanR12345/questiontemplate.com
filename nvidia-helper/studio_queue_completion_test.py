"""A failure saving project status must never kill the worker or repeat a provider job."""
import json
import sqlite3
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from studio_data import new_project
from studio_service import StudioService


class IdleService(StudioService):
    def worker(self):
        # Start the real production worker after installing the fault and test jobs.
        pass

class OneStorageFailure:
    def __init__(self,db,prefix):
        self.db=db;self.prefix=prefix;self.failed=threading.Event()
    def execute(self,sql,*args):
        if not self.failed.is_set() and sql.startswith(self.prefix):
            self.failed.set()
            raise sqlite3.OperationalError('disk I/O error')
        return self.db.execute(sql,*args)
    def __getattr__(self,name):return getattr(self.db,name)
    def __enter__(self):self.db.__enter__();return self
    def __exit__(self,*args):return self.db.__exit__(*args)


class CompletionRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        config=patch('studio_service.__file__',str(Path(self.temp.name)/'studio_service.py'))
        config.start()
        self.addCleanup(config.stop)
        self.open_helper()
        self.addCleanup(self.close_helper)
        self.project=self.helper.store.save(new_project('Completion recovery'))
        self.pid=self.project['id']

    def open_helper(self):
        self.helper=IdleService(self.temp.name,SimpleNamespace(gpu='Test'),lambda:None,threading.Lock(),
            lambda:None,lambda:None,SimpleNamespace(control=lambda *_:None))
        self.helper.retarget_images=Mock()

    def close_helper(self):
        if self.helper:
            self.helper.close()
            self.assertFalse(self.helper.thread.is_alive())
            self.helper=None

    def enqueue(self,name,pid=None):
        pid=pid or self.pid
        self.helper.enqueue(pid,None,'retarget-images',options={})
        with self.helper.cv:
            changed=self.helper.db.execute("UPDATE jobs SET id=? WHERE project=? AND status='QUEUED' AND id LIKE 'job-%'",(name,pid))
            self.assertEqual(changed.rowcount,1)
            self.helper.db.commit()

    def start(self):
        self.helper.thread=threading.Thread(target=StudioService.worker,args=(self.helper,),daemon=True)
        self.helper.thread.start()

    def wait(self,condition):
        until=time.monotonic()+8
        while time.monotonic()<until:
            with self.helper.cv:
                if condition():
                    return
            time.sleep(.02)
        self.fail('Timed out waiting for the real queue worker.')

    def attempts(self):
        return [tuple(r) for r in self.helper.db.execute('SELECT id,status,attempt FROM jobs ORDER BY id')]

    def timings(self,pid=None):
        return [x['jobId'] for x in self.helper.store.load(pid or self.pid)['production']['timings']]

    def test_transient_memory_error_allows_next_job_and_repairs_timing_once(self):
        self.enqueue('first')
        other=self.helper.store.save(new_project('Independent project'))['id']
        self.enqueue('second',other)
        original=self.helper.sync_queue_states
        def fail_once(pid,completion):
            if completion['id']=='first' and not self.helper.project_update_warning:
                raise MemoryError('injected after job commit')
            return original(pid,completion)
        with patch.object(self.helper,'sync_queue_states',side_effect=fail_once):
            self.start()
            self.wait(lambda:self.helper.project_update_warning and self.helper.retarget_images.call_count==2)
            self.assertIn('MemoryError',self.helper.snapshot()['projectUpdateWarning'])
            self.wait(lambda:self.helper.next_project_update is None)
            self.assertEqual(self.attempts(),[('first','COMPLETE',1),('second','COMPLETE',1)])
            self.assertEqual(self.timings(),['first'])
            self.assertEqual(self.timings(other),['second'])
            self.assertIsNone(self.helper.snapshot()['projectUpdateWarning'])
            self.assertTrue(self.helper.thread.is_alive())

    def test_restart_after_writing_project_before_ack_does_not_repeat_work(self):
        self.enqueue('first')
        original=self.helper.sync_queue_states
        def lose_ack(pid,completion):
            original(pid,completion)
            raise OSError('injected after project file was replaced')
        with patch.object(self.helper,'sync_queue_states',side_effect=lose_ack):
            self.start()
            self.wait(lambda:self.helper.project_update_warning)
            self.assertEqual(self.helper.retarget_images.call_count,1)
            self.assertEqual(self.timings(),['first'])
            row=self.helper.db.execute('SELECT completion FROM queue_project_updates').fetchone()
            self.assertNotIn('payload',json.loads(row[0]))
            self.close_helper()
        self.open_helper()
        self.start()
        self.wait(lambda:self.helper.next_project_update is None)
        self.assertEqual(self.attempts(),[('first','COMPLETE',1)])
        self.assertEqual(self.timings(),['first'])
        self.helper.retarget_images.assert_not_called()

    def test_parent_completion_outlives_project_write_failure_and_restart(self):
        from unittest.mock import Mock
        self.helper.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            ('parent',self.pid,None,None,'produce-story','{}','QUEUED','',0,1,None,0,0,None))
        self.helper.db.commit()
        paid=threading.Event()
        action=Mock(side_effect=lambda *_:paid.set())
        self.helper.produce_story=action
        original=self.helper.store.mutate
        def disk_error(*args,**kwargs):
            if paid.is_set():raise OSError('Project file busy after production returned')
            return original(*args,**kwargs)
        with patch.object(self.helper.store,'mutate',side_effect=disk_error):
            self.start()
            self.wait(lambda:self.helper.project_update_warning)
            self.assertEqual(self.attempts(),[('parent','COMPLETE',1)])
            self.assertTrue(self.helper.thread.is_alive())
            action.assert_called_once()
            self.close_helper()
        self.open_helper();self.helper.produce_story=Mock()
        self.start();self.wait(lambda:self.helper.next_project_update is None)
        self.assertEqual(self.attempts(),[('parent','COMPLETE',1)])
        self.assertEqual(self.helper.store.load(self.pid)['production']['status'],'COMPLETE')
        self.helper.produce_story.assert_not_called()

    def test_unsubmitted_receipt_and_exact_refund_survive_separate_storage_failure(self):
        from studio_queue_image_recovery import QueuedImageReceipt
        from studio_data import get_shot
        chapter=self.project['chapters'][0]['id'];sid='review-shot'
        def add(p):
            p['chapters'][0]['scenes']=[{'id':'scene','shots':[{'id':sid,'chapterId':chapter,'sceneId':'scene',
                'prompt':'The reading room.','characters':[],'camera':{},'generationSettings':{},
                'status':'FAILED','start':0,'end':1}]}]
        self.helper.store.mutate(self.pid,add)
        token=self.helper.reserve_automatic_repair(self.pid,chapter,sid,'spec')
        receipt=QueuedImageReceipt(self.helper,{'id':'image','chapter':chapter,'shot':sid})
        receipt.dispatch('comfyui',0,'spec','','',None,{})
        with patch.object(self.helper,'release_unsubmitted_repair',side_effect=OSError('Snapshot busy')):
            self.helper.record_unsubmitted_repair(self.pid,chapter,sid,'spec',token,receipt=receipt)
        record=self.helper.db.execute('SELECT receipt FROM queued_image_receipts WHERE job=?',('image',)).fetchone()
        self.assertTrue(json.loads(record[0])['retrySafe'])
        self.assertEqual(get_shot(self.helper.store.load(self.pid),chapter,sid)['automaticRepairAttempts'],1)
        self.close_helper();self.open_helper();self.start()
        self.wait(lambda:self.helper.next_project_update is None)
        shot=get_shot(self.helper.store.load(self.pid),chapter,sid)
        self.assertEqual(shot['automaticRepairAttempts'],0)
        self.assertEqual(shot['automaticRepairClaims'],[])
        self.assertEqual(self.attempts(),[])

    def test_old_admission_result_cannot_mark_a_different_dispatch_safe(self):
        from studio_queue_image_recovery import QueuedImageReceipt
        job={'id':'image','chapter':'chapter','shot':'shot'}
        first=QueuedImageReceipt(self.helper,job);second=QueuedImageReceipt(self.helper,job)
        first.dispatch('comfyui',0,'one','','',None,{})
        second.dispatch('comfyui',1,'two','','',None,{})
        first.confirm_retry_safe()
        row=self.helper.db.execute('SELECT receipt FROM queued_image_receipts WHERE job=?',('image',)).fetchone()
        record=json.loads(row[0]);self.assertEqual(record['spec'],'two')
        self.assertFalse(record.get('retrySafe',False))

    def test_transient_terminal_sql_error_retries_only_bookkeeping(self):
        self.enqueue('first')
        db=OneStorageFailure(self.helper.db,'UPDATE jobs SET status=?,message=?,seconds=?')
        self.helper.db=db
        self.start();self.assertTrue(db.failed.wait(5))
        self.wait(lambda:self.helper.project_update_warning)
        self.assertEqual(self.helper.current,'first')
        self.helper.retarget_images.assert_called_once()
        self.wait(lambda:self.attempts()==[('first','COMPLETE',1)] and self.helper.next_project_update is None)
        self.assertEqual(self.timings(),['first'])
        self.helper.retarget_images.assert_called_once()

if __name__=='__main__':
    unittest.main()
