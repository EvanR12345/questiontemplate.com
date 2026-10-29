"""Accepted groups through real ownership/ledger/stream paths; network forbidden."""
import copy
import io
import json
import threading
import unittest
from unittest.mock import patch

import studio_overlap_test as fixtures
from cost_control import SpendLedger
from director_acceleration_test import storyboard
from director_pipeline import AcceptedVisualQueue
from director_provider import compact_source_evidence
from director_storyboard import storyboard_schema
from director_wire import CompactDirectorWire
from studio_data import digest
from studio_overlap import CloudStoryOverlap, LaneDirector
from studio_service import StudioService, JobCancelled


def compact_answer(codec,value):
    if isinstance(value,dict):
        return {codec.keys.get(key,key):compact_answer(codec,item) for key,item in value.items()}
    if isinstance(value,list):return [compact_answer(codec,item) for item in value]
    return codec.encode_values(value)


class MockStreams:
    """Hold complete SSE receipts independently while the real lanes are owned."""
    def __init__(self,test,overlap,calls,modes=None):
        self.test=test;self.overlap=overlap;self.calls={payload['sentences'][0]['text'].split('marker:')[1]:payload
            for _method,payload in calls}
        self.modes=modes or {};self.cv=threading.Condition();self.releases={name:threading.Event() for name in self.calls}
        self.started={};self.finished=set();self.bodies={};self.headers={};self.active=0;self.peak=0

    def wait_started(self,*names):
        with self.cv:
            if not self.cv.wait_for(lambda:all(name in self.started for name in names),timeout=5):
                raise AssertionError('Missing mocked wire dispatch: '+repr(names))

    def release(self,*names):
        for name in names:self.releases[name].set()

    def release_all(self):
        for event in self.releases.values():event.set()

    def open(self,request,*_args,**_kwargs):
        self.test.assertEqual(request.full_url,'https://api.openai.com/v1/responses')
        self.test.assertEqual(request.get_method(),'POST')
        body=json.loads(request.data)
        payload=json.loads(body['input'][1]['content'][0]['text'])
        name=payload['sentences'][0]['text'].split('marker:')[1]
        original=self.calls[name]
        codec=CompactDirectorWire(compact_source_evidence(copy.deepcopy(original)),storyboard_schema(original))
        self.test.assertEqual(body['text']['format']['schema'],codec.schema)
        self.test.assertEqual(payload,codec.context)
        self.test.assertEqual(body['model'],'gpt-6-luna')
        self.test.assertEqual(body['service_tier'],'default')
        self.test.assertEqual(body['reasoning']['effort'],'low')
        self.test.assertEqual(body['max_output_tokens'],8000)
        self.test.assertFalse(body['store']);self.test.assertTrue(body['stream'])
        self.test.assertGreater(self.overlap.api_lane.active,0)
        self.test.assertLessEqual(self.overlap.api_lane.active,4)
        with self.cv:
            # A second dispatch, including a hidden transport retry, is a failure.
            self.test.assertNotIn(name,self.started)
            self.started[name]=True;self.bodies[name]=body
            self.headers[name]=request.get_header('X-client-request-id')
            self.active+=1;self.peak=max(self.peak,self.active);self.cv.notify_all()
        mode=self.modes.get(name,'complete')
        value=compact_answer(codec,storyboard(original)) if mode!='malformed' else {'wrongField':True}
        created={'type':'response.created','response':{'id':'mock-response-'+name}}
        status='incomplete' if mode=='incomplete' else 'completed'
        completed={'id':'mock-response-'+name,'status':status,'service_tier':'default',
            'usage':{'input_tokens':100,'output_tokens':30,'output_tokens_details':{'reasoning_tokens':5}}}
        if mode=='incomplete':completed['incomplete_details']={'reason':'max_output_tokens'}
        owner=self
        class Response(io.BytesIO):
            def __iter__(stream):
                try:
                    yield b'data: '+json.dumps(created).encode()+b'\n'
                    if not owner.releases[name].wait(5):raise AssertionError('Mock receipt not released: '+name)
                    if mode=='unknown':return
                    yield b'data: '+json.dumps({'type':'response.output_text.delta','delta':json.dumps(value)}).encode()+b'\n'
                    yield b'data: '+json.dumps({'type':'response.'+status,'response':completed}).encode()+b'\n'
                finally:
                    with owner.cv:
                        owner.active-=1;owner.finished.add(name);owner.cv.notify_all()
        return Response()


