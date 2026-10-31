"""Dense image permits, independent real QC adapters and durable recovery guards."""
import base64
import copy
import io
import json
import shutil
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import studio_overlap_test as fixtures
from studio_data import digest,get_shot,uid
from studio_deferred_qc import DeferredQC,DenseReviews,capacity_preflight,qc_snapshot
from studio_overlap import CloudStoryOverlap


class DenseProvider:
    id='comfyui';client_capacity=4;model_copies=2
    def __init__(self):self.requests=[];self.lock=threading.Lock();self.sixth=threading.Event()
    def unload(self):pass
    def getCapabilities(self):return {'maxReferenceImages':2,'supportsImageEditing':True,'supportsStyleReference':True,'supportsNegativePrompt':False}
    def validateSettings(self,settings,**kwargs):return copy.deepcopy(settings)
    def generateImage(self,request,checkpoint):
        with self.lock:index=len(self.requests);self.requests.append(copy.deepcopy({k:v for k,v in request.items() if not k.startswith('_')}))
        observer=request['_executionObserver'];operation='dense-image-'+str(index)
        observer('submitting',{'operationId':operation,'provider':'comfyui'})
        observer('accepted',{'operationId':operation,'remoteId':'dense-prompt-'+str(index)})
        if getattr(self,'before_complete',None):self.before_complete(index,checkpoint)
        observer('completed',{'operationId':operation})
        if index==5:self.sixth.set()
        return {'pil':Image.new('RGB',(16,16),(index+1,70,100)),
            'model':request['settings']['model'],'settings':request['settings'],'remotePromptId':'dense-prompt-'+str(index)}


class DurableQueueTest(unittest.TestCase):
    def descriptor(self,**changes):
        return {'project':'project','chapter':'chapter','shot':'shot','path':'saved.png',
            'imageSHA256':'1'*64,'spec':'2'*64,'policy':'3'*64,'parent':'execution','revision':1,**changes}

    def test_pending_restart_dedup_and_unknown_claim_reject_replay(self):
        with tempfile.TemporaryDirectory() as folder:
            queue=DeferredQC(folder,'parent');identity=queue.enqueue(self.descriptor())
            self.assertEqual(queue.enqueue(self.descriptor(revision=2,parent='new-parent')),identity)
            queue.close();queue=DeferredQC(folder,'parent')
            self.assertEqual(queue.pending()[0],identity);self.assertTrue(queue.claim(identity,'actual-execution'))
            queue.close()
            with self.assertRaisesRegex(RuntimeError,'unresolved execution'):DeferredQC(folder,'parent')
            recovered=DeferredQC(folder,'parent',recover=lambda task,execution:execution=='actual-execution')
            try:self.assertEqual(recovered.snapshot()['counts'],{'COMPLETE':1});self.assertIsNone(recovered.pending())
            finally:recovered.close()

    def test_full_burst_707_and_existing_repair_ceiling_fit_and_disk_failure_is_early(self):
        shots=[{'generationSettings':{'width':1280,'height':720}} for _ in range(707)]
        with patch('studio_deferred_qc.shutil.disk_usage',return_value=SimpleNamespace(free=13*2**30)):
            proof=capacity_preflight({},shots,'.',2)
        self.assertEqual(proof['maximumDescriptors'],2121)
        self.assertLess(proof['requiredFreeBytes'],13*2**30)
        with patch('studio_deferred_qc.shutil.disk_usage',return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError,'free disk'):capacity_preflight({},shots,'.',2)
        with self.assertRaisesRegex(ValueError,'descriptor capacity'):capacity_preflight({},shots,'.',20)

    def test_descriptor_payload_bound_does_not_store_images_or_snapshots(self):
        with tempfile.TemporaryDirectory() as folder:
            queue=DeferredQC(folder,'parent')
            try:
                with self.assertRaisesRegex(ValueError,'metadata allowance'):
                    queue.enqueue(self.descriptor(path='x'*5000))
                self.assertEqual(queue.snapshot()['counts'],{})
            finally:queue.close()

    def test_failed_settled_descriptor_requires_intentional_new_parent(self):
        with tempfile.TemporaryDirectory() as folder:
            first=DeferredQC(folder,'old-job');identity=first.enqueue(self.descriptor())
            self.assertTrue(first.claim(identity,'settled-failed'));first.finish(identity,'FAILED');first.close()
            first=DeferredQC(folder,'old-job')
            try:self.assertEqual(first.snapshot()['counts'],{'FAILED':1});self.assertIsNone(first.pending())
            finally:first.close()
            new=DeferredQC(folder,'explicit-new-job')
            try:self.assertEqual(new.snapshot()['counts'],{});self.assertIsNotNone(new.enqueue(self.descriptor()))
            finally:new.close()


