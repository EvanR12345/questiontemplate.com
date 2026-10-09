import contextvars
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from director_tasks import bounded_director_map,validate_director_result


class IndependentDirectorTasksTest(unittest.TestCase):
    def test_results_keep_input_order_with_three_independent_active_tasks(self):
        barrier=threading.Barrier(3)
        lock=threading.Lock()
        active=peak=0
        def execute(item, stopped):
            nonlocal active,peak
            with lock:
                active+=1;peak=max(peak,active)
            barrier.wait(timeout=3)
            with lock:active-=1
            return item*10
        result=bounded_director_map(range(6),execute,lambda *_:None,3)
        self.assertEqual(result,[0,10,20,30,40,50]);self.assertEqual(peak,3)

    def test_failure_stops_admission_and_drains_already_started_work(self):
        started=threading.Barrier(3)
        completed=[];admitted=[]
        def execute(item, stopped):
            admitted.append(item);started.wait(timeout=3)
            if item==0:raise ValueError('Invalid first plan')
            self.assertTrue(stopped.wait(3))
            completed.append(item);return item
        with self.assertRaisesRegex(ValueError,'Invalid first plan'):
            bounded_director_map(range(1000),execute,lambda *_:None,3)
        self.assertEqual(set(admitted),{0,1,2});self.assertEqual(set(completed),{1,2})

    def test_cancellation_before_dispatch_admits_no_work(self):
        calls=[]
        def gate(*_):raise RuntimeError('Cancelled before paid dispatch')
        with self.assertRaisesRegex(RuntimeError,'Cancelled'):
            bounded_director_map(range(10),lambda item,stop:calls.append(item),gate,3)
        self.assertEqual(calls,[])

    def test_serial_mode_is_supported_and_unbounded_fanout_is_rejected(self):
        self.assertEqual(bounded_director_map([1,2],lambda item,stop:item,lambda *_:None,1),[1,2])
        for limit in (0,4,True,1.5):
            with self.assertRaises(ValueError):
                bounded_director_map([],lambda *_:None,lambda *_:None,limit)

    def test_trace_context_is_inherited_without_leaking_worker_changes(self):
        context=contextvars.ContextVar('test-director-parent',default='none')
        context.set('chapter-parent')
        def execute(item, stopped):
            value=context.get();context.set('child-'+str(item));return value
        self.assertEqual(bounded_director_map(range(8),execute,lambda *_:None,3),['chapter-parent']*8)
        self.assertEqual(context.get(),'chapter-parent')

    def test_invalid_workflow_is_rejected_before_downstream_serial_calls(self):
        context={'available':[{'provider':'comfyui','models':['klein'],'workflows':['reference']} ]}
        valid={'provider':'comfyui','model':'klein','workflow':'reference'}
        self.assertEqual(validate_director_result('selectImageWorkflow',context,valid),valid)
        for field in ('provider','model','workflow'):
            with self.assertRaises(ValueError):
                validate_director_result('selectImageWorkflow',context,valid | {field:'unavailable'})

    def test_prompt_batch_requires_one_nonempty_result_for_each_frozen_shot(self):
        context={'shots':[{},{}]}
        valid={'prompts':[{'shotIndex':1,'prompt':'Second'}, {'shotIndex':0,'prompt':'First'}]}
        self.assertEqual(validate_director_result('writeImagePrompt',context,valid),valid)
        for entries in ([],valid['prompts'][:1],valid['prompts']*2,
                [{'shotIndex':0,'prompt':'First'},{'shotIndex':True,'prompt':'Second'}],
                [{'shotIndex':0,'prompt':'First'},{'shotIndex':1,'prompt':' '}],
                [{'shotIndex':0,'prompt':'First'},{'shotIndex':2,'prompt':'Second'}]):
            with self.assertRaisesRegex(ValueError,'prompt batch'):
                validate_director_result('writeImagePrompt',context,{'prompts':entries})


if __name__=='__main__':unittest.main()
