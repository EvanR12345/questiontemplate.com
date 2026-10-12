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
    def test_new_worker_lifetime_clears_remote_reference_upload_names_once(self):
        from image_provider import ComfyImageProvider
        provider=ComfyImageProvider({});provider._uploads={'old-sha':'missing-after-restart.png'}
        observations=[]
        def health():
            observations.append(dict(provider._uploads));return {'installed':True}
        provider.healthCheck=health;self.provider=provider;r=self.rental()
        try:
            r.ensure();self.assertEqual(observations,[{}])
            provider._uploads['new-sha']='current-worker.png';r.ensure()
            self.assertEqual(provider._uploads,{'new-sha':'current-worker.png'})
        finally:r.stop()

    def test_externally_running_pod_is_neither_adopted_nor_stopped(self):
        self.client.status='RUNNING';r=self.rental()
        with self.assertRaisesRegex(ManagedGPUError,'separately running'):r.ensure()
        r.stop();self.assertNotIn('start',self.client.calls);self.assertNotIn('stop',self.client.calls)

    def test_new_rental_does_not_inherit_old_stopped_time_or_cost(self):
        self.path.write_text(json.dumps({'status':'STOPPED','started':1,'stopped':9,'ready':2,
            'estimatedUSD':.9,'seconds':8,'spentByProject':{'project':.1}}))
        r=self.rental()
        try:
            r.ensure()
            self.assertNotIn('stopped',r.state)
            self.assertNotIn('estimatedUSD',r.state)
            self.assertEqual(r.state['ready'],100)
            self.assertEqual(r.state['spentByProject'],{'project':.1})
            self.clock[0]+=36;r.stop()
            self.assertAlmostEqual(r.state['estimatedUSD'],.01)
            self.assertAlmostEqual(r.state['spentByProject']['project'],.11)
        finally:r.stop()
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

    def test_prepared_pool_picks_cheapest_exact_gpu_without_starting_others(self):
        self.config.update(runpodPreparedPodIds=['pod-test-123','pod-test-456','pod-test-789'],
                           runpodRequiredGPUType='NVIDIA GeForce RTX 5090',runpodMaxHourlyUSD=1)
        rows={pid:{'id':pid,'status':'EXITED','actions':['start'],'cost':cost,'gpu':{'id':gpu}}
              for pid,cost,gpu in [('pod-test-123',.8,'NVIDIA GeForce RTX 5090'),
                                  ('pod-test-456',.6,'NVIDIA GeForce RTX 5090'),
                                  ('pod-test-789',.2,'NVIDIA RTX PRO 6000')]}
        r=self.rental();r.client=Mock();r.client.request.side_effect=lambda pid:rows[pid]
        self.assertEqual(r.select_prepared_worker()['id'],'pod-test-456')
        self.assertTrue(all(len(c.args)==1 for c in r.client.request.call_args_list))

    def test_unavailable_exact_gpu_never_substitutes_or_rents(self):
        self.config.update(runpodRequiredGPUType='NVIDIA GeForce RTX 5090',runpodAvailabilityWaitSeconds=0)
        r=self.rental()
        with self.assertRaisesRegex(ManagedGPUError,'No eligible'):r.ensure()
        self.assertNotIn('start',self.client.calls)

    def test_availability_wait_is_cancellable_before_any_purchase(self):
        self.config.update(runpodRequiredGPUType='NVIDIA GeForce RTX 5090',runpodAvailabilityWaitSeconds=60)
        r=self.rental();calls=[]
        def gate(message):
            calls.append(message)
            if len(calls)>3:raise RuntimeError('cancelled')
        r.gate=gate
        with self.assertRaisesRegex(RuntimeError,'cancelled'):r.ensure()
        self.assertNotIn('start',self.client.calls)

    def test_reconcile_saved_pool_worker_not_current_primary(self):
        self.config['runpodPreparedPodIds']=['pod-test-123','pod-test-456']
        self.path.write_text(json.dumps({'podId':'pod-test-456','status':'STARTING','started':1,
            'hourlyUSD':1,'project':'project'}))
        self.assertEqual(reconcile(self.config,self.path,self.client)['status'],'STOPPED')

    def test_regional_stock_does_not_confuse_other_regions_with_prepared_host(self):
        self.config.update(runpodRequiredGPUType='NVIDIA GeForce RTX 5090',runpodCheckRegionalStock=True)
        r=self.rental();r.client=Mock()
        r.client.request.return_value={'id':'pod-test-123','status':'EXITED','actions':['start'],'cost':.9,
            'gpu':{'id':'NVIDIA GeForce RTX 5090'},'dataCenterId':'US-NE-1'}
        r.client.gpu_catalog.return_value=[{'gpuId':'NVIDIA GeForce RTX 5090',
            'dataCenterAvailability':[{'dataCenterId':'EU-RO-1','stockStatus':'High'}]}]
        with self.assertRaisesRegex(ManagedGPUError,'No eligible'):r.ensure()
        self.assertTrue(all(len(call.args)==1 for call in r.client.request.call_args_list))

    def test_changed_gpu_after_start_is_stopped_before_provider_connection(self):
        self.config.update(runpodRequiredGPUType='NVIDIA GeForce RTX 5090')
        original=self.client.request
        def request(pid,action=None):
            row=original(pid,action);row['gpu']={'id':'NVIDIA GeForce RTX 5090' if self.client.status=='EXITED' else 'different GPU'}
            return row
        self.client.request=request;r=self.rental();r.connector=Mock()
        with self.assertRaisesRegex(ManagedGPUError,'differs'):r.ensure()
        r.connector.assert_not_called();self.assertEqual(r.state['status'],'STOPPED')

    def test_live_stock_failure_does_not_start_a_worker(self):
        self.config.update(runpodRequiredGPUType='NVIDIA GeForce RTX 5090',runpodCheckRegionalStock=True)
        r=self.rental();r.client=Mock();r.client.gpu_catalog.side_effect=ManagedGPUError('inventory unavailable')
        with self.assertRaisesRegex(ManagedGPUError,'inventory unavailable'):r.ensure()
        r.client.request.assert_not_called()

    def test_explicit_existing_cli_control_keeps_key_out_of_arguments_and_errors(self):
        import io,subprocess
        folder=Path(self.folder.name);cli=folder/'runpodctl.exe';cli.touch()
        keyfile=folder/'action.txt';keyfile.write_text('rpa_private-existing-test')
        readfile=folder/'read.json';readfile.write_text(json.dumps({'runpodApiKey':'read-only-test'}))
        config={'runpodCLI':str(cli),'runpodActionKeyFile':str(keyfile),'runpodKeyFile':str(readfile)}
        client=RunpodClient(config)
        body=json.dumps({'id':'pod-test-123','status':'RUNNING'}).encode()
        with patch('studio_runpod.subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')) as run, \
                patch('studio_runpod.urllib.request.urlopen',return_value=io.BytesIO(body)) as read:
            self.assertEqual(client.request('pod-test-123','start')['status'],'RUNNING')
            self.assertEqual(run.call_args.args[0],[str(cli),'pod','start','pod-test-123'])
            self.assertEqual(run.call_args.kwargs['env']['RUNPOD_API_KEY'],'rpa_private-existing-test')
            self.assertEqual(read.call_args.args[0].get_method(),'GET')
        for result in (SimpleNamespace(returncode=1,stderr='rpa_private-existing-test'),
                       subprocess.TimeoutExpired('private',20)):
            kwargs={'side_effect':result} if isinstance(result,Exception) else {'return_value':result}
            with patch('studio_runpod.subprocess.run',**kwargs):
                with self.assertRaises(ManagedGPUError) as error:client.request('pod-test-123','stop')
                self.assertNotIn('rpa_private-existing-test',str(error.exception))

    def test_pipeline_finalizer_stops_owned_rental_on_success_cancel_and_failure(self):
        from studio_service import StudioService,JobCancelled
        for error in (None,JobCancelled(),ValueError('source changed')):
            service=StudioService.__new__(StudioService);service.config=self.config
            service.store=Mock();service.store.root=Path(self.folder.name)
            service.store.load.return_value={'settings':{'image':{'provider':'comfyui','model':'flux','workflow':'flux-auto'},'cloudWindow':{'gpuBudgetUSD':1}}}
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