class AcceptedVisualOwnershipIntegrationTest(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown

    def make_overlap(self):
        project=copy.deepcopy(self.project)
        project['settings']['director'].update(parallelism=4,executionMode='staged-lean',
            sourceVisualOverlap=True,reasoning='Fast',maxOutputTokens=8000)
        project=self.service.store.save(project)
        overlap=CloudStoryOverlap(self.service,project,{'overlap':True})
        self.assertIsInstance(overlap.main.director,LaneDirector)
        self.assertEqual(overlap.api_lane.capacity,4)
        return overlap,project

    @staticmethod
    def calls(names):
        return [('planStoryboard',{'sentences':[{'index':0,'text':'Mira carries a brass key. marker:'+name,
                    'start':0,'end':5}], 'people':[{'id':'mira','name':'Mira'}],
                'priorState':{'characters':{'mira':{'outfit':'gray coat'}}},'acceptedChanges':[],
                'requiredVisualChangeBoundaries':[0],'directorPayloadVersion':6,
                'cadenceTarget':{'approximateShots':1},'style':'unchanged visual style'}) for name in names]

    def ledger(self,project):
        return SpendLedger(self.service.store.folder(project['id'])/'api-cost-ledger.json',
            project['settings']['budget']['openaiUSD'],allow_multiple=True).load()

    def children(self,overlap):
        return [row for row in self.service.execution_journal.snapshot(overlap.parent) if row['kind']=='director-pass']

    def assert_owners(self,overlap,project,expected,calls,streams):
        children=self.children(overlap)
        self.assertEqual(len(children),len(streams.started))
        by_hash={digest({'method':method,'input':payload,'analysis':expected}):payload for method,payload in calls}
        self.assertEqual({row['input_hash'] for row in children},set(by_hash))
        self.assertEqual(len({row['id'] for row in children}),len(children))
        state=self.ledger(project);records=[*state['receipts'],*state['requests'].values()]
        self.assertEqual(len(records),len(streams.started))
        self.assertEqual(len({row['owner'] for row in records}),len(records))
        self.assertEqual({row['requestId'] for row in records},set(streams.headers.values()))
        for row in children:
            self.assertEqual(len(row['operations']),1)
            self.assertIn(next(iter(row['operations'])),streams.headers.values())

    def test_real_executor_uses_free_four_slot_capacity_across_groups_and_settles_every_owner(self):
        overlap,project=self.make_overlap();chapter=project['chapters'][0]['id']
        expected=StudioService.analysis_input_signature(project,chapter)
        first=self.calls(['A0','A1','A2','A3']);second=self.calls(['B0','B1']);calls=first+second
        streams=MockStreams(self,overlap,calls)
        try:
            with patch('socket.create_connection',side_effect=AssertionError('Network forbidden')), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=streams.open):
                with AcceptedVisualQueue(overlap.main,project,chapter,expected,True) as queue:
                    self.assertTrue(queue.request_dispatch)
                    queue.add(first);streams.wait_started('A0','A1','A2','A3')
                    queue.add(second);streams.release('A1','A2','A3')
                    streams.wait_started('B0','B1')
                    self.assertNotIn('A0',streams.finished)
                    streams.release_all();result=queue.finish(calls)
            self.assertEqual([row['detail']['shots'][0]['action'].split('marker:')[1] for row in result],
                ['A0','A1','A2','A3','B0','B1'])
            self.assertEqual(streams.peak,4);self.assertEqual(overlap.api_lane.active,0)
            state=self.ledger(project);self.assertFalse(state['requests']);self.assertEqual(len(state['receipts']),6)
            self.assertTrue(all(row['usageConfirmed'] and row['estimatedUSD']>0 for row in state['receipts']))
            self.assert_owners(overlap,project,expected,calls,streams)
            self.assertTrue(all(row['status']=='COMPLETE' for row in self.children(overlap)))
            self.assertEqual(len(self.service.active_executions),1)
            current=self.service.store.load(project['id'])
            self.assertEqual(current['chapters'],project['chapters'])
            self.assertEqual(current['settings'],project['settings'])
        finally:
            streams.release_all();overlap.close('COMPLETE')

    def test_sibling_failure_preserves_unknown_drains_paid_receipts_and_buys_no_retry_or_next_group(self):
        overlap,project=self.make_overlap();chapter=project['chapters'][0]['id']
        expected=StudioService.analysis_input_signature(project,chapter)
        first=self.calls(['bad','retry','unknown','paid']);second=self.calls(['never0','never1']);calls=first+second
        streams=MockStreams(self,overlap,calls,{'bad':'malformed','retry':'incomplete','unknown':'unknown'})
        original_settle=SpendLedger.settle;retry_settled=threading.Event();queue=None
        def settle(ledger,*args,**kwargs):
            result=original_settle(ledger,*args,**kwargs)
            if kwargs.get('request_id')==streams.headers.get('retry'):
                # Preserve the existing eligible max-output retry but close its
                # shared dispatch gate after the sibling's settled invalid reply.
                retry_settled.set();streams.release('bad')
                self.assertTrue(queue.stopped.wait(5))
            return result
        try:
            with patch('socket.create_connection',side_effect=AssertionError('Network forbidden')), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=streams.open), \
                 patch.object(SpendLedger,'settle',new=settle):
                with AcceptedVisualQueue(overlap.main,project,chapter,expected,True) as queue:
                    queue.add(first);streams.wait_started('bad','retry','unknown','paid')
                    queue.add(second);streams.release('retry')
                    self.assertTrue(retry_settled.wait(5));self.assertTrue(queue.stopped.wait(5))
                    streams.release('unknown','paid')
                    with self.assertRaises((ValueError,RuntimeError,JobCancelled)):queue.finish(calls)
            self.assertEqual(set(streams.started),{'bad','retry','unknown','paid'})
            self.assertEqual(streams.peak,4);self.assertEqual(overlap.api_lane.active,0)
            state=self.ledger(project);self.assertEqual(len(state['receipts']),3)
            self.assertTrue(all(row['usageConfirmed'] and row['estimatedUSD']>0 for row in state['receipts']))
            self.assertEqual(len(state['requests']),1);unknown=next(iter(state['requests'].values()))
            self.assertEqual(unknown['status'],'UNKNOWN');self.assertGreater(state['reservedUSD'],0)
            self.assertEqual(unknown['requestId'],streams.headers['unknown'])
            self.assertEqual(unknown['responseId'],'mock-response-unknown')
            self.assert_owners(overlap,project,expected,first,streams)
            children=self.children(overlap)
            self.assertEqual(sum(row['status']=='UNKNOWN' for row in children),1)
            self.assertEqual(sum(row['status']=='COMPLETE' for row in children),1)
            self.assertEqual(len(self.service.active_executions),1)
            current=self.service.store.load(project['id'])
            self.assertEqual(current['chapters'],project['chapters'])
            self.assertEqual(current['settings'],project['settings'])
        finally:
            streams.release_all();overlap.close('FAILED')
        with self.assertRaisesRegex(RuntimeError,'unresolved requests'):
            CloudStoryOverlap(self.service,self.service.store.load(project['id']),{'overlap':True})


if __name__=='__main__':unittest.main()
