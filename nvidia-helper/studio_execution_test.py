import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from studio_execution import (CommitCoordinator,CoordinatedStore,CoordinatedTrace,
    ExecutionJournal,ImageLane,BoundedExecutions,UnresolvedExecution)
from production_trace import ProductionTrace
from studio_data import ProjectStore,new_project


class ExecutionTest(unittest.TestCase):
    def test_second_journal_owner_cannot_recover_or_modify_live_executions(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'executions.sqlite3';journal=ExecutionJournal(path)
            context=journal.admit('job','project',None,None,'stage',0,'input',{})
            with self.assertRaisesRegex(RuntimeError,'Another helper'):ExecutionJournal(path)
            self.assertEqual(journal.snapshot('job')[0]['status'],'RUNNING')
            journal.finish(context.identity,'COMPLETE');journal.close()
            replacement=ExecutionJournal(path);replacement.close()

    def test_terminal_outcomes_and_remote_identity_cannot_be_rewritten(self):
        with tempfile.TemporaryDirectory() as root:
            journal=ExecutionJournal(Path(root)/'executions.sqlite3')
            context=journal.admit('job','project',None,None,'stage',0,'input',{})
            journal.observe(context.identity,'submitting',{'operationId':'image'})
            journal.observe(context.identity,'accepted',{'operationId':'image','remoteId':'owned'})
            with self.assertRaisesRegex(RuntimeError,'Conflicting'):
                journal.observe(context.identity,'accepted',{'operationId':'image','remoteId':'other'})
            journal.observe(context.identity,'completed',{'operationId':'image'})
            journal.finish(context.identity,'COMPLETE');journal.finish(context.identity,'COMPLETE')
            with self.assertRaisesRegex(RuntimeError,'terminal'):journal.finish(context.identity,'CANCELLED')
            row=journal.snapshot('job')[0];self.assertEqual(row['status'],'COMPLETE')
            self.assertEqual(row['operations']['image']['remoteId'],'owned');journal.close()

    def test_restart_blocks_submitted_and_completed_but_unpublished_operations(self):
        for completed in (False,True):
            with tempfile.TemporaryDirectory() as root:
                path=Path(root)/'executions.sqlite3'
                journal=ExecutionJournal(path)
                context=journal.admit('job','project','chapter','shot','qc',7,'input',{'quality':'same'},['handoff'])
                journal.observe(context.identity,'submitting',{'operationId':'api','provider':'luna'})
                journal.observe(context.identity,'accepted',{'operationId':'api','remoteId':'response-owned'})
                if completed:journal.observe(context.identity,'completed',{'operationId':'api'})
                journal.close();journal=ExecutionJournal(path)
                row=journal.snapshot('job')[0]
                self.assertEqual(row['status'],'UNKNOWN')
                self.assertEqual(row['operations']['api']['remoteId'],'response-owned')
                with self.assertRaises(UnresolvedExecution):
                    journal.admit('NEW-JOB','project','chapter','shot','qc',8,'changed',{},[])
                journal.close()

    def test_restart_before_remote_admission_can_retry_without_replay(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'executions.sqlite3';journal=ExecutionJournal(path)
            old=journal.admit('job','project','chapter','shot','qc',2,'input',{})
            journal.close();journal=ExecutionJournal(path)
            self.assertEqual(journal.snapshot('job')[0]['status'],'INTERRUPTED')
            new=journal.admit('job','project','chapter','shot','qc',2,'input',{})
            self.assertNotEqual(new.identity,old.identity)
            journal.close()

    def test_known_result_and_completed_publication_are_reusable(self):
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'executions.sqlite3';journal=ExecutionJournal(path)
            context=journal.admit('job','project',None,None,'direction',0,'input',{})
            journal.observe(context.identity,'submitting',{'operationId':'api'})
            journal.observe(context.identity,'completed',{'operationId':'api'})
            self.assertEqual(journal.finish(context.identity,'COMPLETE'),'COMPLETE')
            journal.close();journal=ExecutionJournal(path)
            self.assertFalse(journal.unresolved_project('project'));journal.close()

    def test_cancel_after_submission_retains_unknown_and_duplicate_admission_fails(self):
        with tempfile.TemporaryDirectory() as root:
            journal=ExecutionJournal(Path(root)/'executions.sqlite3')
            context=journal.admit('job','project',None,'shot','image',0,'input',{})
            journal.observe(context.identity,'submitting',{'operationId':'remote'})
            with self.assertRaisesRegex(RuntimeError,'Duplicate'):
                journal.observe(context.identity,'submitting',{'operationId':'remote'})
            self.assertEqual(journal.finish(context.identity,'CANCELLED'),'UNKNOWN')
            journal.close()

    def test_single_commit_thread_preserves_all_project_mutations_and_trace_parents(self):
        with tempfile.TemporaryDirectory() as root:
            coordinator=CommitCoordinator();native=ProjectStore(root)
            project=native.save(new_project())
            store=CoordinatedStore(native,coordinator)
            original=ProductionTrace(native.root/'trace.sqlite3');trace=CoordinatedTrace(original,coordinator)
            threads=set()
            def job(index):
                with trace.span(project['id'],'parent '+str(index)):
                    with trace.span(project['id'],'child '+str(index)):
                        def save(p):
                            threads.add(threading.current_thread().name)
                            p.setdefault('savedIndexes',[]).append(index)
                        store.mutate(project['id'],save)
            with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(job,range(20)))
            self.assertEqual(threads,{'studio-commits'})
            self.assertEqual(sorted(native.load(project['id'])['savedIndexes']),list(range(20)))
            spans=trace.report(project['id'])['spans'];byid={row['id']:row for row in spans}
            children=[row for row in spans if row['stage'].startswith('child')]
            self.assertEqual(len(children),20)
            for child in children:self.assertEqual(byid[child['parent']]['stage'],'parent '+child['stage'].split()[-1])
            self.assertTrue(all(row['status']=='COMPLETE' for row in spans))
            coordinator.call(original.close);coordinator.close()

    def test_image_lane_serializes_requests_and_prioritizes_waiting_repairs(self):
        lane=ImageLane();first=threading.Event();release=threading.Event();order=[]
        def run(label,repair=False):
            with lane.acquire(lambda *_:None,repair):
                order.append(label)
                if label=='active':first.set();self.assertTrue(release.wait(3))
        with ThreadPoolExecutor(max_workers=3) as pool:
            active=pool.submit(run,'active');self.assertTrue(first.wait(2))
            fresh=pool.submit(run,'fresh')
            while len(lane.waiters)<1:time.sleep(.001)
            repair=pool.submit(run,'repair',True)
            while len(lane.waiters)<2:time.sleep(.001)
            release.set()
            for future in (active,fresh,repair):future.result(3)
        self.assertEqual(order,['active','repair','fresh'])

    def test_cancelled_waiter_releases_ticket_without_cancelling_active_request(self):
        lane=ImageLane();active=threading.Event();release=threading.Event()
        def owner():
            with lane.acquire(lambda *_:None):active.set();release.wait(3)
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(owner);self.assertTrue(active.wait(2))
            def cancelled(*_):raise ValueError('only this waiter cancelled')
            with self.assertRaisesRegex(ValueError,'only this'):
                with lane.acquire(cancelled):pass
            self.assertTrue(lane.active);self.assertFalse(lane.waiters)
            release.set();future.result(3)
        self.assertFalse(lane.active)

    def test_pool_applies_backpressure_and_surfaces_failure_before_new_work(self):
        pool=BoundedExecutions(2,lambda *_:None)
        block=threading.Event()
        def work():block.wait(2)
        pool.submit(work);pool.submit(work)
        waiting=threading.Event()
        with ThreadPoolExecutor(max_workers=1) as thread:
            def submit():waiting.set();return pool.submit(lambda:None)
            future=thread.submit(submit);self.assertTrue(waiting.wait(1))
            self.assertFalse(future.done());self.assertEqual(len(pool.pending),2)
            block.set();future.result(3);pool.drain()
        failure=pool.submit(lambda:1/0)
        while not failure.done():time.sleep(.001)
        with self.assertRaises(ZeroDivisionError):pool.submit(lambda:None)
        pool.close()


if __name__=='__main__':unittest.main()
