"""Offline owner/broker/native contract tests; no provider credentials or calls."""
import copy
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parent
APP=ROOT
sys.path.insert(0,str(APP))
import studio_cache_owner as owner
import studio_runpod_cache as cache
import studio_gpu_broker as broker
import studio_runpod as native
import studio_worker_bootstrap as bootstrap


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value),encoding='utf-8')


def free_pair():
    for port in range(21000,23000,2):
        sockets=[]
        try:
            for value in (port,port+1):
                s=socket.socket();sockets.append(s);s.bind(('127.0.0.1',value))
            return port
        except OSError:pass
        finally:
            for s in sockets:s.close()
    raise RuntimeError('No free isolated test pair')


class Fixture:
    def __init__(self,root):
        self.root=Path(root);self.private=self.root/'private';self.state=self.private/'global-state'
        self.path=self.root/'store-a'/'.managed-rental.json';self.project='pr-0123456789abcdef'
        self.budget={'gpuBudgetUSD':.20,'gpuPreviouslySpentUSD':0}
        self.image={'provider':'comfyui','model':'flux2-klein-4b','workflow':'flux2-klein-4b-auto',
                    'width':1280,'height':720,'steps':4,'guidance':1,'referenceStrength':.65,'loras':[],'controlnets':[],'ipAdapter':{}}
        self.value={'id':self.project,'settings':{'image':self.image,'cloudWindow':self.budget},'chapters':[]}
        self.save_project()
        workflow=self.private/'workflows.json'
        dump(workflow,{'automaticWorkflows':{'flux2-klein-4b-auto':{'model':'flux2-klein-4b'}},'variants':[
            {'model':'flux2-klein-4b','prompt':{
                '1':{'class_type':'UNETLoader','inputs':{'unet_name':'flux-2-klein-4b-fp8.safetensors'}},
                '2':{'class_type':'CLIPLoader','inputs':{'clip_name':'qwen_3_4b.safetensors'}},
                '3':{'class_type':'VAELoader','inputs':{'vae_name':'flux2-vae.safetensors'}}}}]})
        manifest=self.private/'manifest.json';dump(manifest,{'fixture':'not a real weight file; no weight or image request'})
        profile=self.private/'profile.json';dump(profile,{'schemaVersion':1,'enabled':True,'volumeId':'fixture0123',
            'volumeName':'fixture-private-cache','dataCenter':'EU-RO-1','sizeGB':80,'type':'STANDARD',
            'mountPath':'/runpod-volume','retentionDeadlineEpoch':time.time()+48*3600,
            'cacheManifestPath':str(manifest),'cacheManifestSHA256':owner.sha(manifest),'stateRoot':str(self.state)})
        key=self.private/'dummy-key';key.write_text('NO PROVIDER KEY');Path(str(key)+'.pub').write_text('ssh-ed25519 AAAAFIXTURE')
        self.config={'runpodImageCacheEnabled':True,'runpodImageCacheProfile':str(profile),'comfyModelCopies':2,
            'comfyEndpoint':f'http://127.0.0.1:{free_pair()}','comfyWorkflow':str(workflow),
            'runpodActionKeyFile':str(key),'runpodSSHKeyFile':str(key),'gpuProviderOrder':['runpod']}
    def save_project(self):dump(self.path.parent/self.project/'project.json',self.value)
    def prepare(self):return cache.prepare_job(self.config,self.path,self.project,self.budget,lambda *_:None)


def fake_live(job,counter,unknown=False):
    """Faked provider boundary used only by offline tests/process workers."""
    gone={'yes':False}
    def api(path,body=None,method='GET'):
        if path.startswith('/network-volumes/'):
            p=job.verify_profile();return {'id':p['volumeId'],'name':p['volumeName'],'size':80,'dataCenter':'EU-RO-1','type':'STANDARD'}
        if path.startswith('/catalog/gpus/'):
            return {'id':owner.GPU,'secure':True,'price':{'secure':1.19},'dataCenters':[{'id':'EU-RO-1','availability':'HIGH'}]}
        if path=='/pods' and method=='POST':
            with Path(counter).open('a') as f:f.write('ONE_MOCK_POST\n')
            if unknown:raise TimeoutError('faked lost response')
            return {'id':'fixturepod123'}
        if path=='/pods/fixturepod123':
            if method=='DELETE':gone['yes']=True;return {}
            if gone['yes']:raise owner.APIError(404)
            state=job.state();return {'id':'fixturepod123','name':state['name'],'status':'RUNNING','gpu':{'id':owner.GPU,'count':1},
                'cloud':'SECURE','dataCenterId':'EU-RO-1','cost':1.19,'createdAt':__import__('datetime').datetime.fromtimestamp(
                    state['startedEpoch'],__import__('datetime').timezone.utc).isoformat(),'mounts':{'network':[{'volumeId':job.plan['volumeId'],'path':'/runpod-volume'}]}}
        raise AssertionError('No other provider route allowed')
    def arm(state):state['watchdogArmed']=True;job.save(state)
    job.api=api;job.arm=arm;job.scheduled=Mock();job.setup_handoff=lambda state:{'status':'MOCK_ACK_RUNNING'}


