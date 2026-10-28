"""Local lifecycle tests: no sockets, model calls or rental allocation."""
import copy
import hashlib
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from studio_data import new_project
from studio_director_limits import ADAPTIVE_IMAGE_ADMISSION
from studio_execution import BoundedExecutions, ImageLane
from studio_image_workers import ComfyWorkerPool
from studio_overlap import CloudStoryOverlap
from studio_performance import profile_for


def until(predicate, timeout=3):
    end=time.monotonic()+timeout
    while not predicate():
        if time.monotonic()>end:raise AssertionError('Synthetic lifecycle did not reach its event')
        time.sleep(.001)


def workers():
    provider=ComfyWorkerPool({'comfyModelCopies':2},lambda config:SimpleNamespace(
        invalidate_backend_cache=lambda:None,unload=lambda:None))
    provider.confirm_workers({'gpu':'NVIDIA GeForce RTX 5090',
        'workers':[{'pid':100,'port':8188},{'pid':101,'port':8189}]})
    return provider


def runtime():
    result=object.__new__(CloudStoryOverlap)
    result.root=SimpleNamespace(providers={'comfyui':workers()})
    result.settings={'image':{'provider':'comfyui'},'qcCheckLevel':'practical',
                     'director':{'parallelism':4}}
    result.base_image_tasks=5
    result.adaptive_image_admission=True
    result.image_lane=ImageLane(4)
    result.api_lane=ImageLane(4)
    result.pool=BoundedExecutions(5,lambda *_:None,max_workers=8)
    result.image_tasks=5
    result.admitted_image_windows=set()
    return result


class AdaptivePoolTest(unittest.TestCase):
    def test_default_contract_remains_five_and_growth_requires_explicit_executor_room(self):
        for value in (True,1,6,8):
            with self.assertRaises(ValueError):BoundedExecutions(value,lambda *_:None)
        pool=BoundedExecutions(5,lambda *_:None)
        try:
            with self.assertRaises(ValueError):pool.set_limit(8)
            for value in (True,1,6,float('nan')):
                with self.assertRaises(ValueError):pool.set_limit(value)
        finally:pool.close()
        pool=BoundedExecutions(5,lambda *_:None,max_workers=8)
        try:
            pool.set_limit(8);self.assertEqual(pool.limit,8)
            pool.set_limit(2);self.assertEqual(pool.limit,2)
        finally:pool.close()

    def test_eight_lifecycles_keep_four_image_and_api_lanes_and_retire_any_completion(self):
        pool=BoundedExecutions(5,lambda *_:None,max_workers=8);pool.set_limit(8)
        images=ImageLane(4);api=ImageLane(4)
        releases=[threading.Event() for _ in range(9)]
        seen=[];reviews=[];lock=threading.Lock();peak={'image':0,'api':0}
        payloads=[{'seed':i,'referenceImages':['exact-reference'],'prompt':'fixed grounded scene'} for i in range(9)]
        expected=hashlib.sha256(json.dumps(payloads,sort_keys=True).encode()).hexdigest()
        def work(index):
            with images.acquire(lambda *_:None):
                with lock:seen.append(copy.deepcopy(payloads[index]));peak['image']=max(peak['image'],images.active)
                time.sleep(.005)
            with api.acquire(lambda *_:None):
                with lock:reviews.append(index);peak['api']=max(peak['api'],api.active)
                if not releases[index].wait(4):raise AssertionError('QC release missing')
            return index
        try:
            futures=[pool.submit(work,i) for i in range(8)]
            until(lambda:len(seen)==8 and len(reviews)==4)
            with ThreadPoolExecutor(max_workers=1) as owner:
                ninth=owner.submit(pool.submit,work,8)
                time.sleep(.025)
                self.assertFalse(ninth.done());self.assertEqual(len(pool.pending),8)
                # Release a later QC while the first is still held: no FIFO wait.
                later=reviews[3];self.assertNotEqual(later,reviews[0]);releases[later].set()
                new=ninth.result(3)
                self.assertFalse(futures[reviews[0]].done())
                self.assertLessEqual(len(pool.pending),8)
                for event in releases:event.set()
                pool.drain()
                self.assertEqual(new.result(),8)
            actual=sorted(seen,key=lambda item:item['seed'])
            self.assertEqual(hashlib.sha256(json.dumps(actual,sort_keys=True).encode()).hexdigest(),expected)
            self.assertEqual(sorted(reviews),list(range(9)))
            self.assertLessEqual(peak['image'],4);self.assertEqual(peak['api'],4)
        finally:
            for event in releases:event.set()
            pool.close()

    def test_downgrade_drains_without_dropping_purchased_futures(self):
        pool=BoundedExecutions(5,lambda *_:None,max_workers=8);pool.set_limit(8)
        release=[threading.Event() for _ in range(8)]
        def work(i):
            if not release[i].wait(4):raise AssertionError('Release missing')
            return i
        try:
            originals=[pool.submit(work,i) for i in range(8)]
            pool.set_limit(5)
            with ThreadPoolExecutor(max_workers=1) as owner:
                next_task=owner.submit(pool.submit,lambda:8)
                time.sleep(.025);self.assertFalse(next_task.done())
                for event in release[:3]:event.set()
                time.sleep(.025);self.assertFalse(next_task.done())
                release[3].set();next_task.result(3)
                self.assertTrue(all(f in pool.all for f in originals))
                for event in release:event.set()
                pool.drain();self.assertEqual([f.result() for f in originals],list(range(8)))
        finally:
            for event in release:event.set()
            pool.close()

    def test_completed_failure_and_cancel_prevent_new_callback_at_eight(self):
        pool=BoundedExecutions(5,lambda *_:None,max_workers=8);pool.set_limit(8)
        calls=[]
        try:
            failed=pool.submit(lambda:(_ for _ in ()).throw(RuntimeError('owned failure')))
            until(failed.done)
            with self.assertRaisesRegex(RuntimeError,'owned failure'):pool.submit(lambda:calls.append('new'))
            self.assertFalse(calls)
            pool.gate=lambda *_:(_ for _ in ()).throw(RuntimeError('cancelled'))
            with self.assertRaisesRegex(RuntimeError,'cancelled'):pool.submit(lambda:calls.append('new'))
            self.assertFalse(calls)
        finally:pool.close()


