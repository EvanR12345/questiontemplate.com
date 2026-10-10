"""Real API adapter/journal/ledger; all network replies are synthetic."""
import copy
import io
import json
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import studio_overlap_test as fixtures
from studio_data import character,digest
from studio_overlap import CloudStoryOverlap
from studio_service import StudioService,JobCancelled
from studio_data import validate_project


class DirectorParallelTest(unittest.TestCase):
    # Reuse only setup/cleanup, not the image/QC test scenarios.
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown
    def requests(self,count=6):
        return [('writeImagePrompt',{'shots':[{'shotIndex':0,'narration':'Moment '+str(i),
            'draftPrompt':'Mira carries the key.'}],'model':'klein','promptFormat':'natural'}) for i in range(count)]

    def make_overlap(self,limit=3):
        p=copy.deepcopy(self.project);p['settings']['director']['parallelism']=limit
        if limit>3:p['settings']['director']['executionMode']='staged-review'
        p=self.service.store.save(p)
        return CloudStoryOverlap(self.service,p,{'overlap':True}),p

    @staticmethod
    def stream(index, value=None, completed=True):
        events=[{'type':'response.created','response':{'id':'response-'+str(index)}}]
        if completed:
            events += [{'type':'response.output_text.delta','delta':json.dumps(value if value is not None else
                {'prompts':[{'shotIndex':0,'prompt':'Mira holds the key.'}]})},
                {'type':'response.completed','response':{'id':'response-'+str(index),'status':'completed',
                    'usage':{'input_tokens':100,'output_tokens':30}}}]
        return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))

    def ledger(self,p):
        from cost_control import SpendLedger
        return SpendLedger(self.service.store.folder(p['id'])/'api-cost-ledger.json',
            p['settings']['budget']['openaiUSD'],allow_multiple=True).load()

    def test_project_validation_keeps_old_serial_default_and_rejects_unbounded_fanout(self):
        p=copy.deepcopy(self.project);p['settings']['director'].pop('parallelism',None)
        validate_project(p)
        for value in (0,4,True,2.5,'3'):
            p['settings']['director']['parallelism']=value
            with self.assertRaisesRegex(ValueError,'parallel director'):validate_project(p)

    def test_edit_during_paid_response_settles_receipt_but_cannot_commit_old_plan(self):
        overlap,p=self.make_overlap();chid=p['chapters'][0]['id']
        person=character('Mira');p['characters']=[person]
        p=self.service.store.save(p);expected=StudioService.analysis_input_signature(p,chid)
        def response(*args,**kwargs):
            self.service.store.mutate(p['id'],lambda q:q['characters'][0].update(description='New manual identity description'))
            return self.stream(0)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                with self.assertRaisesRegex(ValueError,'changed during direction'):
                    overlap.main.director_calls(p,chid,self.requests(1),expected)
            self.assertFalse(self.ledger(p)['requests']);self.assertEqual(len(self.ledger(p)['receipts']),1)
            self.assertEqual(self.service.store.load(p['id'])['characters'][0]['description'],'New manual identity description')
            children=[r for r in self.service.execution_journal.snapshot(overlap.parent) if r['kind']=='director-pass']
            self.assertEqual(children[0]['status'],'SUPERSEDED')
        finally:overlap.close('FAILED')

    def test_malformed_sibling_stops_new_dispatch_and_drains_paid_receipts(self):
        overlap,p=self.make_overlap();barrier=threading.Barrier(3)
        seen=[];lock=threading.Lock();failed=threading.Event()
        def response(*args,**kwargs):
            with lock:index=len(seen);seen.append(index)
            barrier.wait(timeout=3)
            if index==0:
                failed.set();return self.stream(index,{'wrongField':True})
            self.assertTrue(failed.wait(3))
            return self.stream(index)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                with self.assertRaises(ValueError):
                    overlap.main.director_calls(p,p['chapters'][0]['id'],self.requests(30))
            # Responses may finish before the failure is observed; the invariant
            # is bounded admission and no UNKNOWN/free charge after draining.
            self.assertLess(len(seen),30);self.assertFalse(self.ledger(p)['requests'])
            self.assertEqual(len(self.ledger(p)['receipts']),len(seen))
            self.assertEqual(overlap.api_lane.active,0)
            self.assertEqual(len(self.service.active_executions),1)
        finally:overlap.close('FAILED')

    def test_missing_receipt_preserves_unknown_liability_and_prevents_replay(self):
        overlap,p=self.make_overlap()
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=self.stream(0,completed=False)) as request:
                with self.assertRaises(RuntimeError):
                    overlap.main.director_calls(p,p['chapters'][0]['id'],self.requests(1))
                self.assertEqual(request.call_count,1)
            state=self.ledger(p);self.assertEqual(len(state['requests']),1)
            self.assertEqual(next(iter(state['requests'].values()))['status'],'UNKNOWN')
            self.assertGreater(state['reservedUSD'],0)
            children=[r for r in self.service.execution_journal.snapshot(overlap.parent) if r['kind']=='director-pass']
            self.assertEqual(children[0]['status'],'UNKNOWN')
        finally:overlap.close('FAILED')
        with self.assertRaisesRegex(RuntimeError,'unresolved requests'):
            CloudStoryOverlap(self.service,self.service.store.load(p['id']),{'overlap':True})

    def test_budget_rejection_buys_no_request(self):
        p=copy.deepcopy(self.project);p['settings']['budget']['openaiUSD']=.000001
        p['settings']['director']['parallelism']=3;p=self.service.store.save(p)
        overlap=CloudStoryOverlap(self.service,p,{'overlap':True})
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(RuntimeError,'budget'):
                    overlap.main.director_calls(p,p['chapters'][0]['id'],self.requests(6))
                request.assert_not_called()
            self.assertFalse(self.ledger(p)['requests']);self.assertEqual(overlap.api_lane.active,0)
        finally:overlap.close('FAILED')

    def test_pause_drains_paid_responses_then_holds_admission_until_resume(self):
        overlap,p=self.make_overlap();barrier=threading.Barrier(3);paused=threading.Event()
        seen=[];lock=threading.Lock()
        def response(*args,**kwargs):
            with lock:index=len(seen);seen.append(index)
            if index<3:barrier.wait(timeout=3)
            raw=self.stream(index).getvalue()
            owner=self
            class Response(io.BytesIO):
                def __iter__(stream):
                    if index==0:owner.service.control('pause');paused.set()
                    while True:
                        line=stream.readline()
                        if not line:break
                        yield line
            return Response(raw)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response), \
                 ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.director_calls,p,p['chapters'][0]['id'],self.requests(6))
                try:
                    self.assertTrue(paused.wait(3));deadline=time.monotonic()+3
                    while len(self.ledger(p)['receipts'])<3 and time.monotonic()<deadline:time.sleep(.02)
                    self.assertEqual(len(self.ledger(p)['receipts']),3)
                    self.assertFalse(self.ledger(p)['requests']);self.assertEqual(len(seen),3)
                    self.assertFalse(future.done())
                finally:self.service.control('resume')
                self.assertEqual(len(future.result(timeout=6)),6)
            self.assertEqual(len(self.ledger(p)['receipts']),6);self.assertEqual(overlap.api_lane.active,0)
        finally:
            with self.service.cv:self.service.paused=False;self.service.cv.notify_all()
            overlap.close('COMPLETE')

    def test_cancelling_one_paid_pass_keeps_its_liability_and_drains_siblings(self):
        overlap,p=self.make_overlap();chid=p['chapters'][0]['id'];calls=self.requests(9)
        expected=StudioService.analysis_input_signature(p,chid)
        target_hash=digest({'method':calls[0][0],'input':calls[0][1],'analysis':expected})
        barrier=threading.Barrier(3);cancelled=threading.Event();seen=[];lock=threading.Lock()
        def response(request,**kwargs):
            body=json.loads(request.data);payload=json.loads(body['input'][1]['content'][0]['text'])
            target=payload['shots'][0]['narration']=='Moment 0'
            with lock:index=len(seen);seen.append(index)
            barrier.wait(timeout=3);raw=self.stream(index).getvalue();owner=self
            class Response(io.BytesIO):
                def __iter__(stream):
                    if not target:owner.assertTrue(cancelled.wait(3))
                    while True:
                        line=stream.readline()
                        if not line:break
                        yield line
                        if target and b'response.created' in line:
                            child=next(c for c in owner.service.active_executions.values()
                                if c.kind=='director-pass' and c.input_hash==target_hash)
                            owner.service.control('cancel-current',job=child.identity);cancelled.set()
            return Response(raw)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                with self.assertRaises(JobCancelled):overlap.main.director_calls(p,chid,calls,expected)
            state=self.ledger(p)
            self.assertEqual(len(seen),3);self.assertEqual(len(state['receipts']),2)
            self.assertEqual(len(state['requests']),1)
            self.assertEqual(next(iter(state['requests'].values()))['status'],'UNKNOWN')
            self.assertEqual(overlap.api_lane.active,0);self.assertEqual(len(self.service.active_executions),1)
        finally:overlap.close('FAILED')

    def test_analysis_input_guard_includes_manual_cast_description_and_alias_changes(self):
        p=copy.deepcopy(self.project);p['characters']=[character('Mira')]
        chid=p['chapters'][0]['id'];base=StudioService.analysis_input_signature(p,chid)
        for field,value in [('name','Mary'),('description','User selected red hair'),
                ('aliases',['The keeper']),('defaultAppearance',{'outfit':'manual red cloak'})]:
            edited=copy.deepcopy(p);edited['characters'][0][field]=value
            self.assertNotEqual(StudioService.analysis_input_signature(edited,chid),base)
        edited=copy.deepcopy(p);edited.setdefault('production',{})['timings']=[{'seconds':3}]
        self.assertEqual(StudioService.analysis_input_signature(edited,chid),base)

    def test_previous_chapter_memory_edit_invalidates_analysis_without_an_appearance_change(self):
        from studio_data import new_chapter
        p=copy.deepcopy(self.project);p['chapters'].append(new_chapter(2))
        p['chapters'][0]['handoff']={'state':{'characters':{}},'memory':{'revealed':['Mira knows the secret.']}}
        chid=p['chapters'][1]['id'];base=StudioService.analysis_input_signature(p,chid)
        edited=copy.deepcopy(p)
        edited['chapters'][0]['handoff']['memory']['revealed']=['Mira has not learned the secret.']
        self.assertNotEqual(StudioService.analysis_input_signature(edited,chid),base)
        self.assertEqual(edited['chapters'][0]['handoff']['state'],p['chapters'][0]['handoff']['state'])

    def test_parallel_requests_keep_frozen_inputs_own_receipts_and_bound_shared_api_slots(self):
        self.verify_parallel_receipts(3,6)

    def test_staged_eight_slots_have_distinct_receipts_and_bound_all_dispatch(self):
        self.verify_parallel_receipts(8,16)

    def verify_parallel_receipts(self,limit,count):
        overlap,p=self.make_overlap(limit);barrier=threading.Barrier(limit)
        lock=threading.Lock();seen=[];active=peak=0
        def response(request,**kwargs):
            nonlocal active,peak
            body=json.loads(request.data)
            with lock:
                seen.append(body);index=len(seen);active+=1;peak=max(peak,active)
            barrier.wait(timeout=3)
            with lock:active-=1
            value={'prompts':[{'shotIndex':0,'prompt':'Directed moment '+str(index)}]}
            if limit>3:
                def compact(value,schema):
                    if isinstance(value,dict):
                        return {key:compact(value[child['description'].split('. ',1)[0]],child)
                            for key,child in schema['properties'].items()}
                    if isinstance(value,list):return [compact(item,schema['items']) for item in value]
                    return value
                value=compact(value,body['text']['format']['schema'])
            events=[{'type':'response.created','response':{'id':'response-'+str(index)}},
                {'type':'response.output_text.delta','delta':json.dumps(value)},
                {'type':'response.completed','response':{'id':'response-'+str(index),'status':'completed',
                    'usage':{'input_tokens':100,'output_tokens':30}}}]
            return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
        calls=self.requests(count);before=copy.deepcopy(calls)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                results=overlap.main.director_calls(p,p['chapters'][0]['id'],calls)
            self.assertEqual(len(results),count);self.assertEqual(peak,limit);self.assertEqual(calls,before)
            self.assertEqual(overlap.api_lane.active,0)
            ledger=json.loads((self.service.store.folder(p['id'])/'api-cost-ledger.json').read_text())
            self.assertEqual(len(ledger['receipts']),count);self.assertFalse(ledger['requests'])
            children=[r for r in self.service.execution_journal.snapshot(overlap.parent) if r['kind']=='director-pass']
            self.assertEqual(len(children),count);self.assertTrue(all(r['status']=='COMPLETE' for r in children))
            self.assertEqual(len({next(iter(r['operations'])) for r in children}),count)
            self.assertTrue(all(len(r['operations'])==1 for r in children))
            self.assertEqual(len(self.service.active_executions),1)
        finally:overlap.close('COMPLETE')

    def test_ordered_story_pass_cannot_be_accidentally_parallelized(self):
        overlap,p=self.make_overlap()
        try:
            with patch('openai_director.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(ValueError,'ordered story state'):
                    overlap.main.director_calls(p,p['chapters'][0]['id'],[('analyzeStory',{})])
                request.assert_not_called()
        finally:overlap.close('FAILED')

    def test_stale_input_before_dispatch_buys_no_call_and_keeps_edit(self):
        overlap,p=self.make_overlap();chid=p['chapters'][0]['id']
        expected=overlap.main.analysis_input_signature(p,chid)
        person=character('Manual person',kind='main');person['permanentIdentity']['eyes']='green'
        self.service.store.mutate(p['id'],lambda q:q['characters'].append(person))
        try:
            with patch('openai_director.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(ValueError,'inputs changed'):
                    overlap.main.director_calls(p,chid,self.requests(1),expected)
                request.assert_not_called()
            self.assertEqual(self.service.store.load(p['id'])['characters'][-1]['id'],person['id'])
        finally:overlap.close('FAILED')


if __name__=='__main__':unittest.main()
