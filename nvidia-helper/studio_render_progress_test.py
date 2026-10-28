"""Frame display sampling preserves production controls and durable receipts."""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from studio_execution import CommitCoordinator,ExecutionJournal
from studio_overlap import CloudStoryOverlap
from studio_service import StudioService,JobCancelled,AudioYield
from studio_render_progress import FrameProgress


class FrameProgressTest(unittest.TestCase):
    def test_samples_only_frame_messages_and_keeps_phase_and_clip_boundaries(self):
        now=[0.]
        sampler=FrameProgress(clock=lambda:now[0])
        self.assertTrue(sampler.allow('a','Rendering frame 1 / 100'))
        self.assertFalse(sampler.allow('a','Rendering frame 2 / 100'))
        now[0]=.25
        self.assertTrue(sampler.allow('a','Rendering frame 3 / 100'))
        self.assertFalse(sampler.allow('a','Rendering frame 4 / 100'))
        self.assertTrue(sampler.allow('a','Rendering frame 100 / 100'))
        self.assertTrue(sampler.allow('a','Assembling chapter'))
        self.assertTrue(sampler.allow('a','Saving narration'))
        self.assertTrue(sampler.allow('a','Rendering frame 50 / 100'))
        self.assertTrue(sampler.allow('a','Rendering frame 1 / 100'))
        self.assertTrue(sampler.allow('b','Rendering frame 2 / 100'))

    def test_context_storage_is_bounded_and_non_frame_messages_clear_it(self):
        sampler=FrameProgress(clock=lambda:0.)
        for index in range(300):sampler.allow(index,'Rendering frame 1 / 100')
        self.assertEqual(len(sampler.frames),256)
        self.assertTrue(sampler.allow(299,'Complete'))
        self.assertNotIn(299,sampler.frames)


class ProductionProgressTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name)
        self.db=sqlite3.connect(root/'jobs.sqlite3',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,message TEXT)')
        self.db.execute("INSERT INTO jobs VALUES('job','Preparing')");self.db.commit()
        self.now=[0.]
        self.service=SimpleNamespace(cv=threading.Condition(threading.RLock()),closed=False,
            cancel=False,yield_requested=False,paused=False,current='job',db=self.db,
            _frame_progress=FrameProgress(clock=lambda:self.now[0]))
        self.coordinator=CommitCoordinator()
        self.journal=ExecutionJournal(root/'execution.sqlite3')
        self.context=self.journal.admit('job','p','ch',None,'early-chapter-render',1,'hash',{})
        self.scheduler_checks=[]
        self.overlap=SimpleNamespace(root=self.service,
            scheduler=SimpleNamespace(check=lambda:self.scheduler_checks.append(True)),
            progress_keys={},frame_progress=FrameProgress(clock=lambda:self.now[0]),
            coordinator=self.coordinator,journal=self.journal,parent='job')

    def tearDown(self):
        self.coordinator.close();self.journal.close();self.db.close();self.temp.cleanup()

    def gate(self,overlap,message):
        if overlap:CloudStoryOverlap.gate(self.overlap,self.context,message)
        else:StudioService.gate(self.service,message)

    def test_both_real_gates_reduce_writes_and_keep_first_last_and_phase_immediate(self):
        for overlap in (False,True):
            with self.subTest(overlap=overlap):
                before=self.db.total_changes
                for index in range(1,101):self.gate(overlap,f'Rendering frame {index} / 100')
                self.assertEqual(self.db.total_changes-before,2)
                self.assertEqual(self.db.execute('SELECT message FROM jobs').fetchone()[0],'Rendering frame 100 / 100')
                self.gate(overlap,'Assembling chapter')
                self.assertEqual(self.db.total_changes-before,3)
                if overlap:
                    self.assertEqual(self.journal.snapshot('job')[0]['message'],'Assembling chapter')
                    self.assertEqual(len(self.scheduler_checks),101)

    def test_both_real_gates_publish_intermediate_progress_after_quarter_second(self):
        for overlap in (False,True):
            with self.subTest(overlap=overlap):
                before=self.db.total_changes;self.now[0]=0.
                self.gate(overlap,'Rendering frame 1 / 100')
                self.now[0]=.249;self.gate(overlap,'Rendering frame 2 / 100')
                self.assertEqual(self.db.total_changes-before,1)
                self.now[0]=.25;self.gate(overlap,'Rendering frame 3 / 100')
                self.assertEqual(self.db.total_changes-before,2)

    def test_cancel_yield_and_closed_are_checked_on_suppressed_frames(self):
        for overlap in (False,True):
            for flag,error in (('cancel',JobCancelled),('closed',JobCancelled),('yield_requested',AudioYield)):
                with self.subTest(overlap=overlap,flag=flag):
                    self.gate(overlap,'Rendering frame 1 / 100')
                    setattr(self.service,flag,True)
                    try:
                        with self.assertRaises(error):self.gate(overlap,'Rendering frame 2 / 100')
                    finally:setattr(self.service,flag,False)

    def test_context_cancellation_and_scheduler_failures_are_checked_on_suppressed_frame(self):
        self.gate(True,'Rendering frame 1 / 100')
        self.context.cancelled.set()
        with self.assertRaises(JobCancelled):self.gate(True,'Rendering frame 2 / 100')
        self.context.cancelled.clear()
        def failed():raise RuntimeError('scheduler failed')
        self.overlap.scheduler.check=failed
        with self.assertRaisesRegex(RuntimeError,'scheduler failed'):
            self.gate(True,'Rendering frame 2 / 100')

    def test_pause_waits_and_cancellation_wakes_on_suppressed_frame(self):
        for overlap in (False,True):
            with self.subTest(overlap=overlap):
                self.gate(overlap,'Rendering frame 1 / 100')
                entered=threading.Event();finished=threading.Event();errors=[]
                original=self.service.cv.wait
                def observed_wait(timeout=None):entered.set();return original(timeout)
                self.service.cv.wait=observed_wait;self.service.paused=True
                def execute():
                    try:self.gate(overlap,'Rendering frame 2 / 100')
                    except BaseException as error:errors.append(error)
                    finally:finished.set()
                worker=threading.Thread(target=execute);worker.start()
                try:
                    self.assertTrue(entered.wait(2));self.assertFalse(finished.is_set())
                    with self.service.cv:
                        self.service.cancel=True;self.service.cv.notify_all()
                    self.assertTrue(finished.wait(2));self.assertIsInstance(errors[0],JobCancelled)
                finally:
                    with self.service.cv:
                        self.service.paused=False;self.service.cancel=True;self.service.cv.notify_all()
                    worker.join(2);self.service.cancel=False;self.service.cv.wait=original

    def test_sampling_does_not_change_durable_receipt_or_terminal_state(self):
        self.journal.observe(self.context.identity,'submitting',{'operationId':'op','provider':'synthetic'})
        self.journal.observe(self.context.identity,'accepted',{'operationId':'op','remoteId':'receipt'})
        for index in range(1,101):self.gate(True,f'Rendering frame {index} / 100')
        self.journal.observe(self.context.identity,'completed',{'operationId':'op'})
        self.journal.finish(self.context.identity,'COMPLETE','Finished')
        row=self.journal.snapshot('job')[0]
        self.assertEqual(row['status'],'COMPLETE');self.assertEqual(row['message'],'Finished')
        self.assertEqual(row['operations']['op'],{'state':'COMPLETED','provider':'synthetic','remoteId':'receipt'})


if __name__=='__main__':unittest.main()
