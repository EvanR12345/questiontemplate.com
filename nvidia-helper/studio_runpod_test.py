import json,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio_runpod import ProductionGPU,ManagedGPUError,RunpodClient,reconcile

class Client:
    def __init__(self,status='EXITED',fail_start=False,fail_stop=False):
        self.status=status;self.calls=[];self.fail_start=fail_start;self.fail_stop=fail_stop
    def request(self,pod,action=None):
        self.calls.append(action)
        if action=='start':
            self.status='RUNNING'
            if self.fail_start:raise ManagedGPUError('Lost start response')
        if action=='stop':
            if self.fail_stop:raise ManagedGPUError('offline')
            self.status='EXITED'
        return {'id':pod,'status':self.status,'actions':['start'] if self.status=='EXITED' else ['stop'],
                'cost':1.0,'ssh':{'direct':{'host':'192.0.2.1','port':22,'username':'root'}}}

class RentalTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'lease.json'
        self.config={'runpodManagedPodId':'pod-test-123'};self.client=Client();self.clock=[100]
        self.provider=SimpleNamespace(healthCheck=lambda:{'installed':True})
    def tearDown(self):self.folder.cleanup()
    def rental(self,**kw):
        return ProductionGPU(self.config,self.path,'project',{'gpuBudgetUSD':1},self.provider,lambda *_:None,
            client=self.client,connector=lambda *_:None,clock=lambda:self.clock[0],**kw)
    def test_start_is_lazy_once_and_verified_stop_includes_startup_time(self):
        r=self.rental();self.assertEqual(self.client.calls,[])
        try:
            r.ensure();r.ensure();self.assertEqual(self.client.calls.count('start'),1)
            self.clock[0]+=36;r.stop()
            self.assertEqual(self.client.calls.count('stop'),1)
            self.assertAlmostEqual(r.state['estimatedUSD'],.01)
            self.assertEqual(json.loads(self.path.read_text())['status'],'STOPPED')
            with self.assertRaisesRegex(ManagedGPUError,'already closed'):r.ensure()
        finally:r.stop()
    def test_externally_running_pod_is_neither_adopted_nor_stopped(self):
        self.client.status='RUNNING';r=self.rental()
        with self.assertRaisesRegex(ManagedGPUError,'separately running'):r.ensure()
        r.stop();self.assertNotIn('start',self.client.calls);self.assertNotIn('stop',self.client.calls)
    def test_lost_start_response_still_owns_and_stops_exact_worker(self):
        self.client.fail_start=True;r=self.rental()
        with self.assertRaisesRegex(ManagedGPUError,'Lost start'):r.ensure()
        self.assertEqual(r.state['status'],'STOPPED');self.assertEqual(self.client.calls.count('start'),1)
        self.assertEqual(self.client.calls.count('stop'),1)
    def test_unknown_shutdown_blocks_another_purchase_until_reconciliation(self):
        r=self.rental();r.ensure();self.client.fail_stop=True
        with self.assertRaisesRegex(ManagedGPUError,'shutdown could not'):r.stop()
        with self.assertRaisesRegex(ManagedGPUError,'reconciliation'):self.rental()
        self.client.fail_stop=False
        self.assertEqual(reconcile(self.config,self.path,self.client)['status'],'STOPPED')
    def test_watchdog_can_stop_during_paused_startup_gate(self):
        entered=threading.Event();resume=threading.Event();errors=[]
        r=self.rental()
        def gate(message):
            if 'private connection' in message:entered.set();resume.wait(4)
        r.gate=gate
        def run():
            try:r.ensure()
            except Exception as e:errors.append(e)
        thread=threading.Thread(target=run);thread.start()
        try:
            self.assertTrue(entered.wait(2));self.clock[0]=r.deadline+1
            self.assertTrue(r.finished.wait(2))
            self.assertEqual(self.client.status,'EXITED')
        finally:resume.set();thread.join(3);r.stop()
        self.assertTrue(errors);self.assertEqual(r.state['status'],'STOPPED')
    def test_invalid_cap_does_not_start_worker(self):
        for cap in (None,False,-1,float('nan'),.001):
            r=self.rental();r.budget={'gpuBudgetUSD':cap}
            with self.assertRaises(ManagedGPUError):r.ensure()
            self.assertNotIn('start',self.client.calls)
    def test_rest_actions_are_limited_to_saved_id_and_start_stop(self):
        with self.assertRaises(ValueError):RunpodClient({}).request('pod-test-123','terminate')
        with self.assertRaises(ValueError):RunpodClient({}).request('../other-pod')

    def test_pipeline_finalizer_stops_owned_rental_on_success_cancel_and_failure(self):
        from studio_service import StudioService,JobCancelled
        for error in (None,JobCancelled(),ValueError('source changed')):
            service=StudioService.__new__(StudioService);service.config=self.config
            service.store=Mock();service.store.root=Path(self.folder.name)
            service.store.load.return_value={'settings':{'image':{'provider':'comfyui'},'cloudWindow':{'gpuBudgetUSD':1}}}
            service.providers={'comfyui':self.provider};service.gate=Mock()
            lease=Mock()
            service._produce_story=Mock(side_effect=error,return_value='finished')
            with patch('studio_runpod.ProductionGPU',return_value=lease):
                if error:
                    with self.assertRaises(type(error)):service.produce_story('project',{})
                else:self.assertEqual(service.produce_story('project',{}),'finished')
            lease.stop.assert_called_once_with('production ended')
            self.assertIsNone(service._production_gpu)

    def test_managed_receipts_keep_failed_rental_cost_without_duplicate_history(self):
        from studio_service import StudioService
        from studio_data import ProjectStore,new_project
        service=StudioService.__new__(StudioService);service.store=ProjectStore(self.folder.name)
        p=service.store.save(new_project());pid=p['id']
        receipt={'status':'STOPPED','started':100,'stopped':136,'podId':'pod-test-123',
                 'reason':'production failed','estimatedUSD':.01,'spentByProject':{pid:.01}}
        service.record_gpu_state(pid,receipt);service.record_gpu_state(pid,receipt)
        run=service.store.load(pid)['production']
        self.assertEqual(len(run['gpuRentalHistory']),1)
        self.assertEqual(run['costs']['gpuWindowEstimatedUSD'],.01)

if __name__=='__main__':unittest.main()
