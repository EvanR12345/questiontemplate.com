"""Durable admission hooks and budget backpressure with all transport mocked."""
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
import comfy_recovery_test as comfy
import openai_director_test as luna
from cost_control import SpendLedger
from director_provider import obj,STR
from image_provider import RemoteGenerationUncertain
from openai_director import OpenAIDirector
from studio_execution import ExecutionJournal


class ObserverTest(unittest.TestCase):
    def test_luna_admission_is_durable_before_http_and_response_id_survives(self):
        with tempfile.TemporaryDirectory() as root:
            journal=ExecutionJournal(Path(root)/'executions.sqlite3')
            context=journal.admit('job','project',None,None,'qc',0,'input',{})
            director=OpenAIDirector({},root);director.key=lambda:'synthetic-key'
            director.spend_ledger=SpendLedger(Path(root)/'ledger.json',.02)
            director.execution_observer=lambda event,details:journal.observe(context.identity,event,details)
            def network(*args,**kwargs):
                row=journal.snapshot('job')[0]
                self.assertEqual(len(row['operations']),1)
                self.assertEqual(next(iter(row['operations'].values()))['state'],'SUBMITTED')
                return luna.stream(id='known-response')
            with patch('openai_director.urllib.request.urlopen',side_effect=network):
                director.call('QC',{},obj({'summary':STR}),lambda *_:None)
            journal.finish(context.identity,'COMPLETE')
            op=next(iter(journal.snapshot('job')[0]['operations'].values()))
            self.assertEqual(op['state'],'COMPLETED');self.assertEqual(op['remoteId'],'known-response')
            journal.close()

    def test_luna_observer_failure_before_dispatch_preserves_liability_and_sends_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            director=OpenAIDirector({},root);director.key=lambda:'synthetic-key'
            director.spend_ledger=SpendLedger(Path(root)/'ledger.json',.02)
            def observer(*args):raise OSError('durable storage unavailable')
            director.execution_observer=observer
            with patch('openai_director.urllib.request.urlopen') as network:
                with self.assertRaises(OSError):director.call('QC',{},obj({'summary':STR}),lambda *_:None)
                network.assert_not_called()
            self.assertEqual(next(iter(director.spend_ledger.load()['requests'].values()))['status'],'UNKNOWN')

    def test_comfy_observer_failure_after_acceptance_or_retrieval_cannot_trigger_paid_retry(self):
        fixture=comfy.RecoveryTest()
        for failure in ('accepted','completed'):
            with self.subTest(failure=failure):
                events=[]
                def observer(event,details):
                    events.append(event)
                    if event==failure:raise RuntimeError('Journal write failed')
                with patch('image_provider.request_json',side_effect=[{'prompt_id':'accepted'},fixture.history()]) as calls, \
                     patch('image_provider.urllib.request.urlopen',return_value=fixture.png()):
                    with self.assertRaises(RemoteGenerationUncertain) as error:
                        fixture.provider().generateImage(fixture.request() | {'_executionObserver':observer},lambda *_:None)
                    self.assertEqual(error.exception.prompt_id,'accepted')
                    self.assertEqual(sum(call.args[0].endswith('/prompt') for call in calls.call_args_list),1)
                    self.assertEqual(events[0],'submitting')

    def test_comfy_known_failed_history_is_terminal_and_retains_prompt_identity(self):
        fixture=comfy.RecoveryTest();events=[]
        history={'accepted':{'status':{'status_str':'error'},'outputs':{}}}
        with patch('image_provider.request_json',side_effect=[{'prompt_id':'accepted'},history]):
            with self.assertRaisesRegex(RuntimeError,'ComfyUI failed'):
                fixture.provider().generateImage(fixture.request() | {
                    '_executionObserver':lambda event,details:events.append((event,details))},lambda *_:None)
        self.assertEqual([event for event,_ in events],['submitting','accepted','failed'])
        self.assertEqual(events[1][1]['remoteId'],'accepted')

    def test_shared_budget_waits_for_active_usage_then_admits_without_raising_cap(self):
        with tempfile.TemporaryDirectory() as root:
            first=OpenAIDirector({},root);second=OpenAIDirector({},root)
            for director in (first,second):
                director.key=lambda:'synthetic-key';director.wait_for_budget=True
                director.spend_ledger=SpendLedger(Path(root)/'ledger.json',.0068,allow_multiple=True)
            submitted=threading.Event();waiting=threading.Event();release=threading.Event();calls=[]
            def network(request,*args,**kwargs):
                calls.append(request)
                if len(calls)==1:
                    submitted.set()
                    if not release.wait(3):raise RuntimeError('Test release missing')
                return luna.stream()
            def gate(message):
                if message=='Waiting for reserved API usage to settle':waiting.set()
            with patch('openai_director.urllib.request.urlopen',side_effect=network),ThreadPoolExecutor(max_workers=2) as pool:
                one=pool.submit(first.call,'one',{},obj({'summary':STR}),lambda *_:None)
                self.assertTrue(submitted.wait(2))
                two=pool.submit(second.call,'two',{},obj({'summary':STR}),gate)
                self.assertTrue(waiting.wait(2));self.assertEqual(len(calls),1)
                release.set();one.result(3);two.result(3)
            state=first.spend_ledger.load();self.assertFalse(state['requests'])
            self.assertEqual(len(state['receipts']),2);self.assertEqual(state['capNanoUSD'],6800000)


if __name__=='__main__':unittest.main()