class DenseProfileTest(unittest.TestCase):
    def test_capability_defaults_only_eligible_managed_workflow_and_preserves_voice(self):
        from studio_data import new_project
        from studio_planner_profile import planner_profile
        from studio_performance import profile_for
        p=new_project();p['settings'].update(unattendedProduction=True)
        p['settings']['image']['provider']='comfyui';p['settings']['director'].update(provider='openai-luna',parallelism=8)
        voice=profile_for('Narration',p,{})
        with patch('studio_gpu_broker.gpu_configured',return_value=True):
            result=planner_profile(p,config={'experimentalAdaptiveImageAdmission':True})
            self.assertEqual(result['generationStrategy'],'align');self.assertTrue(result['denseImagePhase']['eligible'])
            self.assertEqual(result['denseImagePhase']['qcTaskSlots'],4)
            p['generationStrategy']='dense'
            self.assertEqual(planner_profile(p,config={'experimentalAdaptiveImageAdmission':True})['imageAdmission']['maximumAfterVerification'],5)
            p['generationStrategy']='align'
            self.assertEqual(planner_profile(p)['generationStrategy'],'align')
        del p['generationStrategy']
        with patch('studio_gpu_broker.gpu_configured',return_value=False):
            self.assertEqual(planner_profile(p)['generationStrategy'],'align')
        p['generationStrategy']='dense'
        self.assertEqual(profile_for('Narration',p,{}),voice)
        self.assertEqual(profile_for('Visual QC',p,{})['reviewAPISlots'],4)
        self.assertIn('imagePhasePolicy',profile_for('Image generation',p,{}))