class RuntimeGuardTest(unittest.TestCase):
    def test_live_receipt_and_ram_are_required_and_loss_demotes_locally(self):
        overlap=runtime();provider=overlap.root.providers['comfyui']
        try:
            with patch('studio_snapshot_budget.adaptive_snapshot_room',return_value=True), \
                 patch('studio_render.VideoRenderer.available_memory_bytes',return_value=2**30):
                overlap.update_image_window({});self.assertEqual(overlap.pool.limit,8)
                provider.quarantined=True
                overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5)
                provider.quarantined=False;proof=provider.confirmation
                for value in (None,{},proof | {'gpu':'another GPU'},
                              proof | {'workers':[{'pid':100,'port':8188},{'pid':100,'port':8189}]}):
                    provider.confirmation=value
                    overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5)
                provider.confirmation=proof
                overlap.update_image_window({},review_only=True);self.assertEqual(overlap.pool.limit,5)
            with patch('studio_snapshot_budget.adaptive_snapshot_room',return_value=False), \
                 patch('studio_render.VideoRenderer.available_memory_bytes',return_value=None):
                overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5)
        finally:overlap.pool.close()

    def test_configured_slots_alone_or_strict_checks_never_promote(self):
        overlap=runtime()
        try:
            with patch('studio_snapshot_budget.adaptive_snapshot_room',return_value=True), \
                 patch('studio_render.VideoRenderer.available_memory_bytes',return_value=2**30):
                for kind,value in (('api',3),('image',3)):
                    lane=getattr(overlap,kind+'_lane');lane.capacity=value
                    overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5);lane.capacity=4
                overlap.settings['qcCheckLevel']='strict'
                overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5)
                overlap.settings['qcCheckLevel']='practical'
                overlap.root.providers['comfyui'].confirmation=None
                overlap.update_image_window({});self.assertEqual(overlap.pool.limit,5)
        finally:overlap.pool.close()

    def test_aggregate_metadata_uses_admitted_windows_not_eligibility_probes(self):
        overlap=runtime()
        try:
            overlap.pool.set_limit(8)
            self.assertFalse(overlap.admitted_image_windows)
            overlap.admitted_image_windows.update((5,8));overlap.pool.set_limit(5)
            details=overlap.image_admission_details(aggregate=True)
            self.assertEqual((details['imageWindowMin'],details['imageWindowMax']),(5,8))
            self.assertEqual(details['imageTasks'],8)
        finally:overlap.pool.close()


class LearningIsolationTest(unittest.TestCase):
    def test_five_eight_and_mixed_image_history_are_distinct_without_voice_or_qc_changes(self):
        p=new_project();p['settings']['image']['provider']='comfyui'
        p['settings']['qcCheckLevel']='practical';p['settings']['director']['parallelism']=4
        config={'comfyModelCopies':2}
        five=profile_for('Image pipeline',p,config,{'imageTasks':5})
        eight=profile_for('Image pipeline',p,config,{'imageTasks':8})
        mixed=profile_for('Image pipeline',p,config,{'imageTasks':8,'imageWindowMin':5,'imageWindowMax':8})
        self.assertEqual(five['imageAdmissionPolicy'],ADAPTIVE_IMAGE_ADMISSION)
        self.assertEqual(len({json.dumps(value,sort_keys=True) for value in (five,eight,mixed)}),3)
        legacy={k:v for k,v in five.items() if not k.startswith('imageWindow') and k!='imageAdmissionPolicy'}
        self.assertNotEqual(five,legacy)
        for stage in ('Narration','Visual QC'):
            before=profile_for(stage,p,config)
            after=profile_for(stage,p,config,{'imageTasks':8,'imageAdmissionPolicy':ADAPTIVE_IMAGE_ADMISSION,
                                             'imageWindowMin':5,'imageWindowMax':8})
            self.assertEqual(before,after)