def competing_worker(spec):
    data=json.loads(Path(spec).read_text());cache.workstation_owner_root=lambda:Path(data['ownerRoot'])
    while not Path(data['barrier']).exists():time.sleep(.02)
    try:
        job=cache.prepare_job(data['config'],Path(data['nativePath']),data['project'],data['budget'],lambda *_:None)
        fake_live(job,data['counter']);job.launch(job.folder/'native-admission.json');status='ONE_ACK_OWNED'
    except BaseException as e:status='BLOCKED_'+type(e).__name__
    dump(Path(data['result']),{'status':status})


class CacheIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.f=Fixture(self.temp.name)
        self.root_patch=patch.object(cache,'workstation_owner_root',return_value=self.f.state.resolve());self.root_patch.start()
    def tearDown(self):self.root_patch.stop();self.temp.cleanup()
    def test_materialized_owner_root_is_canonical_before_native_pointer_write(self):
        from contextlib import contextmanager
        logical=self.f.state
        canonical=self.f.private/'materialized-owner'
        created={'yes':False};original_resolve=Path.resolve;original_lock=owner.ownership_lock
        @contextmanager
        def materialize(path):
            if path==logical:
                canonical.mkdir();created['yes']=True
                with original_lock(canonical):yield
            else:
                with original_lock(path):yield
        def resolving(path,*a,**kw):
            if path==logical and created['yes']:return original_resolve(canonical,*a,**kw)
            return original_resolve(path,*a,**kw)
        with patch.object(owner,'ownership_lock',materialize),patch.object(Path,'resolve',resolving):
            job=self.f.prepare()
            native=owner.read(self.f.path)
            self.assertEqual(native['cacheOwnerRoot'],str(job.root))
            self.assertEqual(job.root,canonical)
            self.assertTrue((job.folder/'plan.json').exists())
    def test_directory_identity_refuses_foreign_and_missing_roots(self):
        self.f.state.mkdir(parents=True)
        self.assertTrue(owner.same_directory(self.f.state,self.f.state/'.'))
        self.assertFalse(owner.same_directory(self.f.state,self.f.private))
        self.assertFalse(owner.same_directory(self.f.state,self.f.private/'absent'))
        self.assertFalse(owner.same_directory(None,self.f.state))

    def test_disabled_unsupported_render_and_external_startup_keep_native_controller(self):
        self.assertFalse(cache.supported({},{}));self.assertFalse(cache.supported(self.f.config,{'model':'qwen-edit'}))
        self.assertFalse(broker.workload_verified(self.f.config,'runpod','render'))
        with patch.object(broker,'ProductionGPU') as native:
            reservation=broker.GPUReservation(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock(),
                                             owned_startup={'existing':'approved'},image_selection={'model':'flux2-klein-4b'})
            reservation.make('runpod');native.assert_called_once()
    def test_no_provider_at_configuration_preflight_or_constructor(self):
        with patch.object(owner.Job,'api',side_effect=AssertionError('network forbidden')):
            self.assertTrue(broker.gpu_configured(self.f.config));self.assertIsNone(cache.pending_reason(self.f.config,self.f.path))
            lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock())
            self.assertFalse(lease.owned);self.assertIsNone(lease.cache_job)
    def test_max_saved_native_and_global_prior_never_resets(self):
        dump(self.f.path,{'status':'STOPPED','spentByProject':{self.f.project:.03}})
        dump(self.f.state/'spend-ledger.json',{'spentByProject':{self.f.project:.04}})
        job=self.f.prepare();self.assertEqual(job.plan['previousGPUUpperUSD'],.04)
        self.assertEqual(job.plan['gpuAndDiskCapUSD'],.20)
        self.assertLessEqual(job.plan['deadlineEpoch'],time.time()+(.20-.04-.04)/1.21*3600+1)
    def test_insufficient_and_changed_saved_cap_stop_before_create(self):
        dump(self.f.state/'spend-ledger.json',{'spentByProject':{self.f.project:.19}})
        with self.assertRaises(Exception):self.f.prepare()
        self.assertFalse((self.f.state/'active-owner.json').exists())
        self.f.budget={'gpuBudgetUSD':.3,'gpuPreviouslySpentUSD':0}
        with self.assertRaises(Exception):self.f.prepare()
    def test_private_profile_is_not_project_controlled_or_alternate_owner_root(self):
        p=owner.read(self.f.config['runpodImageCacheProfile']);p['stateRoot']=str(self.f.root/'new-budget-reset-root')
        dump(Path(self.f.config['runpodImageCacheProfile']),p)
        with self.assertRaises(Exception):self.f.prepare()
    def test_expiry_preflight_and_deadline_clamp(self):
        p=owner.read(self.f.config['runpodImageCacheProfile']);p['retentionDeadlineEpoch']=time.time()+10800+300
        dump(Path(self.f.config['runpodImageCacheProfile']),p);job=self.f.prepare()
        self.assertLessEqual(job.plan['deadlineEpoch'],p['retentionDeadlineEpoch']-10800)
        p['retentionDeadlineEpoch']=time.time()+100;dump(Path(self.f.config['runpodImageCacheProfile']),p)
        self.assertIsNotNone(cache.launch_reason(self.f.config,self.f.path,{'model':'flux2-klein-4b'}))
    def test_safe_no_post_preflight_failure_releases_reserved_owner(self):
        job=self.f.prepare();job.scheduled=Mock();closed=job.cleanup()
        self.assertTrue(closed['deletionVerified']);self.assertTrue(closed['settlementVerified'])
        job.scheduled.assert_not_called();again=self.f.prepare();self.assertNotEqual(job.plan['jobId'],again.plan['jobId'])
    def test_unknown_is_reserved_and_blocks_other_store_and_provider(self):
        job=self.f.prepare();counter=self.f.root/'counter';fake_live(job,counter,unknown=True)
        with self.assertRaises(TimeoutError):job.launch(job.folder/'native-admission.json')
        self.assertEqual(owner.read(self.f.state/'spend-ledger.json')['spentByProject'][self.f.project],.20)
        self.assertIsNotNone(cache.pending_reason(self.f.config,self.f.path))
        with self.assertRaises(Exception):self.f.prepare()
        self.assertEqual(counter.read_text().count('ONE_MOCK_POST'),1)
        with patch.object(broker.GPUReservation,'make',side_effect=AssertionError('alternate provider must not be touched')):
            with self.assertRaises(Exception):broker.GPUReservation(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock()).ensure()
    def test_ack_recovers_after_allocation_state_write_failure_and_deletes_exactly(self):
        job=self.f.prepare();counter=self.f.root/'counter';fake_live(job,counter)
        real_save=job.save
        def save(value,exclusive=False):
            if value.get('status')=='CREATE_ACKNOWLEDGED':raise OSError('faked following state write failure')
            return real_save(value,exclusive)
        job.save=save
        with self.assertRaises(OSError):job.launch(job.folder/'native-admission.json')
        state=job.state();self.assertEqual(state['podId'],'fixturepod123');self.assertTrue(state['get404Verified'])
        self.assertTrue(state['settlementVerified']);self.assertEqual(counter.read_text().count('ONE_MOCK_POST'),1)
    def test_deleted_but_unsettled_owner_retries_settlement_before_task_retirement(self):
        job=self.f.prepare();job.cleanup();state=job.state();state['settlementVerified']=False;job.save(state);job.scheduled=Mock()
        with patch.object(job,'settle',wraps=job.settle) as settle:closed=job.expire()
        settle.assert_called_once();self.assertTrue(closed['settlementVerified'])
    def test_cleanup_ignores_disabled_expired_profile_but_rejects_foreign_store(self):
        job=self.f.prepare();p=owner.read(self.f.config['runpodImageCacheProfile']);p['retentionDeadlineEpoch']=0
        dump(Path(self.f.config['runpodImageCacheProfile']),p);self.f.config['runpodImageCacheEnabled']=False
        other=self.f.root/'store-b'/'.managed-rental.json'
        with self.assertRaises(Exception):cache.reconcile(self.f.config,other)
        result=cache.reconcile(self.f.config,self.f.path);self.assertEqual(result['status'],'STOPPED')
        self.assertNotIn('cacheOwnerRoot',result);self.assertNotIn('cacheJobId',result)
    def test_closed_cache_history_cannot_mark_newer_prepared_running_lease_stopped(self):
        job=self.f.prepare();job.cleanup()
        newer={'status':'RUNNING','project':self.f.project,'podId':'new-prepared-pod',
               'spentByProject':{self.f.project:.09},'started':time.time(),'hourlyUSD':1.19}
        dump(self.f.path,newer);before=self.f.path.read_bytes()
        self.assertIsNone(cache.reconcile(self.f.config,self.f.path));self.assertEqual(self.f.path.read_bytes(),before)
        with patch('studio_runpod.reconcile',return_value={'status':'STOPPED','project':self.f.project}) as generic:
            broker.reconcile(self.f.config,self.f.path)
        generic.assert_called_once()
    def test_reconcile_merges_each_project_carry_by_maximum(self):
        job=self.f.prepare();job.cleanup()
        native_state=owner.read(self.f.path);native_state['spentByProject']={self.f.project:.09,'pr-ffffffffffffffff':.08};dump(self.f.path,native_state)
        dump(self.f.state/'spend-ledger.json',{'spentByProject':{self.f.project:.03,'pr-ffffffffffffffff':.10}})
        result=cache.reconcile(self.f.config,self.f.path)
        self.assertEqual(result['spentByProject'][self.f.project],.09)
        self.assertEqual(result['spentByProject']['pr-ffffffffffffffff'],.10)
    def test_policy_ignores_output_timing_but_preserves_actual_quality_and_cap(self):
        old=owner.policy_digest(self.f.value);p=copy.deepcopy(self.f.value);p['production']={'stage':'live status'};p['revision']=21
        self.assertEqual(owner.policy_digest(p),old)
        p['settings']['image']['steps']=1;self.assertNotEqual(owner.policy_digest(p),old)
    def test_later_queued_custom_model_is_denied_before_reusing_owned_lease(self):
        lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock());lease.owned=True
        self.f.value['chapters']=[{'scenes':[{'shots':[{'imageModel':'qwen-edit'}]}]}];self.f.save_project()
        with self.assertRaises(ValueError):lease.ensure()
    def test_private_owner_fields_are_not_reported_to_browser_project(self):
        report=Mock();lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock(),report)
        lease.save(status='STOPPED',cacheOwnerRoot='PRIVATE',cacheJobId='PRIVATE')
        self.assertNotIn('cacheOwnerRoot',report.call_args.args[0]);self.assertNotIn('cacheJobId',report.call_args.args[0])
    def test_stop_and_tunnel_and_report_faults_do_not_bypass_owned_delete(self):
        job=self.f.prepare()
        lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock())
        cleanup=Mock(wraps=job.cleanup);job.cleanup=cleanup;lease.cache_job=job
        lease.tunnel=SimpleNamespace(poll=Mock(return_value=None),terminate=Mock(side_effect=OSError()),kill=Mock(side_effect=OSError()))
        with patch.object(native.ProductionGPU,'stop',side_effect=OSError('native proof write failed')):
            with self.assertRaises(Exception):lease.stop()
        cleanup.assert_called_once();self.assertTrue(lease.cache_closed)
    def test_stale_old_stop_and_every_late_save_cannot_overwrite_new_same_path_owner(self):
        old=self.f.prepare();lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock())
        lease.cache_job=old;real_cleanup=old.cleanup;new=[]
        def interleave():
            closed=real_cleanup();new.append(self.f.prepare())
            current=owner.read(self.f.path);current['status']='STARTING';dump(self.f.path,current)
            return closed
        old.cleanup=interleave
        with patch.object(native.ProductionGPU,'stop',return_value=None):lease.stop()
        current=owner.read(self.f.path)
        self.assertEqual(current['cacheJobId'],new[0].plan['jobId']);self.assertEqual(current['status'],'STARTING')
        before=self.f.path.read_bytes();lease.save(status='SHUTDOWN_UNCONFIRMED');lease.save(status='STOPPED')
        self.assertEqual(self.f.path.read_bytes(),before);self.assertTrue(lease.cache_stale_save_skipped)
    def test_reconcile_interleave_cannot_overwrite_new_same_path_owner(self):
        old=self.f.prepare();real_cleanup=owner.Job.cleanup;new=[]
        def interleave(job):
            closed=real_cleanup(job)
            if job.plan['jobId']==old.plan['jobId'] and not new:
                new.append(self.f.prepare());current=owner.read(self.f.path);current['status']='STARTING';dump(self.f.path,current)
            return closed
        with patch.object(owner.Job,'cleanup',interleave):
            with self.assertRaises(cache.ManagedGPUError):cache.reconcile(self.f.config,self.f.path)
        current=owner.read(self.f.path);self.assertEqual(current['cacheJobId'],new[0].plan['jobId'])
        self.assertEqual(current['status'],'STARTING')
    def test_native_admission_uses_internal_original_handoff_not_external_receipt(self):
        job=self.f.prepare();runtime=job.folder/'runtime-config.private.json';dump(runtime,self.f.config|{
            'runpodManagedPodId':'fixturepod123','runpodPreparedPodIds':['fixturepod123'],'runpodRequireMountedCache':True})
        handoff={'runtimeConfigPath':str(runtime),'podId':'fixturepod123',
            'ownedWorkerReceipt':{'project':self.f.project,'podId':'fixturepod123','name':'studio-two-copy-5090-0123456789ab','started':time.time(),'capUSD':.2},
            'requiredCloudWindowPatch':{'gpuBudgetUSD':.2,'gpuPreviouslySpentUSD':.001}}
        job.launch=Mock(return_value=handoff)
        lease=cache.CachedProductionGPU(self.f.config,self.f.path,self.f.project,self.f.budget,Mock(),Mock())
        def ensure(controller):
            self.assertEqual(controller.owned_startup,handoff['ownedWorkerReceipt'])
            self.assertTrue(controller.config['runpodRequireMountedCache']);self.assertEqual(controller.budget['gpuPreviouslySpentUSD'],.001)
            controller.owned=True
        with patch.object(cache,'prepare_job',return_value=job),patch.object(native.ProductionGPU,'ensure',ensure):lease.ensure()
        self.assertTrue(lease.owned);job.launch.assert_called_once()
    def test_native_bootstrap_refuses_missing_cache_proof_before_worker_confirmation(self):
        config=self.f.config|{'runpodManagedPodId':'fixturepod123','runpodPreparedPodIds':['fixturepod123'],
            'runpodBootstrapKleinPodIds':['fixturepod123'],'runpodRequireMountedCache':True}
        provider=Mock();lease=native.ProductionGPU(config,self.f.path,self.f.project,self.f.budget,provider,Mock())
        lease.started=time.time();lease.deadline=time.time()+200;lease.owned=True
        process=Mock(returncode=0);process.communicate.return_value=('MODELS_VERIFIED\nWORKERS_VERIFIED {}\n','');process.poll.return_value=0
        pod={'ssh':{'direct':{'host':'127.0.0.1','port':2222,'username':'root'}}}
        with patch.object(native.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(native.subprocess,'Popen',return_value=process):
            with self.assertRaises(native.ManagedGPUError):lease.bootstrap(pod)
        provider.confirm_workers.assert_not_called();self.assertFalse(getattr(lease,'bootstrapped',False))
        submitted=process.communicate.call_args.kwargs['input'];self.assertIn('Required mounted model cache',submitted)
    def test_native_bootstrap_persists_exact_cache_proof_before_ready_workers(self):
        config=self.f.config|{'runpodManagedPodId':'fixturepod123','runpodPreparedPodIds':['fixturepod123'],
            'runpodBootstrapKleinPodIds':['fixturepod123'],'runpodRequireMountedCache':True}
        provider=Mock();lease=native.ProductionGPU(config,self.f.path,self.f.project,self.f.budget,provider,Mock())
        lease.started=time.time();lease.deadline=time.time()+200;lease.owned=True
        proof={'version':1,'cacheRequired':True,'modelRoot':'/runpod-volume/models',
               'destinationRoot':'/workspace/runpod-slim/ComfyUI/models','modelCopies':2,'noModelDownloads':True,
               'fullSHA256Verified':True,'linksVerified':True,
               'weights':[{'path':x['destination'],'bytes':x['bytes'],'sha256':x['sha256']} for x in bootstrap.MODELS]}
        process=Mock(returncode=0);process.poll.return_value=0
        process.communicate.return_value=('CACHE_MODELS_VERIFIED '+json.dumps(proof)+'\nMODELS_VERIFIED\nWORKERS_VERIFIED {"workers":[{"index":0,"pid":111,"port":8188},{"index":1,"pid":222,"port":8189}]}\n','')
        pod={'ssh':{'direct':{'host':'127.0.0.1','port':2222,'username':'root'}}}
        with patch.object(native.subprocess,'run',return_value=SimpleNamespace(returncode=0)),patch.object(native.subprocess,'Popen',return_value=process):lease.bootstrap(pod)
        self.assertEqual(owner.read(self.f.path)['cacheModelsVerified'],proof);self.assertTrue(lease.bootstrapped)
        provider.confirm_workers.assert_called_once()
    def test_public_descriptor_and_config_exclusions_never_expose_private_ids(self):
        descriptor=cache.public_descriptor(self.f.config);wire=json.dumps(descriptor)
        self.assertTrue(descriptor['profileReady']);self.assertFalse(descriptor['normalBrokerLiveValidated'])
        for hidden in ('fixture0123',str(self.f.state),self.f.config['runpodImageCacheProfile']):self.assertNotIn(hidden,wire)
        self.assertIn("'runpodImageCacheProfile'",(ROOT/'server.py').read_text())
    def test_normal_broker_claim_requires_current_profile_source_and_exact_live_scope(self):
        proof={'version':1,'status':'PASS_NORMAL_NATIVE_CACHE_GPU_RESERVATION','policyId':'jit-image-klein-cache-v1',
            'profileSHA256':owner.sha(self.f.config['runpodImageCacheProfile']),'moduleSHA256':cache.module_hashes(),
            'nativeWorkersVerified':2,'mountedRequiredModelsVerified':3,'oneImage':True,'graphAndSettingsExact':True,
            'ownedDeletion404Verified':True,'noExternalOwnedWorkerReceipt':True,'normalBrokerEntryPoint':True,
            'observedAtEpoch':time.time(),'nativeReadySeconds':2,'throughPNGSaveSeconds':3}
        path=self.f.private/'synthetic-offline-validation.json';dump(path,proof)
        config=self.f.config|{'runpodImageCacheValidationReceipt':str(path)}
        self.assertTrue(cache.public_descriptor(config)['normalBrokerLiveValidated'])
        for key in ('graphAndSettingsExact','noExternalOwnedWorkerReceipt','normalBrokerEntryPoint','ownedDeletion404Verified'):
            changed=copy.deepcopy(proof);changed[key]=False;dump(path,changed)
            self.assertFalse(cache.public_descriptor(config)['normalBrokerLiveValidated'])
        changed=copy.deepcopy(proof);changed['moduleSHA256']['studio_cache_owner.py']='0'*64;dump(path,changed)
        self.assertFalse(cache.public_descriptor(config)['normalBrokerLiveValidated'])
    def test_real_two_process_admission_across_stores_permits_one_mock_post(self):
        barrier=self.f.root/'go';counter=self.f.root/'posts';processes=[]
        try:
            for index in (0,1):
                native=self.f.root/f'store-{index}'/'.managed-rental.json'
                dump(native.parent/self.f.project/'project.json',self.f.value)
                spec=self.f.root/f'worker-{index}.json';result=self.f.root/f'result-{index}.json'
                dump(spec,{'ownerRoot':str(self.f.state.resolve()),'barrier':str(barrier),'counter':str(counter),
                    'nativePath':str(native),'project':self.f.project,'budget':self.f.budget,'config':self.f.config,'result':str(result)})
                code='import studio_native_cache_test as t;t.competing_worker('+repr(str(spec))+')'
                processes.append(subprocess.Popen([sys.executable,'-B','-c',code],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE))
            barrier.write_text('start')
            for process in processes:
                out,err=process.communicate(timeout=25);self.assertEqual(process.returncode,0,err.decode()[-1000:])
            statuses=[owner.read(self.f.root/f'result-{i}.json')['status'] for i in (0,1)]
            self.assertEqual(statuses.count('ONE_ACK_OWNED'),1,statuses)
            self.assertEqual(counter.read_text().count('ONE_MOCK_POST'),1)
        finally:
            for process in processes:
                if process.poll() is None:process.kill();process.communicate()


if __name__=='__main__':unittest.main()