class DensePhaseTest(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown

    def prepared(self,count=8):
        # These historical tests exercise the explicit stop-before-QC policy.
        self.service.config['studioDenseLeaseContinuity']=False
        self.service.wait_for_overlap=False
        self.service.store.mutate(self.project['id'],lambda p:p['settings']['director'].update(parallelism=1,executionMode='classic'))
        self.provider=DenseProvider();self.service.providers['comfyui']=self.provider
        self.service.analyze(self.service.store.load(self.project['id']),self.project['chapters'][0]['id'],{'managedPipeline':True})
        self.service.confirm_chapter_people(self.project['id'],self.project['chapters'][0]['id'])
        def ready(p):
            p['chapters']=p['chapters'][:1];p['settings'].update(unattendedProduction=True,automaticRepair=False)
            p['settings']['director'].update(parallelism=4,executionMode='staged-lean')
            p['generationStrategy']='dense';p['production']={'generationStrategy':'dense'}
            scene=p['chapters'][0]['scenes'][0];base=scene['shots'][0]
            scene['shots']=[{**copy.deepcopy(base),'id':uid('shot-')} for _ in range(count)]
            p['characters'][0]['references']=[{'path':'canonical.png','kind':'face'}]
        self.service.store.mutate(self.project['id'],ready)
        Image.new('RGB',(16,16),'gray').save(self.service.store.folder(self.project['id'])/'canonical.png')
        self.project=self.service.store.load(self.project['id'])
        stopped=threading.Event();service=self.service
        class Rental:
            def __init__(self):self.state={'status':'STOPPED'};self.starts=0;self.stops=0
            def ensure(rental):
                if rental.state['status']=='RUNNING':return
                self.assertTrue(service.store.load(self.project['id'])['production'].get('denseImagePreflight'))
                rental.state={'status':'RUNNING'};rental.starts+=1
            def check(rental):pass
            def stop(rental,reason):
                if rental.state['status']=='RUNNING':rental.stops+=1
                rental.state={'status':'STOPPED'};stopped.set()
        self.rental=Rental();self.service._production_gpu=self.rental;self.stopped=stopped
        overlap=CloudStoryOverlap(self.service,self.project,{'overlap':True,'generationStrategy':'dense'})
        return overlap

    @staticmethod
    def response(index,passed=True):
        value={'pass':passed,'issues':[] if passed else ['Wrong essential weapon.'],
            'findings':[] if passed else [{'issue':'Wrong essential weapon.','severity':'major'}],
            'repairPrompt':'' if passed else 'Correct the weapon.','action':'pass' if passed else 'image_edit'}
        events=[{'type':'response.created','response':{'id':'dense-qc-'+str(index)}},
            {'type':'response.output_text.delta','delta':json.dumps(value)},
            {'type':'response.completed','response':{'id':'dense-qc-'+str(index),'status':'completed','service_tier':'default',
                'usage':{'input_tokens':100,'output_tokens':25}}}]
        return io.BytesIO(b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events))

    def test_sixth_image_runs_while_four_paid_qcs_block_and_gpu_stops_before_tail(self):
        overlap=self.prepared();release=threading.Event();qc_started=threading.Event();lock=threading.Lock();seen=[]
        def response(request,**kwargs):
            with lock:index=len(seen);seen.append(json.loads(request.data))
            raw=self.response(index).getvalue()
            class Response(io.BytesIO):
                def __iter__(stream):
                    qc_started.set();self.assertTrue(release.wait(8),'QC release fixture timed out')
                    yield from io.BytesIO(raw)
            return Response(raw)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.run_dense_image_phase,self.project['id'])
                try:
                    self.assertTrue(qc_started.wait(4));self.assertTrue(self.provider.sixth.wait(4))
                    self.assertTrue(self.stopped.wait(4));self.assertFalse(future.done())
                    self.assertEqual(len(self.provider.requests),8);self.assertLessEqual(len(seen),4)
                    self.assertEqual(self.service.renderer.full_calls,0)
                finally:release.set()
                future.result(timeout=10)
            self.assertEqual(len(seen),8);self.assertEqual(self.rental.starts,1);self.assertEqual(self.rental.stops,1)
            self.assertTrue(all(get_shot(self.service.store.load(self.project['id']),self.project['chapters'][0]['id'],s['id'])['qc']['pass'] for s in self.project['chapters'][0]['scenes'][0]['shots']))
            self.assertEqual(overlap.api_lane.capacity,4);self.assertEqual(overlap.pool.maximum,5)
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'COMPLETE':8})
        finally:release.set();overlap.close('COMPLETE')

    def test_invalid_plan_or_missing_reference_buys_no_image_or_rental(self):
        for defect in ('plan','reference'):
            overlap=self.prepared(1)
            try:
                self.service.store.mutate(self.project['id'],lambda p:
                    p['chapters'][0]['handoff'].update(sourceSignature='stale') if defect=='plan' else p['characters'][0].update(references=[]))
                with patch('studio_service.require_shot',return_value={'status':'PASSED'}):
                    with self.assertRaisesRegex(ValueError,'chapter plan|canonical references'):
                        overlap.main.run_dense_image_phase(self.project['id'])
                self.assertEqual(self.provider.requests,[]);self.assertEqual(self.rental.starts,0)
            finally:overlap.close('FAILED')

    def test_missing_style_or_manual_reference_fails_before_gpu_start(self):
        for kind in ('style','manual'):
            overlap=self.prepared(1)
            def missing(p):
                if kind=='style':p['styleReferences']=[{'path':'missing-style.png'}]
                else:
                    shot=p['chapters'][0]['scenes'][0]['shots'][0]
                    shot['manual']={'referenceImages':True};shot['referenceImages']=[{'path':'missing-manual.png'}]
            self.service.store.mutate(self.project['id'],missing)
            try:
                with patch('studio_service.require_shot',return_value={'status':'PASSED'}):
                    with self.assertRaisesRegex(ValueError,'every selected'):
                        overlap.main.run_dense_image_phase(self.project['id'])
                self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
            finally:overlap.close('FAILED')

    def test_two_existing_repair_rounds_preserve_retry_ceiling_and_stop_before_rechecks(self):
        overlap=self.prepared(1)
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(automaticRepair=True,maxImageRetries=2))
        # Recreate the owner after this explicit fixture setting change.
        overlap.close('COMPLETE');self.project=self.service.store.load(self.project['id'])
        overlap=CloudStoryOverlap(self.service,self.project,{'overlap':True,'generationStrategy':'dense'})
        seen=[]
        def response(request,**kwargs):
            index=len(seen);seen.append(index)
            return self.response(index,passed=index>=2)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('studio_service.repair_budget_reason',return_value=None), \
                 patch('studio_qc.repair_budget_reason',return_value=None), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                overlap.main.run_dense_image_phase(self.project['id'])
            self.assertEqual(len(self.provider.requests),3);self.assertEqual(len(seen),3)
            self.assertEqual(self.rental.starts,3);self.assertEqual(self.rental.stops,3)
            shot=self.service.store.load(self.project['id'])['chapters'][0]['scenes'][0]['shots'][0]
            self.assertEqual(shot['automaticRepairAttempts'],2);self.assertTrue(shot['qc']['pass'])
        finally:overlap.close('COMPLETE')

    def test_saved_completed_review_recovers_claimed_descriptor_without_another_purchase(self):
        overlap=self.prepared(1)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.response(0)) as request:
                overlap.main.run_dense_image_phase(self.project['id'])
                old=overlap.deferred_qc;old.stop_admission();old.pool.shutdown(wait=True)
                old.queue.db.execute("UPDATE tasks SET status='CLAIMED'");old.queue.db.commit();old.queue.close()
                overlap.deferred_qc=DenseReviews(overlap)
                overlap.deferred_qc.drain();self.assertEqual(request.call_count,1)
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'COMPLETE':1})
        finally:overlap.close('COMPLETE')

    def test_failed_or_stale_pending_resumed_spool_blocks_before_rental(self):
        for state in ('FAILED','PENDING'):
            overlap=self.prepared(1)
            shot=self.project['chapters'][0]['scenes'][0]['shots'][0]
            task={'project':self.project['id'],'chapter':self.project['chapters'][0]['id'],'shot':shot['id'],
                'path':'missing-old.png','imageSHA256':'1'*64,'spec':'2'*64,'policy':'3'*64,
                'parent':'earlier-image','revision':self.project['revision']}
            queue=DeferredQC(self.service.store.folder(self.project['id']),overlap.parent)
            identity=queue.enqueue(task)
            if state=='FAILED':queue.claim(identity,'settled');queue.finish(identity,'FAILED')
            queue.close()
            try:
                with patch('studio_service.require_shot',return_value={'status':'PASSED'}):
                    with self.assertRaises((RuntimeError,ValueError,FileNotFoundError)):
                        overlap.main.run_dense_image_phase(self.project['id'])
                self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
            finally:overlap.close('FAILED')
            # A separate intentional parent owns its independent recovery path.
            self.service.current=uid('explicit-new-job-')

    def test_strict_saved_pending_image_resumes_qc_without_regeneration_or_rental(self):
        overlap=self.prepared(1)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.response(0)):
                overlap.main.run_dense_image_phase(self.project['id'])
        finally:overlap.close('COMPLETE')
        def strict_pending(p):
            p['settings'].update(qcCheckLevel='strict')
            p['chapters'][0]['scenes'][0]['shots'][0]['qc']={'status':'PENDING','pass':None}
        self.service.store.mutate(self.project['id'],strict_pending)
        self.project=self.service.store.load(self.project['id']);previous_starts=self.rental.starts
        self.service.current=uid('new-explicit-job-')
        overlap=CloudStoryOverlap(self.service,self.project,{'overlap':True,'generationStrategy':'dense'})
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.response(1)) as request:
                overlap.main.run_dense_image_phase(self.project['id'])
                self.assertEqual(request.call_count,1)
            self.assertEqual(len(self.provider.requests),1);self.assertEqual(self.rental.starts,previous_starts)
            self.assertTrue(self.service.store.load(self.project['id'])['chapters'][0]['scenes'][0]['shots'][0]['qc']['pass'])
        finally:overlap.close('COMPLETE')

    def test_unknown_qc_receipt_retains_liability_stops_rental_and_blocks_rendering(self):
        overlap=self.prepared(1)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=io.BytesIO(
                    b'data: {"type":"response.created","response":{"id":"unknown-dense"}}\n\n')) as request:
                with self.assertRaises(RuntimeError):overlap.main.run_dense_image_phase(self.project['id'])
                self.assertEqual(request.call_count,1)
            state=json.loads((self.service.store.folder(self.project['id'])/'api-cost-ledger.json').read_text())
            self.assertEqual(len(state['requests']),1);self.assertGreater(state['reservedUSD'],0)
            self.assertEqual(next(iter(state['requests'].values()))['status'],'UNKNOWN')
            self.assertEqual(self.rental.state['status'],'STOPPED');self.assertEqual(self.service.renderer.full_calls,0)
        finally:overlap.close('FAILED')

    def test_stale_during_qc_preserves_manual_change_and_cannot_certify_current_shot(self):
        overlap=self.prepared(1);shot=self.project['chapters'][0]['scenes'][0]['shots'][0]
        def response(*args,**kwargs):
            self.service.store.mutate(self.project['id'],lambda p:get_shot(p,self.project['chapters'][0]['id'],shot['id']).update(prompt='Manual newer direction'))
            return self.response(0)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                with self.assertRaisesRegex(ValueError,'superseded|changed'):overlap.main.run_dense_image_phase(self.project['id'])
            current=get_shot(self.service.store.load(self.project['id']),self.project['chapters'][0]['id'],shot['id'])
            self.assertEqual(current['prompt'],'Manual newer direction');self.assertIsNot(current['qc']['pass'],True)
            self.assertEqual(self.rental.state['status'],'STOPPED')
        finally:overlap.close('FAILED')

    def test_qc_failure_stops_new_admission_but_accepted_images_poll_to_completion_before_stop(self):
        overlap=self.prepared(8);accepted=threading.Event();release=threading.Event();completed=[]
        def held(index,checkpoint):
            if index:
                accepted.set();self.assertTrue(release.wait(8))
                checkpoint(0,4,'Collect already accepted image')
                completed.append(index)
        self.provider.before_complete=held
        original_stop=self.rental.stop
        def stopped(reason):
            self.assertEqual(len(completed),len(self.provider.requests)-1)
            original_stop(reason)
        self.rental.stop=stopped
        def failed_response(*args,**kwargs):
            self.assertTrue(accepted.wait(4))
            return io.BytesIO(b'data: {"type":"response.created","response":{"id":"uncertain-qc"}}\n\n')
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=failed_response),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.run_dense_image_phase,self.project['id'])
                try:
                    limit=time.monotonic()+5
                    while time.monotonic()<limit and not (overlap.deferred_qc and overlap.deferred_qc.error):time.sleep(.01)
                    self.assertIsNotNone(overlap.deferred_qc.error)
                    admitted=len(self.provider.requests);self.assertGreater(admitted,1)
                    self.assertFalse(self.stopped.is_set());self.assertFalse(future.done())
                finally:release.set()
                with self.assertRaises(RuntimeError):future.result(timeout=8)
                self.assertEqual(len(self.provider.requests),admitted)
            rows=self.service.execution_journal.snapshot(overlap.parent)
            image_rows=[row for row in rows if row['kind']=='image-generation']
            self.assertFalse(any(op['state']=='SUBMITTED' for row in image_rows for op in row['operations'].values()))
            self.assertFalse(any(row['status']=='UNKNOWN' for row in image_rows))
            self.assertTrue(self.stopped.is_set())
        finally:release.set();overlap.close('FAILED')

    def test_accepted_chapter_renders_while_unrelated_intro_qc_tail_is_blocked(self):
        overlap=self.prepared(1);release=threading.Event();intro_started=threading.Event();rendered=threading.Event()
        self.service.store.mutate(self.project['id'],lambda p:p['intro'].update(enabled=True,placement='full_story_only',shots=[
            {**copy.deepcopy(p['chapters'][0]['scenes'][0]['shots'][0]),'id':uid('intro-'),'chapterId':None,'narrationSegment':'Intro fixture narration'}]))
        original=overlap.main.render_production_chapter
        def render(*args):rendered.set();return {'fixture':True}
        # Every clone inherits the real entrypoint, so observe it at class scope.
        def response(request,**kwargs):
            body=json.loads(request.data)
            content=json.loads(body['input'][1]['content'][0]['text'])
            is_intro=content['expectedShot']['narrationSegment']=='Intro fixture narration'
            raw=self.response(1 if is_intro else 0).getvalue()
            class Response(io.BytesIO):
                def __iter__(stream):
                    if is_intro:intro_started.set();self.assertTrue(release.wait(8))
                    yield from io.BytesIO(raw)
            return Response(raw)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('studio_service.StudioService.render_production_chapter',side_effect=render), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.run_dense_image_phase,self.project['id'])
                try:
                    self.assertTrue(intro_started.wait(4));self.assertTrue(rendered.wait(4));self.assertFalse(future.done())
                finally:release.set()
                future.result(timeout=8)
            self.assertEqual(len(overlap.render_futures),1)
        finally:release.set();overlap.close('COMPLETE')

    def test_unknown_journal_after_valid_qc_never_admits_early_render(self):
        overlap=self.prepared(1);journal=self.service.execution_journal;finish=journal.finish
        def unresolved(identity,status,message=''):
            row=next(r for r in journal.snapshot(overlap.parent) if r['id']==identity)
            return finish(identity,'UNKNOWN' if row['kind']=='deferred-qc' else status,message)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.response(0)), \
                 patch.object(journal,'finish',side_effect=unresolved):
                with self.assertRaises(RuntimeError):overlap.main.run_dense_image_phase(self.project['id'])
            self.assertEqual(overlap.render_futures,{})
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'UNKNOWN':1})
            self.assertEqual(self.rental.state['status'],'STOPPED')
        finally:overlap.close('FAILED')

    def test_other_completed_qc_cannot_render_before_sibling_journal_unknown_is_observed(self):
        overlap=self.prepared(2);journal=self.service.execution_journal;finish=journal.finish
        unknown_shot=self.project['chapters'][0]['scenes'][0]['shots'][0]['id']
        release=threading.Event();unknown_ready=threading.Event();probed=threading.Event();original=overlap.schedule_render
        bound=DenseReviews._bound;counts={};count_lock=threading.Lock();calls=[]
        def held_bound(reviews,task,current=None):
            result=bound(reviews,task,current)
            if current is None:
                with count_lock:counts[task['shot']]=counts.get(task['shot'],0)+1;number=counts[task['shot']]
                if task['shot']==unknown_shot and number==2:
                    unknown_ready.set();self.assertTrue(release.wait(8))
            return result
        def unresolved(identity,status,message=''):
            row=next(r for r in journal.snapshot(overlap.parent) if r['id']==identity)
            return finish(identity,'UNKNOWN' if row['kind']=='deferred-qc' and row['shot']==unknown_shot else status,message)
        def probe(*args):
            qc_worker=threading.current_thread().name.startswith('studio-dense-qc')
            if qc_worker:self.assertTrue(unknown_ready.wait(4))
            result=original(*args)
            if qc_worker:probed.set();release.set()
            return result
        def response(*args,**kwargs):
            with count_lock:index=len(calls);calls.append(index)
            return self.response(index)
        overlap.schedule_render=probe
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response), \
                 patch.object(DenseReviews,'_bound',held_bound),patch.object(journal,'finish',side_effect=unresolved):
                with self.assertRaises(RuntimeError):overlap.main.run_dense_image_phase(self.project['id'])
            self.assertTrue(probed.is_set());self.assertEqual(overlap.render_futures,{})
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'].get('UNKNOWN'),1)
        finally:release.set();overlap.close('FAILED')

    def test_qc_barrier_waits_for_last_early_render_admission_callback(self):
        overlap=self.prepared(1);entered=threading.Event();release=threading.Event();admitted=threading.Event()
        original=overlap.schedule_render
        def held(chapter,index,chapters):
            # Chapter preparation may probe admission before QC. Hold only its
            # final accepted-review callback, with no remaining other work.
            current=self.service.store.load(self.project['id'])
            shot=current['chapters'][0]['scenes'][0]['shots'][0]
            if shot.get('qc',{}).get('pass') is True:
                entered.set();self.assertTrue(release.wait(8));original(chapter,index,chapters);admitted.set()
            else:original(chapter,index,chapters)
        overlap.schedule_render=held
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.response(0)),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.run_dense_image_phase,self.project['id'])
                try:
                    self.assertTrue(entered.wait(4));self.assertTrue(self.stopped.wait(4))
                    self.assertFalse(future.done());self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'CLAIMED':1})
                finally:release.set()
                future.result(timeout=8);self.assertTrue(admitted.is_set())
            self.assertEqual(len(overlap.render_futures),1)
        finally:release.set();overlap.close('COMPLETE')

    def test_intro_images_each_receive_one_check_and_share_the_initial_burst(self):
        overlap=self.prepared(1)
        self.service.store.mutate(self.project['id'],lambda p:p['intro'].update(enabled=True,shots=[
            {**copy.deepcopy(p['chapters'][0]['scenes'][0]['shots'][0]),'id':uid('intro-'),'chapterId':None}
            for _ in range(2)]))
        seen=[]
        def response(*args,**kwargs):
            index=len(seen);seen.append(index);return self.response(index)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                overlap.main.run_dense_image_phase(self.project['id'])
            self.assertEqual(len(self.provider.requests),3);self.assertEqual(len(seen),3)
            self.assertEqual(self.rental.starts,1);self.assertEqual(self.rental.stops,1)
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'COMPLETE':3})
        finally:overlap.close('COMPLETE')


if __name__=='__main__':unittest.main()
