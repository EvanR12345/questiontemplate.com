import io
import hashlib
import json
import os
import tempfile
import unittest
import urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from director_provider import obj, STR
from openai_director import OpenAIDirector


def stream(value='{"summary":"complete"}', status="completed", **extra):
    response = {"status": status, "usage": {"input_tokens": 100, "output_tokens": 25}, **extra}
    events = [{"type": "response.output_text.delta", "delta": value},
              {"type": "response." + status, "response": response}]
    return io.BytesIO(b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events))


class LunaTest(unittest.TestCase):
    def test_incomplete_prompt_mapping_is_charged_but_never_cached(self):
        from cost_control import SpendLedger
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder);director.spend_ledger=SpendLedger(Path(folder)/'ledger.json',1)
            with patch('openai_director.urllib.request.urlopen',return_value=stream('{"prompts":[]}')):
                with self.assertRaisesRegex(ValueError,'prompt batch'):
                    director.writeImagePrompt({'shots':[{'shotIndex':0,'draftPrompt':'Mira has the key.'}]},lambda *_:None)
            self.assertGreater(director.spend_ledger.load()['spentUSD'],0)
            self.assertFalse(list((Path(folder)/'director-cache').glob('*.json')))

    def test_invalid_old_cache_is_preserved_without_implicit_repurchase(self):
        from director_provider import DirectorProvider,arr,INT
        for raw in ('{"prompts":[]}','{corrupted-json'):
            with self.subTest(raw=raw),tempfile.TemporaryDirectory() as folder:
                capture=DirectorProvider();captured=[]
                capture.call=lambda role,context,schema,*args:captured.append((role,schema))
                context={'shots':[{'shotIndex':0,'draftPrompt':'Mira has the key.'}]}
                capture.writeImagePrompt(context,lambda *_:None);role,schema=captured[0]
                director=self.director(folder)
                identity={'provider':'openai-luna','adapterVersion':1,'model':director.model,
                    'role':role,'context':context,'schema':schema,'reasoning':'medium','vision':False}
                cache=Path(folder)/'director-cache'/(hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'.json')
                cache.parent.mkdir();cache.write_text(raw)
                with patch('openai_director.urllib.request.urlopen') as request:
                    with self.assertRaisesRegex(ValueError,'retained for diagnosis'):
                        director.writeImagePrompt(context,lambda *_:None)
                    request.assert_not_called()
                retained=list(cache.parent.glob('*.invalid-*.json'))
                self.assertEqual(len(retained),1);self.assertEqual(retained[0].read_text(),raw)
                self.assertFalse(cache.exists())
                with patch('openai_director.urllib.request.urlopen',return_value=stream(
                    '{"prompts":[{"shotIndex":0,"prompt":"Side light on the held key."}]}')):
                    value=director.writeImagePrompt(context,lambda *_:None)
                self.assertEqual(value['prompts'][0]['shotIndex'],0);self.assertTrue(cache.is_file())

    def test_invalid_paid_result_records_failure_time_and_cost_without_payload(self):
        from cost_control import SpendLedger
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder);director.spend_ledger=SpendLedger(Path(folder)/'ledger.json',1)
            timings=[];director.timing_callback=lambda *args:timings.append(args)
            with patch('openai_director.urllib.request.urlopen',return_value=stream('{"summary":12}')):
                with self.assertRaises(ValueError):director.call('Test',{},obj({'summary':STR}),lambda *_:None)
            self.assertEqual(len(timings),1);details=timings[0][2]
            self.assertEqual(details['status'],'FAILED');self.assertFalse(details['usagePending'])
            self.assertEqual(details['reservedUSD'],0);self.assertGreater(details['estimatedUSD'],0)
            self.assertAlmostEqual(details['estimatedUSD'],director.spend_ledger.load()['spentUSD'])
            self.assertNotIn('summary',json.dumps(details));self.assertNotIn('test-key',json.dumps(details))

    def test_interrupted_paid_stream_reports_reserved_liability_separately(self):
        from cost_control import SpendLedger
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder);director.spend_ledger=SpendLedger(Path(folder)/'ledger.json',1)
            timings=[];director.timing_callback=lambda *args:timings.append(args)
            response=io.BytesIO(b'data: {"type":"response.created","response":{"id":"synthetic-receipt"}}\n\n')
            with patch('openai_director.urllib.request.urlopen',return_value=response):
                with self.assertRaises(RuntimeError):director.call('Test',{},obj({'summary':STR}),lambda *_:None)
            details=timings[0][2];self.assertTrue(details['usagePending'])
            self.assertEqual(details['estimatedUSD'],0);self.assertGreater(details['reservedUSD'],0)
            self.assertTrue(director.spend_ledger.load()['requests'])
            self.assertNotIn('synthetic-receipt',json.dumps(details))

    def test_latency_separates_visible_reasoning_tokens_and_attempt_limits(self):
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder);timings=[]
            director.timing_callback=lambda *args:timings.append(args)
            response=stream(usage={'input_tokens':100,'output_tokens':40,
                'output_tokens_details':{'reasoning_tokens':15}})
            with patch('openai_director.urllib.request.urlopen',return_value=response):
                director.call('Test',{},obj({'summary':STR}),lambda *_:None)
            details=timings[0][2]
            self.assertEqual(details['reasoningTokens'],15);self.assertEqual(details['visibleOutputTokens'],25)
            attempt=details['latency']['attempts'][0]
            self.assertGreaterEqual(attempt['firstTextSeconds'],attempt['headersSeconds'])
            self.assertGreaterEqual(attempt['streamSeconds'],attempt['firstTextSeconds'])
            self.assertEqual(attempt['outputLimit'],12000);self.assertEqual(attempt['status'],'completed')
            self.assertNotIn('synthetic',json.dumps(details))

    def test_unreported_reasoning_count_stays_unknown_instead_of_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder);timings=[];director.timing_callback=lambda *a:timings.append(a)
            with patch('openai_director.urllib.request.urlopen',return_value=stream()):
                director.call('Test',{},obj({'summary':STR}),lambda *_:None)
            self.assertIsNone(timings[0][2]['reasoningTokens'])
            self.assertIsNone(timings[0][2]['visibleOutputTokens'])

    def test_vision_keeps_all_three_supplied_images_and_exposes_its_limit(self):
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder)
            images=['data:image/png;base64,IMAGE_'+str(i) for i in range(3)]
            with patch('openai_director.urllib.request.urlopen',return_value=stream()) as request:
                director.call('Visual check',{'_images':images},obj({'summary':STR}),lambda *_:None,vision=True)
            body=json.loads(request.call_args.args[0].data)
            actual=[x['image_url'] for x in body['input'][1]['content'] if x['type']=='input_image']
            self.assertEqual(actual,images)
            self.assertEqual(director.max_vision_images,3)

    def test_oversized_vision_request_rejects_before_even_reusing_an_old_truncated_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder)
            schema=obj({'summary':STR});context={'_images':['synthetic-image']*4}
            identity={'provider':'openai-luna','adapterVersion':1,'model':director.model,
                'role':'Visual check','context':context,'schema':schema,'reasoning':'medium','vision':True}
            cache=Path(folder)/'director-cache'/(hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'.json')
            cache.parent.mkdir();cache.write_text('{"summary":"old truncated review"}')
            with patch('openai_director.urllib.request.urlopen') as request, patch.object(director,'key') as key:
                with self.assertRaisesRegex(ValueError,'contains 4'):
                    director.call('Visual check',context,schema,lambda *_:None,vision=True)
                request.assert_not_called();key.assert_not_called()
            self.assertEqual(json.loads(cache.read_text()),{'summary':'old truncated review'})

    def test_invalid_image_collection_fails_before_network_or_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            director=self.director(folder)
            with patch('openai_director.urllib.request.urlopen') as request, patch.object(director,'key') as key:
                with self.assertRaisesRegex(ValueError,'must be a list'):
                    director.call('Visual check',{'_images':'not-a-list'},obj({'summary':STR}),lambda *_:None,vision=True)
                request.assert_not_called();key.assert_not_called()

    def test_flex_setting_does_not_silently_fallback_and_uses_actual_billing_tier(self):
        from cost_control import SpendLedger, luna_cost
        with tempfile.TemporaryDirectory() as folder:
            director=OpenAIDirector({'openaiServiceTier':'flex'},Path(folder))
            director.key=lambda:'test-key'
            director.spend_ledger=SpendLedger(Path(folder)/'ledger.json',.02)
            timings=[];director.timing_callback=lambda *args:timings.append(args)
            with patch('openai_director.urllib.request.urlopen',side_effect=[
                stream(status='incomplete',incomplete_details={'reason':'max_output_tokens'},service_tier='flex'),
                stream(service_tier='default')
            ]) as request:
                director.call('Test',{},obj({'summary':STR}),lambda *_:None)
                self.assertEqual(request.call_count,2)
                self.assertTrue(all(json.loads(c.args[0].data)['service_tier']=='flex'
                                    for c in request.call_args_list))
            expected=luna_cost({'input_tokens':100,'output_tokens':25})*1.5
            self.assertAlmostEqual(timings[0][2]['estimatedUSD'],expected)
            self.assertAlmostEqual(director.spend_ledger.load()['spentUSD'],expected)
            self.assertEqual(timings[0][2]['serviceTiers'],['flex','default'])

    def test_unique_passes_avoid_cache_write_tax_and_implicit_is_configurable(self):
        for mode in ('explicit','implicit'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as folder:
                director=OpenAIDirector({'openaiPromptCacheMode':mode},Path(folder))
                director.key=lambda:'test-key'
                captured=[]
                def response(request,**_):
                    captured.append(json.loads(request.data));return stream()
                with patch('openai_director.urllib.request.urlopen',response):
                    director.call('Test',{},obj({'summary':STR}),lambda *_:None)
                self.assertEqual(captured[0]['prompt_cache_options'],{'mode':mode})
                self.assertNotIn('prompt_cache_breakpoint',json.dumps(captured[0]))
        with tempfile.TemporaryDirectory() as folder:
            director=OpenAIDirector({'openaiPromptCacheMode':'invalid'},Path(folder))
            director.key=lambda:'test-key'
            with patch('openai_director.urllib.request.urlopen') as request:
                with self.assertRaises(ValueError):
                    director.call('Test',{},obj({'summary':STR}),lambda *_:None)
                request.assert_not_called()

    def director(self, path):
        director = OpenAIDirector({}, path)
        director.key = lambda: "sk-test-placeholder-not-a-real-key"
        return director

    def test_stream_contract_cache_and_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            timings = []
            director.timing_callback = lambda *args: timings.append(args)
            with patch("openai_director.urllib.request.urlopen", return_value=stream()) as request:
                for _ in range(2):
                    self.assertEqual(director.call("Story analyst", {"sentences": []}, obj({"summary": STR}), lambda *args: None), {"summary": "complete"})
                body = json.loads(request.call_args.args[0].data)
                self.assertEqual(body["model"], "gpt-6-luna")
                self.assertFalse(body["store"])
                self.assertTrue(body["stream"])
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertEqual(request.call_count, 1)
            self.assertEqual(timings[0][2]["inputTokens"], 100)
            self.assertTrue(timings[1][2]["reused"])
            self.assertEqual(timings[1][1], 0)
            self.assertIsNone(director.response)

    def test_missing_credentials_and_bad_schema_do_not_save_a_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            director = OpenAIDirector({}, folder)
            with patch.dict("os.environ", {}, clear=True), patch("openai_director.urllib.request.urlopen") as request:
                with self.assertRaisesRegex(RuntimeError, "API key"):
                    director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
                request.assert_not_called()
            director = self.director(folder)
            with patch("openai_director.urllib.request.urlopen", return_value=stream('{"summary":123}')):
                with self.assertRaises(ValueError):
                    director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
            self.assertFalse(list(Path(folder).rglob("*.json")))

    def test_cancel_closes_stream_and_preserves_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            response = stream()
            def gate(message):
                if director.response is not None:
                    raise InterruptedError("cancelled")
            with patch("openai_director.urllib.request.urlopen", return_value=response):
                with self.assertRaises(InterruptedError):
                    director.call("Story analyst", {}, obj({"summary": STR}), gate)
            self.assertTrue(response.closed)
            self.assertFalse(list(Path(folder).rglob("*.json")))

    def test_token_limit_retries_only_this_request(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            timings = []
            director.timing_callback = lambda *args: timings.append(args)
            with patch("openai_director.urllib.request.urlopen", side_effect=[stream(status="incomplete", incomplete_details={"reason":"max_output_tokens"}), stream()]) as request:
                director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(json.loads(request.call_args.args[0].data)["max_output_tokens"], 24000)
                self.assertEqual(timings[0][2]["inputTokens"], 200)
                self.assertEqual(timings[0][2]["tokens"], 50)

    def test_cache_write_costs_survive_retry_and_timing_aggregation(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            timings = []
            director.timing_callback = lambda *args: timings.append(args)
            usage = {'input_tokens': 1000, 'output_tokens': 200,
                     'input_tokens_details': {'cached_tokens': 400, 'cache_write_tokens': 300}}
            with patch('openai_director.urllib.request.urlopen', side_effect=[
                stream(status='incomplete', incomplete_details={'reason': 'max_output_tokens'}, usage=usage),
                stream(usage=usage)
            ]):
                director.call('Story analyst', {}, obj({'summary': STR}), lambda *args: None)
            self.assertEqual(timings[0][2]['cacheWriteTokens'], 600)
            self.assertAlmostEqual(timings[0][2]['estimatedUSD'], .000343)

    def test_http_error_never_exposes_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            error = urllib.error.HTTPError("https://api.openai.com", 401, "unauthorized", {}, io.BytesIO(b"private"))
            with patch("openai_director.urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "HTTP 401") as result:
                    director.verify_key()
            self.assertNotIn("sk-", str(result.exception))


class OwnedBillingTest(unittest.TestCase):
    def setUp(self):
        from cost_control import SpendLedger
        self.folder=tempfile.TemporaryDirectory()
        self.root=Path(self.folder.name)
        self.ledger=SpendLedger(self.root/'ledger.json',.1,allow_multiple=True)
        self.director=OpenAIDirector({},self.root/'director')
        self.director.key=lambda:'synthetic-key'
        self.director.spend_ledger=self.ledger

    def tearDown(self): self.folder.cleanup()

    def call(self, gate=lambda *_:None, context=None):
        return self.director.call('Offline test',context or {},obj({'summary':STR}),gate)

    def reserve_other(self):
        self.ledger.reserve({'model':'gpt-6-luna','max_output_tokens':1000,'input':[]},
            request_id='another-request',owner='another-owner')

    def test_success_only_settles_the_request_created_by_this_call(self):
        self.reserve_other()
        with patch('openai_director.urllib.request.urlopen',return_value=stream(id='resp-owned')):
            self.assertEqual(self.call(),{'summary':'complete'})
        state=self.ledger.load()
        self.assertEqual(set(state['requests']),{'another-request'})
        self.assertEqual(len(state['receipts']),1)
        self.assertEqual(state['receipts'][0]['responseId'],'resp-owned')

    def test_cancelled_stream_retains_its_own_unknown_bill_and_other_reservation(self):
        self.reserve_other()
        response=stream()
        def gate(*_):
            if self.director.response is not None: raise InterruptedError('cancelled')
        with patch('openai_director.urllib.request.urlopen',return_value=response):
            with self.assertRaises(InterruptedError): self.call(gate)
        state=self.ledger.load()
        self.assertEqual(len(state['requests']),2)
        self.assertEqual(state['requests']['another-request']['status'],'HELD')
        self.assertEqual(sum(r['status']=='UNKNOWN' for r in state['requests'].values()),1)
        self.assertEqual(state['receipts'],[])
        self.assertTrue(response.closed)
        self.assertIsNone(self.director.response)

    def test_pre_dispatch_cancellation_releases_only_unsubmitted_request(self):
        self.reserve_other()
        def gate(*_):
            if any(r['owner'].startswith('luna-call-') and r['status']=='HELD'
                    for r in self.ledger.load()['requests'].values()):
                raise InterruptedError('cancel before dispatch')
        with patch('openai_director.urllib.request.urlopen') as request:
            with self.assertRaises(InterruptedError): self.call(gate)
            request.assert_not_called()
        state=self.ledger.load()
        self.assertEqual(set(state['requests']),{'another-request'})
        self.assertEqual(len(state['receipts']),1)
        self.assertTrue(state['receipts'][0]['uncharged'])

    def test_server_error_does_not_erase_uncertain_spending(self):
        self.reserve_other()
        error=urllib.error.HTTPError('https://api.openai.com',500,'server failure',{},io.BytesIO(b'{}'))
        with patch('openai_director.urllib.request.urlopen',side_effect=error) as request:
            with self.assertRaisesRegex(RuntimeError,'HTTP 500'): self.call()
            self.assertEqual(request.call_count,1)
        state=self.ledger.load()
        self.assertEqual(len(state['requests']),2)
        self.assertEqual(state['receipts'],[])
        self.assertEqual(sum(r['status']=='UNKNOWN' for r in state['requests'].values()),1)

    def test_rejected_auth_request_is_uncharged_without_releasing_another_owner(self):
        self.reserve_other()
        error=urllib.error.HTTPError('https://api.openai.com',401,'invalid key',{},
            io.BytesIO(b'{"error":{"message":"synthetic-key rejected"}}'))
        with patch('openai_director.urllib.request.urlopen',side_effect=error):
            with self.assertRaisesRegex(RuntimeError,'HTTP 401') as result: self.call()
        self.assertNotIn('synthetic-key',str(result.exception))
        state=self.ledger.load()
        self.assertEqual(set(state['requests']),{'another-request'})
        self.assertTrue(state['receipts'][0]['uncharged'])
        self.assertEqual(state['spentUSD'],0)

    def test_credential_rotation_during_http_failure_does_not_expose_the_submitted_key(self):
        old_key='synthetic-original-secret'
        self.director.key=lambda:old_key
        def response(request,**_):
            self.assertEqual(request.get_header('Authorization'),'Bearer '+old_key)
            self.director.key=lambda:'synthetic-new-secret'
            raise urllib.error.HTTPError('https://api.openai.com',401,'invalid key',{},
                io.BytesIO(json.dumps({'error':{'message':old_key+' rejected'}}).encode()))
        with patch('openai_director.urllib.request.urlopen',side_effect=response):
            with self.assertRaisesRegex(RuntimeError,'HTTP 401') as result: self.call()
        self.assertNotIn(old_key,str(result.exception))
        self.assertNotIn('synthetic-new-secret',str(result.exception))
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(self.ledger.load()['spentUSD'],0)

    def test_unreadable_rotated_credentials_fall_back_to_a_safe_http_error(self):
        def response(*_,**__):
            self.director.key=lambda: (_ for _ in ()).throw(ValueError('malformed credential fixture'))
            raise urllib.error.HTTPError('https://api.openai.com',401,'invalid key',{},
                io.BytesIO(b'{"error":{"message":"synthetic-key rejected"}}'))
        with patch('openai_director.urllib.request.urlopen',side_effect=response):
            with self.assertRaisesRegex(RuntimeError,'HTTP 401') as result: self.call()
        self.assertNotIn('synthetic-key',str(result.exception))
        self.assertNotIn('malformed credential fixture',str(result.exception))
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(self.ledger.load()['spentUSD'],0)

    def test_created_response_id_survives_lost_stream_acknowledgement(self):
        response=io.BytesIO(b'data: {"type":"response.created","response":{"id":"resp-survives"}}\n\n')
        with patch('openai_director.urllib.request.urlopen',return_value=response) as request:
            with self.assertRaisesRegex(RuntimeError,'remains reserved'): self.call()
            self.assertEqual(request.call_count,1)
        state=self.ledger.load()
        pending=next(iter(state['requests'].values()))
        self.assertEqual(pending['responseId'],'resp-survives')
        self.assertEqual(pending['status'],'UNKNOWN')
        self.assertGreater(state['liabilityUSD'],0)

    def test_final_usage_can_be_missing_without_losing_valid_output_or_becoming_free(self):
        self.director.spend_ledger.allow_multiple=False
        with patch('openai_director.urllib.request.urlopen',return_value=stream(usage=None)) as request:
            self.assertEqual(self.call(),{'summary':'complete'})
            self.assertEqual(self.call(),{'summary':'complete'})
            with self.assertRaisesRegex(RuntimeError,'unresolved'): self.call(context={'new':'synthetic'})
            self.assertEqual(request.call_count,1)
        self.assertEqual(next(iter(self.ledger.load()['requests'].values()))['status'],'UNKNOWN')
        self.assertGreater(self.ledger.load()['liabilityUSD'],0)

    def test_incomplete_unknown_usage_never_buys_an_automatic_retry(self):
        with patch('openai_director.urllib.request.urlopen',return_value=stream(status='incomplete',usage={},
                incomplete_details={'reason':'max_output_tokens'})) as request:
            with self.assertRaisesRegex(RuntimeError,'reconcile before retrying'): self.call()
            self.assertEqual(request.call_count,1)
        self.assertEqual(len(self.ledger.load()['requests']),1)

    def test_switching_director_ledger_mid_request_cannot_reassign_its_charge(self):
        from cost_control import SpendLedger
        other=SpendLedger(self.root/'other-ledger.json',.1)
        other.reserve({'model':'gpt-6-luna','max_output_tokens':1000,'input':[]})
        def response(*_,**__):
            self.director.spend_ledger=other
            return stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response): self.call()
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(len(self.ledger.load()['receipts']),1)
        self.assertIsNotNone(other.load()['pending'])
        self.assertEqual(other.load()['receipts'],[])

    def test_mutable_director_rejects_reentry_and_recovers_after_completion(self):
        checked=[]
        def gate(*_):
            if self.director.response is not None and not checked:
                checked.append(True)
                with self.assertRaisesRegex(RuntimeError,'independent request contexts'):
                    self.call(context={'second':'call'})
        with patch('openai_director.urllib.request.urlopen',return_value=stream()) as request:
            self.call(gate)
            self.assertEqual(request.call_count,1)
            self.assertEqual(self.call(),{'summary':'complete'})
        self.assertEqual(checked,[True])

    def test_receive_gate_collects_an_already_submitted_response_while_paused(self):
        paused=threading.Event()
        release=threading.Event()
        normal_gate_blocked=threading.Event()
        received=[]
        def gate(*_):
            if paused.is_set():
                normal_gate_blocked.set()
                release.wait(timeout=3)
        def receive_gate(message): received.append(message)
        self.director.receive_gate=receive_gate
        def response(*_,**__):
            paused.set()
            return stream(id='resp-paused')
        with patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.call,gate)
            try:
                self.assertEqual(future.result(timeout=1),{'summary':'complete'})
                self.assertTrue(paused.is_set())
                self.assertFalse(normal_gate_blocked.is_set())
            finally:
                release.set()
        self.assertGreaterEqual(len(received),2)
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(self.ledger.load()['receipts'][0]['responseId'],'resp-paused')
        self.assertTrue(list((self.root/'director'/'director-cache').glob('*.json')))

    def test_receive_gate_never_bypasses_a_pause_before_dispatch(self):
        paused=threading.Event();paused.set()
        entered=threading.Event();release=threading.Event()
        def gate(*_):
            if paused.is_set():
                entered.set();release.wait(timeout=3)
        self.director.receive_gate=lambda *_:None
        with patch('openai_director.urllib.request.urlopen',return_value=stream()) as request,ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.call,gate)
            try:
                self.assertTrue(entered.wait(timeout=1))
                request.assert_not_called()
                self.assertFalse(future.done())
            finally:
                paused.clear();release.set()
            self.assertEqual(future.result(timeout=2),{'summary':'complete'})
            self.assertEqual(request.call_count,1)

    def test_receive_gate_finishes_incomplete_usage_but_pauses_before_any_retry(self):
        paused=threading.Event();entered=threading.Event();release=threading.Event()
        def gate(*_):
            if paused.is_set():
                entered.set();release.wait(timeout=3)
        self.director.receive_gate=lambda *_:None
        responses=[stream(status='incomplete',incomplete_details={'reason':'max_output_tokens'}),stream()]
        def response(*_,**__):
            if len(responses)==2:paused.set()
            return responses.pop(0)
        with patch('openai_director.urllib.request.urlopen',side_effect=response) as request,ThreadPoolExecutor(1) as pool:
            future=pool.submit(self.call,gate)
            try:
                self.assertTrue(entered.wait(timeout=2))
                self.assertEqual(request.call_count,1)
                self.assertFalse(future.done())
                state=self.ledger.load()
                self.assertIsNone(state['pending'])
                self.assertEqual(len(state['receipts']),1)
                self.assertTrue(state['receipts'][0]['usageConfirmed'])
            finally:
                paused.clear();release.set()
            self.assertEqual(future.result(timeout=2),{'summary':'complete'})
            self.assertEqual(request.call_count,2)
        self.assertEqual(len(self.ledger.load()['receipts']),2)

    def test_receive_gate_still_honors_cancellation_and_retains_its_liability(self):
        self.reserve_other()
        def receive_gate(*_):raise InterruptedError('cancel current receive')
        self.director.receive_gate=receive_gate
        response=stream()
        with patch('openai_director.urllib.request.urlopen',return_value=response):
            with self.assertRaisesRegex(InterruptedError,'cancel current receive'):self.call()
        self.assertTrue(response.closed)
        state=self.ledger.load()
        self.assertEqual(state['requests']['another-request']['status'],'HELD')
        self.assertEqual(sum(r['status']=='UNKNOWN' for r in state['requests'].values()),1)
        self.assertEqual(state['receipts'],[])

    def test_receive_gate_is_captured_before_a_request_can_rebind_the_adapter(self):
        received=[]
        self.director.receive_gate=lambda message:received.append(message)
        def response(*_,**__):
            def changed(*_):raise AssertionError('The current call followed a rebound receive gate')
            self.director.receive_gate=changed
            return stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response):
            self.assertEqual(self.call(),{'summary':'complete'})
        self.assertTrue(received)

    def test_invalid_receive_gate_fails_before_credentials_cache_or_dispatch(self):
        self.director.receive_gate=0
        with patch('openai_director.urllib.request.urlopen') as request,patch.object(self.director,'key') as key:
            with self.assertRaisesRegex(ValueError,'receive gate must be callable'):self.call()
            request.assert_not_called();key.assert_not_called()
        self.assertFalse(self.ledger.path.exists())

    def test_refusal_collects_its_final_usage_without_an_automatic_retry(self):
        events=[{'type':'response.created','response':{'id':'resp-declined'}},
            {'type':'response.refusal.delta','delta':'Synthetic declined response.'},
            {'type':'response.refusal.done','refusal':'Synthetic declined response.'},
            {'type':'response.incomplete','response':{'id':'resp-declined','status':'incomplete',
                'incomplete_details':{'reason':'max_output_tokens'},
                'usage':{'input_tokens':100,'output_tokens':25}}}]
        response=io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
        with patch('openai_director.urllib.request.urlopen',return_value=response) as request:
            with self.assertRaisesRegex(RuntimeError,'declined'):self.call()
            self.assertEqual(request.call_count,1)
        state=self.ledger.load()
        self.assertIsNone(state['pending'])
        self.assertEqual(len(state['receipts']),1)
        self.assertEqual(state['receipts'][0]['responseId'],'resp-declined')
        self.assertTrue(state['receipts'][0]['usageConfirmed'])
        self.assertFalse(list((self.root/'director').rglob('*.json')))

    def test_refusal_without_final_usage_retains_its_unknown_reservation(self):
        events=[{'type':'response.created','response':{'id':'resp-declined-unknown'}},
            {'type':'response.refusal.done','refusal':'Synthetic declined response.'},
            {'type':'response.completed','response':{'id':'resp-declined-unknown','status':'completed','usage':None}}]
        response=io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
        with patch('openai_director.urllib.request.urlopen',return_value=response) as request:
            with self.assertRaisesRegex(RuntimeError,'declined'):self.call()
            self.assertEqual(request.call_count,1)
        state=self.ledger.load()
        self.assertEqual(next(iter(state['requests'].values()))['status'],'UNKNOWN')
        self.assertEqual(state['receipts'],[])
        self.assertFalse(list((self.root/'director').rglob('*.json')))

    def test_two_synthetic_adapters_keep_separate_receipts_in_one_shared_budget(self):
        second=OpenAIDirector({},self.root/'second')
        second.key=lambda:'synthetic-key'
        second.spend_ledger=self.ledger
        barrier=threading.Barrier(2)
        def response(*_,**__):
            barrier.wait(timeout=5)
            return stream()
        with patch('openai_director.urllib.request.urlopen',side_effect=response), ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.call)
            other=pool.submit(second.call,'Offline test',{},obj({'summary':STR}),lambda *_:None)
            self.assertEqual(first.result(timeout=10),{'summary':'complete'})
            self.assertEqual(other.result(timeout=10),{'summary':'complete'})
        state=self.ledger.load()
        self.assertIsNone(state['pending'])
        self.assertEqual(len(state['receipts']),2)
        self.assertEqual(len({r['owner'] for r in state['receipts']}),2)
        self.assertEqual(len({r['requestId'] for r in state['receipts']}),2)

    def test_independent_adapters_cannot_collide_when_publishing_the_same_cache(self):
        second=OpenAIDirector({},self.root/'director')
        second.key=lambda:'synthetic-key'
        second.spend_ledger=self.ledger
        network_barrier=threading.Barrier(2)
        publish_barrier=threading.Barrier(2)
        staged=[]
        cache_fds=set()
        real_tempfile=tempfile.NamedTemporaryFile
        real_fsync=os.fsync
        def response(*_,**__):
            network_barrier.wait(timeout=5)
            return stream()
        def temporary_file(*args,**kwargs):
            handle=real_tempfile(*args,**kwargs)
            if Path(handle.name).parent.name=='director-cache':
                staged.append(Path(handle.name))
                cache_fds.add(handle.fileno())
            return handle
        def sync(fd):
            real_fsync(fd)
            if fd in cache_fds:
                cache_fds.remove(fd)
                publish_barrier.wait(timeout=5)
        with patch('openai_director.urllib.request.urlopen',side_effect=response), \
                patch('openai_director.tempfile.NamedTemporaryFile',temporary_file), \
                patch('openai_director.os.fsync',sync), ThreadPoolExecutor(2) as pool:
            first=pool.submit(self.call)
            other=pool.submit(second.call,'Offline test',{},obj({'summary':STR}),lambda *_:None)
            self.assertEqual(first.result(timeout=10),{'summary':'complete'})
            self.assertEqual(other.result(timeout=10),{'summary':'complete'})
        self.assertEqual(len(set(staged)),2)
        caches=list((self.root/'director'/'director-cache').glob('*.json'))
        self.assertEqual(len(caches),1)
        self.assertEqual(json.loads(caches[0].read_text()),{'summary':'complete'})
        self.assertFalse(list(caches[0].parent.glob('*.writing')))
        self.assertEqual(len(self.ledger.load()['receipts']),2)
        with patch('openai_director.urllib.request.urlopen') as request:
            self.assertEqual(self.call(),{'summary':'complete'})
            request.assert_not_called()

    def test_cache_fsync_failure_retains_the_charge_and_removes_its_staged_output(self):
        real_tempfile=tempfile.NamedTemporaryFile
        real_fsync=os.fsync
        cache_fds=set()
        def temporary_file(*args,**kwargs):
            handle=real_tempfile(*args,**kwargs)
            if Path(handle.name).parent.name=='director-cache': cache_fds.add(handle.fileno())
            return handle
        def sync(fd):
            if fd in cache_fds: raise OSError('synthetic cache durability failure')
            return real_fsync(fd)
        with patch('openai_director.urllib.request.urlopen',return_value=stream()), \
                patch('openai_director.tempfile.NamedTemporaryFile',temporary_file), \
                patch('openai_director.os.fsync',sync):
            with self.assertRaisesRegex(OSError,'cache durability failure'): self.call()
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(len(self.ledger.load()['receipts']),1)
        self.assertGreater(self.ledger.load()['spentUSD'],0)
        self.assertFalse(list((self.root/'director'/'director-cache').glob('*')))
        self.assertIsNone(self.director.response)

    def test_cache_publish_failure_keeps_a_previous_valid_result_and_the_charge(self):
        real_replace=Path.replace
        def replace(path,target):
            if Path(target).parent.name=='director-cache':
                # Model another publisher saving a valid result during our call.
                Path(target).write_text('{"summary":"previous valid result"}')
                raise OSError('synthetic cache publication failure')
            return real_replace(path,target)
        with patch('openai_director.urllib.request.urlopen',return_value=stream()), \
                patch.object(Path,'replace',replace):
            with self.assertRaisesRegex(OSError,'cache publication failure'): self.call()
        cache=next((self.root/'director'/'director-cache').glob('*.json'))
        self.assertEqual(json.loads(cache.read_text()),{'summary':'previous valid result'})
        self.assertFalse(list(cache.parent.glob('*.writing')))
        self.assertEqual(len(self.ledger.load()['receipts']),1)
        self.assertIsNone(self.ledger.load()['pending'])
        with patch('openai_director.urllib.request.urlopen') as request:
            self.assertEqual(self.call(),{'summary':'previous valid result'})
            request.assert_not_called()

    def test_finalized_invalid_schema_is_charged_without_saving_bad_output(self):
        with patch('openai_director.urllib.request.urlopen',return_value=stream('{"summary":12}')):
            with self.assertRaises(ValueError): self.call()
        self.assertIsNone(self.ledger.load()['pending'])
        self.assertEqual(len(self.ledger.load()['receipts']),1)
        self.assertFalse(list((self.root/'director').rglob('*.json')))


if __name__ == "__main__":
    unittest.main()
