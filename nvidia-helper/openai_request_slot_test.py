"""Wire-only lane occupancy without changing paid requests or spend ownership."""
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import openai_director as adapter
import openai_director_test as fixtures
from cost_control import SpendLedger
from director_provider import obj,STR
from studio_execution import ImageLane
from studio_overlap import LaneDirector


class RequestSlotTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.folder=Path(self.temp.name)
        self.lane=ImageLane(1,'director')
        self.director=LaneDirector({},self.folder,self.lane);self.director.key=lambda:'synthetic-key'
        self.director.spend_ledger=SpendLedger(self.folder/'ledger.json',1,allow_multiple=True)

    def tearDown(self):self.temp.cleanup()

    def call(self,context=None,gate=lambda *_:None):
        return self.director.call('Offline slot test',context or {},obj({'summary':STR}),gate)

    def test_exact_vision_body_and_all_reference_details_match_unlaned_adapter(self):
        other=adapter.OpenAIDirector({'openaiServiceTier':'flex'},self.folder/'baseline')
        other.key=lambda:'synthetic-key';other.reasoning='High'
        self.director.config=dict(other.config);self.director.reasoning='High'
        context={'narration':'Mira takes the brass key.','_images':['image-output','ref-one','ref-two']}
        bodies=[]
        def response(request,**kwargs):
            bodies.append(json.loads(request.data));return fixtures.stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response):
            other.call('Visual QC',context,obj({'summary':STR}),lambda *_:None,vision=True)
            self.director.call('Visual QC',context,obj({'summary':STR}),lambda *_:None,vision=True)
        self.assertEqual(bodies[0],bodies[1])
        images=bodies[1]['input'][1]['content'][1:]
        self.assertEqual([item['image_url'] for item in images],context['_images'])
        self.assertTrue(all(item['detail']=='low' for item in images))

    def test_cache_hit_never_waits_for_occupied_lane_and_resets_old_wait(self):
        with patch('openai_director.urllib.request.urlopen',return_value=fixtures.stream()):self.call()
        self.director.api_lane_wait_seconds=900
        with self.lane.acquire(lambda *_:None):
            with patch.object(self.lane,'acquire',side_effect=AssertionError('Cache acquired lane')), \
                 patch('openai_director.urllib.request.urlopen') as request:
                self.assertEqual(self.call(),{'summary':'complete'});request.assert_not_called()
            self.assertEqual(self.lane.active,1)
        self.assertEqual(self.director.api_lane_wait_seconds,0)

    def test_preparation_reservation_validation_progress_and_cache_publish_are_outside_slot(self):
        original_sizes=adapter.request_sizes;reserve=self.director.spend_ledger.reserve
        settle=self.director.spend_ledger.settle;validate=self.director.validate_result
        original_temporary=tempfile.NamedTemporaryFile;checked=[]
        def sizes(*args,**kwargs):
            self.assertEqual(self.lane.active,0);checked.append('prepare');return original_sizes(*args,**kwargs)
        def reserving(*args,**kwargs):
            self.assertEqual(self.lane.active,0);checked.append('reserve');return reserve(*args,**kwargs)
        def settling(*args,**kwargs):
            self.assertEqual(self.lane.active,1);checked.append('settle');return settle(*args,**kwargs)
        def validating(*args,**kwargs):
            self.assertEqual(self.lane.active,0);checked.append('validate');return validate(*args,**kwargs)
        def publishing(*args,**kwargs):
            if Path(kwargs.get('dir','')).name=='director-cache':
                self.assertEqual(self.lane.active,0);checked.append('cache')
            return original_temporary(*args,**kwargs)
        def timing(*args):self.assertEqual(self.lane.active,0);checked.append('timing')
        def response(*args,**kwargs):
            self.assertEqual(self.lane.active,1);checked.append('wire');return fixtures.stream()
        self.director.timing_callback=timing
        with patch('openai_director.request_sizes',side_effect=sizes), \
             patch.object(self.director.spend_ledger,'reserve',side_effect=reserving), \
             patch.object(self.director.spend_ledger,'settle',side_effect=settling), \
             patch.object(self.director,'validate_result',side_effect=validating), \
             patch('openai_director.tempfile.NamedTemporaryFile',side_effect=publishing), \
             patch('openai_director.urllib.request.urlopen',side_effect=response):self.call()
        self.assertEqual(checked,['prepare','reserve','wire','settle','validate','timing','cache'])
        self.assertEqual(self.lane.active,0)

    def test_budget_wait_does_not_hold_slot_and_retains_reserved_owner_rules(self):
        ledger=self.director.spend_ledger
        ledger.cap_nanos=9_000_000
        ledger.reserve({'model':'gpt-6-luna','max_output_tokens':12000,'input':[]},
            request_id='other-held',owner='other-owner')
        self.director.wait_for_budget=True;waited=[]
        def gate(message):
            if message=='Waiting for reserved API usage to settle':
                self.assertEqual(self.lane.active,0);waited.append(message)
                ledger.cancel('other-held',owner='other-owner')
        with patch('openai_director.urllib.request.urlopen',return_value=fixtures.stream()):self.call(gate=gate)
        self.assertTrue(waited);self.assertFalse(ledger.load()['requests'])
        self.assertEqual(len(ledger.load()['receipts']),2)

    def test_cancel_while_waiting_for_slot_releases_only_held_call_and_buys_nothing(self):
        ledger=self.director.spend_ledger
        ledger.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':[]},
            request_id='other-held',owner='other-owner')
        def gate(message):
            if message=='Waiting for director lane':raise InterruptedError('Cancelled while held')
        with self.lane.acquire(lambda *_:None):
            with patch('openai_director.urllib.request.urlopen') as request:
                with self.assertRaisesRegex(InterruptedError,'Cancelled'):self.call(gate=gate)
                request.assert_not_called()
            self.assertEqual(self.lane.active,1)
        state=ledger.load();self.assertEqual(set(state['requests']),{'other-held'})
        self.assertEqual(len(state['receipts']),1);self.assertTrue(state['receipts'][0]['uncharged'])
        self.assertEqual(self.lane.waiters,[])

    def test_retry_releases_and_reacquires_once_with_separate_settled_receipts(self):
        acquire=self.lane.acquire;entries=[];bodies=[]
        @contextmanager
        def counted(gate):
            self.assertEqual(self.lane.active,0)
            with acquire(gate):entries.append(len(entries));yield
        def response(request,**kwargs):
            self.assertEqual(self.lane.active,1);bodies.append(json.loads(request.data))
            if len(bodies)==1:return fixtures.stream(status='incomplete',incomplete_details={'reason':'max_output_tokens'})
            return fixtures.stream()
        with patch.object(self.lane,'acquire',side_effect=counted), \
             patch('openai_director.urllib.request.urlopen',side_effect=response):self.call()
        self.assertEqual(entries,[0,1]);self.assertEqual(bodies[0]['max_output_tokens'],12000)
        self.assertEqual(bodies[1]['max_output_tokens'],24000)
        original=dict(bodies[0]);original['max_output_tokens']=24000
        self.assertEqual(original,bodies[1])
        self.assertEqual(len(self.director.spend_ledger.load()['receipts']),2)

    def test_success_and_failure_timing_exclude_all_lane_wait(self):
        for failing in (False,True):
            with self.subTest(failing=failing),tempfile.TemporaryDirectory() as folder:
                director=LaneDirector({},folder,ImageLane(1));director.key=lambda:'synthetic-key'
                now=[100.];timings=[]
                @contextmanager
                def wait_slot(gate):now[0]+=7;yield
                def response(*args,**kwargs):
                    now[0]+=2
                    if failing:raise TimeoutError('Synthetic timeout')
                    return fixtures.stream()
                director.timing_callback=lambda *args:timings.append(args)
                with patch('openai_director.time.monotonic',side_effect=lambda:now[0]), \
                     patch.object(director.lane,'acquire',side_effect=wait_slot), \
                     patch('openai_director.urllib.request.urlopen',side_effect=response):
                    if failing:
                        with self.assertRaises(TimeoutError):director.call('Test',{},obj({'summary':STR}),lambda *_:None)
                    else:director.call('Test',{},obj({'summary':STR}),lambda *_:None)
                self.assertEqual(timings[0][1],2)
                self.assertEqual(timings[0][2]['latency']['apiLaneWaitSeconds'],7)

    def test_multiple_prepared_adapters_still_bound_actual_wire_dispatch(self):
        barrier=threading.Barrier(2);lock=threading.Lock();active=peak=0
        lane=ImageLane(2,'director')
        directors=[LaneDirector({},self.folder/str(n),lane) for n in range(6)]
        for director in directors:director.key=lambda:'synthetic-key'
        def response(*args,**kwargs):
            nonlocal active,peak
            with lock:active+=1;peak=max(peak,active)
            barrier.wait(timeout=3)
            with lock:active-=1
            return fixtures.stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(max_workers=6) as pool:
            futures=[pool.submit(d.call,'Test',{},obj({'summary':STR}),lambda *_:None) for d in directors]
            self.assertTrue(all(f.result(timeout=5)=={'summary':'complete'} for f in futures))
        self.assertEqual(peak,2);self.assertEqual(lane.active,0);self.assertEqual(lane.waiters,[])

    def test_slow_progress_callback_does_not_block_another_adapters_wire(self):
        progress=threading.Event();second_wire=threading.Event()
        other=LaneDirector({},self.folder/'other',self.lane);other.key=lambda:'synthetic-key'
        def timing(*args):
            progress.set();self.assertTrue(second_wire.wait(3))
        self.director.timing_callback=timing
        def response(request,**kwargs):
            content=json.loads(json.loads(request.data)['input'][1]['content'][0]['text'])
            if content.get('second'):second_wire.set()
            return fixtures.stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(max_workers=2) as pool:
            first=pool.submit(self.call)
            self.assertTrue(progress.wait(3))
            second=pool.submit(other.call,'Test',{'second':True},obj({'summary':STR}),lambda *_:None)
            self.assertEqual(second.result(timeout=4),{'summary':'complete'})
            self.assertEqual(first.result(timeout=4),{'summary':'complete'})
        self.assertEqual(self.lane.active,0)

    def test_binding_retry_settles_then_reacquires_without_changing_repair_rule(self):
        from storyboard_review import bound_context
        from director_provider import arr
        context,_=bound_context({'sentences':[{'text':'first'},{'text':'second'}],
            'shots':[{'startSentence':0,'endSentence':0,'action':'first'},
                     {'startSentence':1,'endSentence':1,'action':'second'}]})
        issue=obj({'shotKey':STR,'category':STR,'sentence':{'type':'integer'},'issue':STR})
        schema=obj({'majorIssues':arr(issue),'advisories':arr(STR)})
        bad={'majorIssues':[{'shotKey':'s0-e0','category':'action','sentence':1,'issue':'wrong source'}],'advisories':[]}
        good={'majorIssues':[],'advisories':[]};acquire=self.lane.acquire;entries=[]
        @contextmanager
        def counted(gate):
            self.assertEqual(self.lane.active,0)
            with acquire(gate):entries.append(len(entries));yield
        with patch.object(self.lane,'acquire',side_effect=counted), \
             patch('openai_director.urllib.request.urlopen',side_effect=[
                 fixtures.stream(json.dumps(bad)),fixtures.stream(json.dumps(good))]) as request:
            self.assertEqual(self.director.call('Storyboard continuity supervisor:',context,schema,lambda *_:None),good)
        self.assertEqual(entries,[0,1]);self.assertEqual(len(self.director.spend_ledger.load()['receipts']),2)
        self.assertIn('Review binding correction',json.loads(request.call_args.args[0].data)['input'][0]['content'])


if __name__=='__main__':unittest.main()
