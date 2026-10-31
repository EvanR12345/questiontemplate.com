"""Same generation/QC/repair work under one bounded managed GPU lease."""
import io
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import studio_overlap_test as fixtures
import studio_dense_phase_test as dense_fixtures
from studio_overlap import CloudStoryOverlap

class LeaseTests(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown
    prepared=dense_fixtures.DensePhaseTest.prepared
    response=staticmethod(dense_fixtures.DensePhaseTest.response)
    def continuous(self,count=8):
        overlap=self.prepared(count)
        self.service.config['studioDenseLeaseContinuity']=True
        overlap.main.config['studioDenseLeaseContinuity']=True
        return overlap
    def test_paid_qc_tail_keeps_owned_worker_then_stops_after_all_same_checks(self):
        overlap=self.continuous();release=threading.Event();started=threading.Event();seen=[]
        def response(request,**kw):
            index=len(seen);seen.append(index);raw=self.response(index).getvalue()
            class Response(io.BytesIO):
                def __iter__(stream):
                    started.set()
                    if not release.wait(8):raise TimeoutError('controlled QC release')
                    yield from io.BytesIO(raw)
            return Response(raw)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response),ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(overlap.main.run_dense_image_phase,self.project['id'])
                try:
                    self.assertTrue(started.wait(4));self.assertTrue(self.provider.sixth.wait(4))
                    self.assertFalse(self.stopped.is_set());self.assertEqual(self.rental.state['status'],'RUNNING')
                    self.assertFalse(future.done())
                finally:release.set()
                future.result(timeout=10)
            self.assertEqual(len(self.provider.requests),8);self.assertEqual(len(seen),8)
            self.assertEqual((self.rental.starts,self.rental.stops),(1,1))
            self.assertEqual(overlap.deferred_qc.queue.snapshot()['counts'],{'COMPLETE':8})
        finally:release.set();overlap.close('COMPLETE')
    def test_same_two_repair_limit_and_three_checks_use_only_one_worker_start(self):
        overlap=self.continuous(1)
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(automaticRepair=True,maxImageRetries=2))
        overlap.close('COMPLETE');self.project=self.service.store.load(self.project['id'])
        overlap=CloudStoryOverlap(self.service,self.project,{'overlap':True,'generationStrategy':'dense'})
        seen=[]
        def response(request,**kw):
            index=len(seen);seen.append(index);return self.response(index,passed=index>=2)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('studio_service.repair_budget_reason',return_value=None), \
                 patch('studio_qc.repair_budget_reason',return_value=None), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                overlap.main.run_dense_image_phase(self.project['id'])
            self.assertEqual((len(self.provider.requests),len(seen)),(3,3))
            self.assertEqual((self.rental.starts,self.rental.stops),(1,1))
            shot=self.service.store.load(self.project['id'])['chapters'][0]['scenes'][0]['shots'][0]
            self.assertEqual(shot['automaticRepairAttempts'],2);self.assertTrue(shot['qc']['pass'])
        finally:overlap.close('COMPLETE')
    def test_unknown_qc_stops_lease_and_preserves_unknown_request(self):
        overlap=self.continuous(1)
        try:
            with patch('studio_service.require_shot',return_value={'status':'PASSED'}), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',return_value=io.BytesIO(b'data: {"type":"response.created","response":{"id":"unresolved"}}\n\n')) as request:
                with self.assertRaises(RuntimeError):overlap.main.run_dense_image_phase(self.project['id'])
            self.assertEqual(request.call_count,1);self.assertEqual(self.rental.state['status'],'STOPPED')
            self.assertEqual((self.rental.starts,self.rental.stops),(1,1))
            state=json.loads((self.service.store.folder(self.project['id'])/'api-cost-ledger.json').read_text(encoding='utf-8'))
            self.assertTrue(state['requests']);self.assertGreater(state['reservedUSD'],0)
        finally:overlap.close('FAILED')

if __name__=='__main__':unittest.main()
