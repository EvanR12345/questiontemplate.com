"""Real full-story/generation/QC orchestration; synthetic remote endpoints only."""
import copy
import io
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from full_video_test import Harness,Renderer
from studio_data import new_project,new_chapter,get_chapter,get_shot
from studio_service import StudioService,JobCancelled
from studio_overlap import CloudStoryOverlap,validate_overlap,NoLocalGeneration
from studio_qc import should_check


class SyntheticComfy:
    id='comfyui'
    def __init__(self,signals):
        self.signals=signals;self.requests=[];self.active=0;self.peak=0
    def unload(self):pass
    def getCapabilities(self):
        return {'maxReferenceImages':2,'supportsImageEditing':True,'supportsStyleReference':True,'supportsNegativePrompt':False}
    def validateSettings(self,settings,**kwargs):return copy.deepcopy(settings)
    def generateImage(self,request,checkpoint):
        self.active+=1;self.peak=max(self.peak,self.active)
        index=len(self.requests);self.requests.append(copy.deepcopy({k:v for k,v in request.items() if not k.startswith('_')}))
        observer=request['_executionObserver'];operation='image-'+str(index)
        observer('submitting',{'operationId':operation,'provider':'comfyui'})
        observer('accepted',{'operationId':operation,'remoteId':'prompt-'+str(index)})
        try:
            if index==0:
                self.signals['firstImage'].set()
                if not self.signals['secondDirection'].wait(3):raise RuntimeError('Direction did not overlap image')
            if index==1:
                if not self.signals['firstQC'].wait(3):raise RuntimeError('QC did not overlap second image')
                self.signals['secondImage'].set()
            checkpoint(0,4,'Synthetic image completed')
            observer('completed',{'operationId':operation})
            return {'pil':Image.new('RGB',(16,16),(20+index*30,70,100)),
                'model':request['settings']['model'],'settings':request['settings'],
                'remotePromptId':'prompt-'+str(index)}
        finally:self.active-=1


class OverlapHarness(Harness):
    def narration(self,p,ch):
        if ch['number']==2 and self.wait_for_overlap:
            if not self.signals['firstImage'].wait(3):raise RuntimeError('Image did not start before next narration')
        return super().narration(p,ch)
    def analyze(self,p,chid,options):
        result=super().analyze(p,chid,options)
        def prepare(q):
            ch=get_chapter(q,chid)
            for scene in ch['scenes']:
                for shot in scene['shots']:
                    shot.update(imageProvider='comfyui',imageModel='klein',workflow='klein',
                        lighting='',action='carries key',pose='',expression='',location='',
                        narrationSegment=ch['sourceText'],continuity={},retryHistory=[],history=[],
                        qc={'status':'UNREVIEWED','pass':None})
                    shot['generationSettings'].update(width=1344,height=768,steps=4)
                    if getattr(self,'planned_change',False) and ch['number']==2:
                        shot['characters'][0]['appearanceState']['outfit']='red coat'
                        shot['intentionalAppearanceChanges']=[{'field':'outfit','after':'red coat','reason':'source narration'}]
            if getattr(self,'extra_unchecked_shot',False):
                scene=ch['scenes'][0]
                extra=copy.deepcopy(scene['shots'][0])
                for index in range(100):
                    extra['id']=scene['shots'][0]['id']+'-sample-'+str(index)
                    if not should_check(q,extra):break
                else:raise AssertionError('No unselected sample fixture found')
                scene['shots'].append(extra)
        self.store.mutate(p['id'],prepare)
        if get_chapter(p,chid)['number']==2:self.signals['secondDirection'].set()
        return result
    def generate(self,*args,**kwargs):return StudioService.generate(self,*args,**kwargs)


class CloudOverlapTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.service=OverlapHarness(self.temp.name,type('Audio',(),{'gpu':'test'})(),lambda:None,
            threading.Lock(),lambda:None,lambda:None,type('Legacy',(),{'control':lambda *args:None})())
        self.service.calls=[];self.service.proposal=False;self.service.fail_chapter=None
        self.service.cancel_after_image=False;self.service.wait_for_overlap=True
        self.signals={key:threading.Event() for key in ('firstImage','secondDirection','firstQC','secondImage')}
        self.service.signals=self.signals
        self.provider=SyntheticComfy(self.signals);self.service.providers['comfyui']=self.provider
        self.service.providers['existing']=NoLocalGeneration()
        self.service.providers['native-flux']=NoLocalGeneration()
        self.service.renderer=Renderer(self.service.store)
        p=new_project();p['chapters'].append(new_chapter(2))
        for chapter in p['chapters']:chapter['sourceText']='Mira carries a brass key.'
        p['settings']['image'].update(provider='comfyui',model='klein',workflow='klein',width=1344,height=768,steps=4)
        p['settings']['director']['provider']='openai-luna'
        p['settings']['visionQC']=True;p['settings']['budget']={'openaiUSD':1}
        p['intro']['enabled']=False
        self.project=self.service.store.save(p)
        self.qc_requests=[];self.failed_qc=False;self.edit_during_qc=False
        self.cancel_during_qc=False
        self.pause_during_qc=False;self.paused=threading.Event()
        self.hold_first_qc=False;self.release_first_qc=threading.Event()

    def tearDown(self):self.service.close();self.temp.cleanup()

    def response(self,request,*args,**kwargs):
        body=json.loads(request.data);index=len(self.qc_requests);self.qc_requests.append(body)
        signals=self.signals;fail=self.failed_qc and index==0
        if self.edit_during_qc and index==0:
            current=self.service.store.load(self.project['id'])
            ch=current['chapters'][0];shot=ch['scenes'][0]['shots'][0]
            self.service.store.mutate(current['id'],lambda q:get_shot(q,ch['id'],shot['id']).update(
                prompt='Manually edited staging',manual={'prompt':True}))
        value={'pass':not fail,'issues':['confirmed defect'] if fail else [],
            'repairPrompt':'Repair the defect' if fail else '', 'action':'regenerate' if fail else 'pass'}
        if fail and hasattr(self,'failure_issue'):value['issues']=[self.failure_issue]
        value['findings']=[{'issue':issue,'severity':getattr(self,'failure_severity','major')}
                           for issue in value['issues']]
        events=[{'type':'response.created','response':{'id':'response-'+str(index)}},
            {'type':'response.output_text.delta','delta':json.dumps(value)},
            {'type':'response.completed','response':{'id':'response-'+str(index),'status':'completed',
                'usage':{'input_tokens':100,'output_tokens':30}}}]
        raw=b''.join(b'data: '+json.dumps(event).encode()+b'\n\n' for event in events)
        class Response(io.BytesIO):
            def __iter__(stream):
                if index==0:
                    signals['firstQC'].set()
                    if not signals['secondImage'].wait(3):raise RuntimeError('Second image did not overlap QC')
                    if self.hold_first_qc and not self.release_first_qc.wait(6):raise RuntimeError('Held QC fixture was not released')
                while True:
                    line=stream.readline()
                    if not line:break
                    if index==0 and self.pause_during_qc and b'response.completed' in line:
                        self.service.control('pause');self.paused.set()
                    yield line
                    if index==0 and self.cancel_during_qc and b'response.created' in line:
                        first_chapter=self.project['chapters'][0]['id']
                        context=next(context for context in self.service.active_executions.values()
                            if context.chapter==first_chapter)
                        self.service.control('cancel-current',job=context.identity)
        return Response(raw)

    def run_story(self,**options):
        with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
             patch('openai_director.OpenAIDirector.key',return_value='sk-synthetic-key'), \
             patch('openai_director.urllib.request.urlopen',side_effect=self.response):
            self.service.produce_story(self.project['id'],{'overlap':True,**options})

    def test_actual_story_overlaps_direction_image_and_qc_without_changing_settings(self):
        self.run_story()
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE');self.assertEqual(self.provider.peak,1)
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),2)
        for request in self.provider.requests:
            self.assertEqual([request['settings'][k] for k in ('steps','width','height')],[4,1344,768])
            self.assertEqual(len(request['referenceImages']),1)
        for chapter in p['chapters']:
            shot=chapter['scenes'][0]['shots'][0]
            self.assertEqual(shot['status'],'PASSED');self.assertTrue(shot['qc']['pass'])
            self.assertTrue(self.service.store.asset(p['id'],shot['imagePath']).is_file())
        executions=self.service.execution_journal.snapshot(next(iter(
            self.service.execution_journal.db.execute('SELECT parent FROM executions')))[0])
        self.assertGreater(len(executions),3);self.assertTrue(all(row['status']=='COMPLETE' for row in executions))
        self.assertEqual(sum(row['kind']=='image-qc' for row in executions),2)
        self.assertEqual(sum(row['kind']=='AI directing' for row in executions),2)
        self.assertFalse(self.service.active_executions)
        ledger=json.loads((self.service.store.folder(p['id'])/'api-cost-ledger.json').read_text())
        self.assertEqual(len(ledger['receipts']),2);self.assertFalse(ledger['requests'])
        self.assertEqual(self.service.renderer.full_calls,1)

    def test_off_checks_overlap_and_resume_without_paid_vision_calls(self):
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        self.signals['firstQC'].set()
        self.run_story()
        self.service.wait_for_overlap=False
        self.run_story()
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertEqual(len(self.provider.requests),2)
        self.assertEqual(self.qc_requests,[])
        for chapter in p['chapters']:
            self.assertEqual(chapter['scenes'][0]['shots'][0]['qc']['status'],'UNCHECKED')

    def test_sampled_overlap_reviews_only_selected_shots_and_reuses_them(self):
        self.service.extra_unchecked_shot=True
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='sampled',qcSampleEvery=20))
        self.run_story()
        self.service.wait_for_overlap=False
        self.run_story()
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertEqual(len(self.provider.requests),4)
        self.assertEqual(len(self.qc_requests),2)
        for chapter in p['chapters']:
            checked,unchecked=chapter['scenes'][0]['shots']
            self.assertTrue(checked['qc']['pass'])
            self.assertEqual(unchecked['qc']['status'],'UNCHECKED')

    def test_qc_failure_runs_existing_automatic_repair_and_checks_the_replacement(self):
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(
            qcPolicy='strict',automaticRepair=True,qcCostEstimate={'baselineControlled':True,
            'includesAPI':True,'includesIncrementalRental':True,'includesRechecks':True,
            'baselineGenerationUSD':1,'combinedQCAndReplacementUpperUSD':.05,'maxImageRetriesCovered':2}))
        self.failed_qc=True;self.run_story()
        self.assertEqual(len(self.provider.requests),3);self.assertEqual(len(self.qc_requests),3)
        repairs=[r for r in self.provider.requests if r['operation']=='edit']
        self.assertEqual(len(repairs),1);self.assertEqual(len(repairs[0]['referenceImages']),1)
        self.assertIn('Repair the defect',repairs[0]['prompt'])
        self.assertEqual(self.provider.peak,1)

    def test_manual_edit_while_qc_is_active_is_preserved_and_not_certified(self):
        self.edit_during_qc=True
        with self.assertRaisesRegex(ValueError,'changed during'):
            self.run_story()
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        self.assertEqual(shot['prompt'],'Manually edited staging')
        self.assertNotEqual(shot['qc'].get('pass'),True)
        self.assertTrue(shot['qcHistory']);self.assertEqual(len(self.provider.requests),2)
        self.assertEqual(self.service.renderer.full_calls,0)

    def test_eligibility_requires_cap_remote_models_and_bounded_slots(self):
        for change in ('cap','provider','fallback','slots'):
            p=copy.deepcopy(self.project);options={'overlap':True}
            if change=='cap':p['settings']['budget']={}
            if change=='provider':p['settings']['director']['provider']='local-qwen'
            if change=='fallback':p['settings']['image']['fallbackEnabled']=True
            if change=='slots':options['overlapShots']=4
            with self.assertRaises(ValueError):validate_overlap(p,options)

    def test_scoped_cancellation_retains_own_unknown_but_other_submitted_qc_finishes(self):
        self.cancel_during_qc=True
        with self.assertRaisesRegex(ValueError,'shot task was cancelled'):self.run_story()
        p=self.service.store.load(self.project['id'])
        second=p['chapters'][1]['scenes'][0]['shots'][0]
        self.assertEqual(second['status'],'PASSED');self.assertTrue(second['qc']['pass'])
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),2)
        ledger=json.loads((self.service.store.folder(p['id'])/'api-cost-ledger.json').read_text())
        self.assertEqual(len(ledger['receipts']),1);self.assertEqual(len(ledger['requests']),1)
        unknown=next(iter(ledger['requests'].values()))
        self.assertEqual(unknown['status'],'UNKNOWN');self.assertEqual(unknown['responseId'],'response-0')
        self.assertEqual(self.service.renderer.full_calls,0)
        with self.assertRaisesRegex(RuntimeError,'unresolved'):self.run_story()
        self.assertEqual(len(self.qc_requests),2)
        with self.assertRaisesRegex(RuntimeError,'unresolved'):
            self.service.enqueue(p['id'],None,'produce-story',options={'overlap':False})

    def test_changed_saved_image_gets_current_qc_without_regenerating_it(self):
        self.run_story();self.service.wait_for_overlap=False
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        path=self.service.store.asset(p['id'],shot['imagePath'])
        Image.new('RGB',(16,16),'green').save(path)
        changed=path.read_bytes()
        self.run_story()
        self.assertEqual(path.read_bytes(),changed)
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),3)
        current=self.service.store.load(p['id'])['chapters'][0]['scenes'][0]['shots'][0]
        self.assertTrue(current['qc']['pass']);self.assertEqual(current['qc']['checkedImagePath'],shot['imagePath'])

    def test_cached_valid_shots_need_no_new_images_or_qc_requests(self):
        self.run_story();self.service.wait_for_overlap=False;self.run_story()
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),2)

    def test_practical_minor_failure_continues_without_paid_repair_or_qc_replay(self):
        self.failed_qc=True;self.failure_issue='The framing is wider than requested.'
        self.failure_severity='advisory'
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(automaticRepair=True))
        self.run_story();self.service.wait_for_overlap=False
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        self.assertFalse(shot['qc']['pass']);self.assertEqual(shot['qc']['status'],'FAILED')
        self.assertEqual(shot['qcDecision']['disposition'],'advisory');self.assertEqual(shot['status'],'COMPLETE')
        self.run_story()
        self.assertEqual(len(self.provider.requests),2);self.assertEqual(len(self.qc_requests),2)
        self.assertEqual(p['production']['status'],'COMPLETE')

    def test_strict_retry_does_not_spend_when_combined_cost_overhead_is_unknown(self):
        self.failed_qc=True
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcPolicy='strict',automaticRepair=True))
        with self.assertRaisesRegex(RuntimeError,'Visual QC'):self.run_story()
        self.assertLessEqual(len(self.provider.requests),2)

    def test_resume_keeps_flagged_saved_image_instead_of_buying_replacement(self):
        self.failed_qc=True
        with self.assertRaisesRegex(RuntimeError,'Visual QC'):self.run_story()
        images=len(self.provider.requests);checks=len(self.qc_requests)
        self.service.wait_for_overlap=False
        with self.assertRaisesRegex(RuntimeError,'No replacement was purchased'):self.run_story()
        self.assertEqual(len(self.provider.requests),images);self.assertEqual(len(self.qc_requests),checks)

    def test_planned_clothing_change_remains_in_generation_and_qc_payloads(self):
        self.service.planned_change=True
        self.service.store.mutate(self.project['id'],lambda q:q['chapters'][1].update(
            sourceText='Mira changes into a red coat and carries a brass key.'))
        self.run_story()
        self.assertIn('red coat',self.provider.requests[1]['prompt'])
        self.assertIn('red coat',json.dumps(self.qc_requests))
        second=self.service.store.load(self.project['id'])['chapters'][1]['scenes'][0]['shots'][0]
        self.assertEqual(second['imageMetadata']['intentionalAppearanceChanges'][0]['after'],'red coat')

    def test_pause_collects_submitted_qc_but_stops_future_qc_until_resume(self):
        self.pause_during_qc=True
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_story)
            try:
                self.assertTrue(self.paused.wait(3))
                deadline=time.monotonic()+3
                while time.monotonic()<deadline:
                    complete=self.service.execution_journal.db.execute(
                        "SELECT count(*) FROM executions WHERE kind='image-qc' AND status='COMPLETE'").fetchone()[0]
                    if complete:break
                    time.sleep(.01)
                self.assertEqual(complete,1);self.assertTrue(self.service.paused)
                self.assertEqual(len(self.qc_requests),1);self.assertFalse(future.done())
            finally:self.service.control('resume')
            future.result(5)
        self.assertEqual(len(self.qc_requests),2)

    def test_narration_finishing_after_source_edit_retains_old_audio_without_selecting_it(self):
        self.service.wait_for_overlap=False
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0]
        def create(text,voice,speed,target,*args):
            Path(target).write_bytes(b'synthetic audio')
            self.service.store.mutate(p['id'],lambda q:get_chapter(q,ch['id']).update(sourceText='Edited narration'))
            return {'path':str(target),'duration':1,'sentences':[]}
        self.service.create_audio=create
        with self.assertRaisesRegex(ValueError,'Narration changed'):
            StudioService.narration(self.service,p,ch)
        current=get_chapter(self.service.store.load(p['id']),ch['id'])
        self.assertEqual(current['sourceText'],'Edited narration')
        self.assertFalse(current.get('audio',{}).get('path'))
        self.assertFalse(current['audioHistory'][0]['selected'])

    def test_direction_lookahead_waits_for_results_older_than_one_chapter(self):
        third=new_chapter(3);third['sourceText']='Mira carries a brass key.'
        self.service.store.mutate(self.project['id'],lambda q:q['chapters'].append(third))
        self.hold_first_qc=True
        with ThreadPoolExecutor(max_workers=1) as worker:
            future=worker.submit(self.run_story)
            try:
                self.assertTrue(self.signals['secondImage'].wait(3))
                self.assertFalse(future.done())
                self.assertNotIn(('analysis',3),self.service.calls)
            finally:self.release_first_qc.set()
            future.result(6)
        self.assertIn(('analysis',3),self.service.calls);self.assertEqual(self.provider.peak,1)

    def test_reordered_chapters_invalidate_the_frozen_story_inputs(self):
        runtime=CloudStoryOverlap(self.service,self.project,{'overlap':True})
        def reorder(q):
            q['chapters'].reverse()
            for number,chapter in enumerate(q['chapters'],1):chapter['number']=number
        try:
            self.service.store.mutate(self.project['id'],reorder)
            with self.assertRaisesRegex(ValueError,'Chapter source changed'):runtime.validate_settings()
        finally:runtime.close('FAILED','User reordered chapters')

    def test_generated_reference_cannot_replace_a_character_edited_during_generation(self):
        from studio_data import character
        self.service.wait_for_overlap=False
        p=self.service.store.load(self.project['id']);person=character('Mira')
        self.service.store.asset(p['id'],'original.png').write_bytes(b'original reference')
        person['references']=[{'path':'original.png','kind':'face'}]
        p['characters']=[person];p=self.service.store.save(p)
        service=self.service
        class EditedReference:
            id='comfyui'
            def getCapabilities(self):return {'maxReferenceImages':2,'supportsStyleReference':False,'supportsNegativePrompt':False}
            def validateSettings(self,settings):return settings
            def generateImage(self,request,checkpoint):
                service.store.mutate(p['id'],lambda q:q['characters'][0].update(description='New manual identity description'))
                return {'pil':Image.new('RGB',(16,16),'blue'),'settings':request['settings']}
        service.providers['comfyui']=EditedReference()
        with self.assertRaisesRegex(ValueError,'reference changed'):
            StudioService.character_reference(service,p,{'characterId':person['id']})
        current=service.store.load(p['id'])['characters'][0]
        self.assertEqual(current['references'],person['references'])
        self.assertEqual(current['description'],'New manual identity description')
        self.assertFalse(current['referenceHistory'][0]['selected'])
        self.assertTrue(service.store.asset(p['id'],current['referenceHistory'][0]['path']).is_file())


if __name__=='__main__':unittest.main()
