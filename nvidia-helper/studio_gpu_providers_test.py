import datetime,io,json,tempfile,time,unittest,urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from studio_gpu_providers import *
from studio_cloud_gpu import CloudGPULease

def vast_row(**changes):
    return dict(id=12345,gpu_name='RTX 5090',num_gpus=1,verification='verified',rentable=True,rented=False,
        reliability=.999,cuda_max_good=12.8,disk_space=100,direct_port_count=1,dph_total=.7,
        inet_up=500,inet_down=500,inet_up_cost=.01,inet_down_cost=.02,**changes)


class ProviderTests(unittest.TestCase):
    def test_cuda_version_is_not_a_decimal(self):
        self.assertGreater(cuda_version('12.10'),cuda_version('12.8'))
        with self.assertRaises(GPUProviderError):cuda_version(None)

    def test_provider_naive_utc_deadline_does_not_use_laptop_timezone(self):
        self.assertEqual(utc_stamp('2026-10-13T23:27:12'),utc_stamp('2026-10-13T23:27:12Z'))
        self.assertEqual(utc_stamp('2026-10-13T19:27:12-04:00'),utc_stamp('2026-10-13T23:27:12Z'))

    def test_vast_requires_one_verified_available_gpu_with_storage_and_ssh(self):
        row=vast_row()
        self.assertEqual(vast_offer(row,25,1)['hourlyUSD'],.7)
        for field,value in [('verification','unverified'),('num_gpus',2),('rented',True),
                            ('direct_port_count',0),('cuda_max_good',12.6),('disk_space',10)]:
            self.assertIsNone(vast_offer(row|{field:value},25,1))
        with self.assertRaises(GPUProviderError):vast_offer(row|{'dph_total':float('nan')},25,1)

    def test_lium_uses_available_count_minimum_order_and_partial_share(self):
        row={'id':str(uuid.uuid4()),'gpu_model':'RTX 5090','gpu_count':2,'available_gpu_count':1,
             'min_rentable_gpu_count':1,'tier':'secure','reliability_score':99,
             'cuda_driver':'13.2','disk_free_gb':80,'price_per_gpu_hour':.7}
        self.assertIsNotNone(lium_offer(row,25,1))
        self.assertIsNone(lium_offer(row,50,1))
        self.assertIsNone(lium_offer(row|{'min_rentable_gpu_count':2},25,1))
        self.assertIsNone(lium_offer(row|{'tier':'spot'},25,1))

    def test_direct_connection_does_not_execute_marketplace_shell_options(self):
        self.assertEqual(lium_ssh('ssh root@192.0.2.1 -p 2022')['port'],2022)
        for value in ['ssh root@evil.test -p 22','ssh -o ProxyCommand=evil root@192.0.2.1 -p 22',
                      'ssh root@192.0.2.1 -p 22; evil']:
            with self.assertRaises(GPUProviderError):lium_ssh(value)

    def test_keys_stay_in_headers_of_the_correct_fixed_provider(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.private.json';path.write_text(json.dumps({'vastApiKey':'V'*40,'liumApiKey':'L'*40}))
            calls=[]
            def transport(request,**_):
                calls.append(request);return io.BytesIO(b'{}')
            VastProvider(path,transport).call('/v1/instances')
            LiumProvider(path,transport).call('/pods')
            self.assertEqual(calls[0].get_header('Authorization'),'Bearer '+'V'*40)
            self.assertEqual(calls[1].get_header('X-api-key'),'L'*40)
            for request in calls:
                self.assertNotIn('V'*40,request.full_url);self.assertNotIn('L'*40,request.full_url)
                self.assertIsNone(request.data)

    def test_lost_create_reply_is_uncertain_and_never_retried(self):
        calls=[]
        def fail(request,**_):calls.append(request);raise TimeoutError('SECRET')
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'.private.json';path.write_text(json.dumps({'vastApiKey':'V'*40}))
            client=VastProvider(path,fail)
            with self.assertRaises(GPURequestUncertain) as error:
                client.create({'id':12345,'diskGB':25},'studio-cloud-'+'a'*32,'official-image')
            self.assertNotIn('SECRET',str(error.exception));self.assertEqual(len(calls),1)

    def test_stale_feed_is_not_treated_as_available(self):
        old=datetime.datetime.fromtimestamp(time.time()-1000,datetime.timezone.utc).isoformat()
        client=LiumProvider(transport=lambda *_args,**_kw:io.BytesIO(json.dumps({'generated_at':old,'nodes':[]}).encode()))
        with self.assertRaisesRegex(GPUProviderError,'stale'):client.offers()

    def test_cannot_control_an_unrelated_instance(self):
        client=VastProvider();client.resources=lambda _:[{'id':12345,'label':'someone-else'}]
        # Exact-name filtering lives in resources; an ID is still required.
        with self.assertRaises(GPURequestUncertain):client.owned('23456','studio-cloud-'+'a'*32)

    def test_matching_id_with_wrong_owner_cannot_be_controlled(self):
        client=VastProvider();client.resources=lambda _:[{'id':12345,'label':'someone-else'}]
        with self.assertRaises(GPURequestUncertain):client.owned('12345','studio-cloud-'+'a'*32)

    def test_lium_release_recovers_after_lost_reply_without_repeating_delete(self):
        client=LiumProvider();identity=str(uuid.uuid4());name='studio-cloud-'+'a'*32;calls=[]
        client.resources=lambda _ : []
        client.call=lambda *args,**kwargs:calls.append(args)
        self.assertEqual(client.stop(identity,name)['status'],'RELEASED')
        self.assertEqual(calls,[])


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'lease.json'
        self.clock=[1000.];self.events=[]
        self.runner=SimpleNamespace(prepare=lambda _:self.events.append('prepare'))
    def tearDown(self):self.temp.cleanup()
    def client(self,failure=None):
        client=SimpleNamespace(key=lambda:'private',offers=lambda *_:[{'id':'12345','hourlyUSD':.7,'diskGB':25}],
            owned=lambda *_:{'actual_status':'running','dph_total':.7},connection=lambda _:{'host':'192.0.2.1','port':22,'username':'root'},
            stop=lambda *_:self.events.append('stop'),resources=lambda _:[])
        def create(*_):
            self.events.append('create')
            if failure:raise failure
            return '12345'
        client.create=create
        return client
    def lease(self,clients,cap=.5,order=None):
        return CloudGPULease({'gpuProviderOrder':order or list(clients)},self.path,'project',{'gpuBudgetUSD':cap},
            self.runner,lambda *_:None,connector=lambda _:self.events.append('connect'),
            clock=lambda:self.clock[0],clients=clients)
    def test_owned_startup_stops_and_counts_setup_in_same_cap(self):
        lease=self.lease({'vast':self.client()})
        try:
            lease.ensure();self.clock[0]+=36;lease.stop()
            self.assertEqual(self.events,['create','connect','prepare','stop'])
            self.assertAlmostEqual(lease.state['estimatedUSD'],.007)
            self.assertEqual(lease.state['status'],'STOPPED')
        finally:lease.stop()
    def test_missing_budget_does_not_allocate(self):
        lease=self.lease({'vast':self.client()},cap=.01)
        with self.assertRaisesRegex(GPUProviderError,'Remaining cap'):lease.ensure()
        self.assertEqual(self.events,[])
    def test_uncertain_creation_blocks_fallback_and_preserves_ownership(self):
        lease=self.lease({'vast':self.client(GPURequestUncertain('lost')),
                         'lium':self.client()},order=['vast','lium'])
        with self.assertRaises(GPURequestUncertain):lease.ensure()
        lease.finished.set() # Prevent test watchdog; receipt remains unresolved.
        self.assertEqual(self.events,['create'])
        self.assertEqual(lease.state['status'],'SHUTDOWN_UNCONFIRMED')
        with self.assertRaises(GPURequestUncertain):self.lease({'vast':self.client()})
    def test_confirmed_capacity_rejection_allows_another_offer(self):
        first=self.client(GPUCapacityUnavailable('gone'));second=self.client()
        # Both fake clients use the Vast contract; provider order remains explicit.
        first.offers=lambda *_:[{'id':'1','hourlyUSD':.7,'diskGB':25}]
        calls=[first,second]
        first.create=lambda *_:(_ for _ in ()).throw(GPUCapacityUnavailable('gone'))
        lease=self.lease({'vast':first})
        first.offers=lambda *_:[{'id':'1','hourlyUSD':.7,'diskGB':25},{'id':'2','hourlyUSD':.7,'diskGB':25}]
        attempt=[0]
        def create(*_):
            attempt[0]+=1
            if attempt[0]==1:raise GPUCapacityUnavailable('gone')
            self.events.append('create');return '12345'
        first.create=create
        try:
            lease.ensure();self.assertEqual(attempt[0],2);lease.stop()
        finally:lease.stop()

if __name__=='__main__':unittest.main()