class AdaptiveStoryTest(unittest.TestCase):
    """Run the real full-story/PNG/QC/journal path with synthetic adapters."""
    def setUp(self):
        import studio_overlap_test as fixtures
        fixtures.CloudOverlapTest.setUp(self)
        original=self.provider
        self.provider=ComfyWorkerPool({'comfyModelCopies':2},lambda config:original)
        self.provider.confirm_workers({'gpu':'NVIDIA GeForce RTX 5090',
            'workers':[{'pid':100,'port':8188},{'pid':101,'port':8189}]})
        self.service.providers['comfyui']=self.provider
        self.service.config['comfyModelCopies']=2
        # The reusable classic fixture briefly writes its synthetic storyboard
        # as provider=existing before OverlapHarness normalizes it. A staged
        # project correctly rejects that intermediate fixture state. Normalize
        # precisely that synthetic director output before its normal store
        # validation; production provider/QC/orchestration methods stay real.
        original_mutate=self.service.store.mutate
        def mutate(pid,callback,*args,**kwargs):
            def output(p):
                result=callback(p)
                for chapter in p['chapters']:
                    for scene in chapter['scenes']:
                        for shot in scene['shots']:
                            if (shot.get('imageProvider')=='existing' and shot.get('imageModel')=='sd15'
                                    and shot.get('prompt')=='Mira carries a key'):
                                shot['imageProvider']='comfyui'
                return result
            return original_mutate(pid,output,*args,**kwargs)
        self.service.store.mutate=mutate
        self.service.store.mutate(self.project['id'],lambda p:(
            p['settings'].update(qcCheckLevel='practical'),
            p['settings']['director'].update(executionMode='staged-lean',parallelism=4)))
        self.promotions=[]
        original_set=BoundedExecutions.set_limit
        def change(pool,limit):
            self.promotions.append(limit);return original_set(pool,limit)
        self.patchers=[patch('studio_render.VideoRenderer.available_memory_bytes',return_value=2**30),
                       patch.object(BoundedExecutions,'set_limit',change)]
        for item in self.patchers:item.start()

    def tearDown(self):
        import studio_overlap_test as fixtures
        try:fixtures.CloudOverlapTest.tearDown(self)
        finally:
            for item in reversed(self.patchers):item.stop()

    def response(self,*args,**kwargs):
        import studio_overlap_test as fixtures
        return fixtures.CloudOverlapTest.response(self,*args,**kwargs)

    def run_story(self,**options):
        import studio_overlap_test as fixtures
        return fixtures.CloudOverlapTest.run_story(self,**options)

    def test_verified_real_orchestration_preserves_requests_qc_counts_and_window_identity(self):
        self.run_story()
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE');self.assertIn(8,self.promotions)
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),2)
        for request in self.provider.requests:
            self.assertEqual([request['settings'][k] for k in ('steps','width','height')],[4,1344,768])
            self.assertEqual(len(request['referenceImages']),1)
        rows=[row for row in p['production']['timings'] if row['stage']=='Image + quality checks']
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(row['imageTasks'],8)
            profile=row['performanceProfile']
            self.assertEqual(profile['imageAdmissionPolicy'],ADAPTIVE_IMAGE_ADMISSION)
            self.assertEqual((profile['imageWindowMin'],profile['imageWindowMax']),(8,8))
        pipeline=[row for row in p['production']['timings'] if row['stage']=='Image pipeline']
        self.assertEqual(len(pipeline),1)
        self.assertEqual(pipeline[0]['performanceProfile']['imageWindowMin'],8)
        self.assertFalse(self.service.active_executions)

    def test_explicit_smaller_limit_is_not_automatically_increased(self):
        self.run_story(overlapShots=2)
        self.assertNotIn(8,self.promotions)
        self.assertTrue(all(value==2 for value in self.promotions))
        p=self.service.store.load(self.project['id'])
        rows=[row for row in p['production']['timings'] if row['stage']=='Image + quality checks']
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(row['imageTasks'],2)
            self.assertEqual((row['performanceProfile']['imageWindowMin'],
                              row['performanceProfile']['imageWindowMax']),(2,2))

    def test_stale_manual_edit_is_retained_at_the_promoted_window(self):
        import studio_overlap_test as fixtures
        fixtures.CloudOverlapTest.test_manual_edit_while_qc_is_active_is_preserved_and_not_certified(self)
        self.assertIn(8,self.promotions)

    def test_scoped_cancel_retains_unknown_without_replaying_other_qc(self):
        import studio_overlap_test as fixtures
        fixtures.CloudOverlapTest.test_scoped_cancellation_retains_own_unknown_but_other_submitted_qc_finishes(self)
        self.assertIn(8,self.promotions)


if __name__=='__main__':unittest.main()
