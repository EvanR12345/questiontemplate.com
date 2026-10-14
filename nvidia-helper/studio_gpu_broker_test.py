import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from studio_gpu_broker import GPUReservation,provider_order,gpu_configured,reconcile
from studio_gpu_providers import GPUCapacityUnavailable,GPURequestUncertain
from studio_cloud_comfy import install_script,COMFY_COMMIT

class BrokerTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'lease.json';self.events=[]
    def tearDown(self):self.temp.cleanup()
    def fake(self,name,error=None):
        lease=SimpleNamespace(state={'status':'STOPPED'},owned=False,deadline=999,runner=SimpleNamespace(close=lambda:self.events.append(name+' close')))
        def ensure():
            self.events.append(name+' ensure')
            if error:raise error
            lease.owned=True;lease.state['status']='RUNNING'
        def stop(_):self.events.append(name+' stop');lease.owned=False;lease.state['status']='STOPPED'
        lease.ensure=ensure;lease.stop=stop;lease.save=lambda **kw:lease.state.update(kw)
        lease.check=lambda:None
        return lease
    def reserve(self,factories,verified=None):
        return GPUReservation({'gpuProviderOrder':list(factories),'gpuImageVerifiedProviders':verified or list(factories)},self.path,'p',{'gpuBudgetUSD':1},None,lambda *_:None,factories=factories)
    def test_confirmed_capacity_falls_back_without_two_rentals(self):
        first=self.fake('vast',GPUCapacityUnavailable('gone'));second=self.fake('lium')
        reserve=self.reserve({'vast':lambda:first,'lium':lambda:second})
        reserve.ensure();reserve.stop()
        self.assertEqual(self.events,['vast ensure','vast close','lium ensure','lium stop','lium close'])
    def test_unknown_create_never_allocates_second_vendor(self):
        first=self.fake('vast',GPURequestUncertain('lost'));second=self.fake('lium')
        reserve=self.reserve({'vast':lambda:first,'lium':lambda:second})
        with self.assertRaises(GPURequestUncertain):reserve.ensure()
        self.assertNotIn('lium ensure',self.events)
    def test_untested_vendor_never_rents(self):
        reserve=self.reserve({'vast':lambda:self.fake('vast'),'lium':lambda:self.fake('lium')},['lium'])
        reserve.ensure();self.assertNotIn('vast ensure',self.events);reserve.stop()
    def test_running_lease_is_reused(self):
        lease=self.fake('lium');reserve=self.reserve({'lium':lambda:lease})
        reserve.ensure();reserve.ensure();self.assertEqual(self.events,['lium ensure','lium ensure']);reserve.stop()
    def test_model_provider_order_accepts_all_three_without_substitution(self):
        self.assertEqual(provider_order({}),['runpod','vast','lium'])
        for order in (['lium','lium'],['a6000'],[],['lium',False]):
            with self.assertRaises(ValueError):provider_order({'gpuProviderOrder':order})
    def test_remote_setup_is_pinned_and_constrains_existing_torch(self):
        script=install_script();compile(script,'cloud-setup','exec')
        self.assertIn(COMFY_COMMIT,script);self.assertIn("'-c',str(constraints)",script)
        self.assertNotIn('pip install torch',script)
    def test_marketplace_recovery_stops_only_saved_owner_and_settles_once(self):
        owner='studio-cloud-'+'a'*32;calls=[]
        self.path.write_text(json.dumps({'status':'STARTING','provider':'lium','name':owner,'podId':'known-pod','started':100,
            'hourlyUSD':.72,'project':'p'}))
        client=SimpleNamespace(stop=lambda *args:calls.append(args))
        state=reconcile({},self.path,{'lium':client},clock=lambda:150)
        self.assertEqual(calls,[('known-pod',owner)]);self.assertEqual(state['status'],'STOPPED')
        reconcile({},self.path,{'lium':client});self.assertEqual(len(calls),1)
    def test_recovery_without_confirmed_resource_never_guesses_an_owner(self):
        self.path.write_text(json.dumps({'status':'SUBMISSION_UNKNOWN','provider':'vast','name':'studio-cloud-'+'a'*32,'podId':None}))
        with self.assertRaises(GPURequestUncertain):reconcile({},self.path,{'vast':SimpleNamespace(resources=lambda _:[])})

if __name__=='__main__':unittest.main()
