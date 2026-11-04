"""Opt-in native IMAGE lease with a private retained Runpod model cache.

No provider action occurs at import, configuration, preflight or construction.
Only ensure(), after the normal caller's plan/source/budget gate, may admit one
job-owned create. Unknown outcomes fence the workstation's other JIT jobs.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import time
import uuid

import studio_cache_owner as owner
from studio_runpod import ProductionGPU,RunpodClient,ManagedGPUError

VALIDATION_MODULES=('studio_cache_owner.py','studio_runpod_cache.py','studio_gpu_broker.py',
    'studio_runpod.py','studio_worker_bootstrap.py','studio_service.py','studio_planner_profile.py',
    'server.py','studio_image_workers.py','image_provider.py','studio_execution.py')


def module_hashes():
    result={}
    for name in VALIDATION_MODULES:
        path=Path(__file__).parent/name
        if not path.is_file():
            spec=importlib.util.find_spec(name[:-3]);path=Path(spec.origin) if spec and spec.origin else path
        result[name]=owner.sha(path)
    return result


def configured(config):
    return config.get('runpodImageCacheEnabled') is True and isinstance(config.get('runpodImageCacheProfile'),str)


def supported(config,selection):
    if not configured(config):return False
    selected=selection or {}
    return selected.get('model','flux2-klein-4b')=='flux2-klein-4b' and selected.get('workflow','flux2-klein-4b-auto')=='flux2-klein-4b-auto'


def workstation_owner_root():
    """One account/workstation root independent of project, config or store."""
    return (Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'QuestionTemplateStudio'/'native-image-cache').resolve()


def private_profile(config,native_path):
    path=Path(config['runpodImageCacheProfile'])
    if not path.is_absolute() or path.resolve().is_relative_to(Path(native_path).resolve().parent):
        raise ManagedGPUError('Cache profile must be private workstation configuration outside browser project storage.')
    value=owner.verify_profile_path(path)
    root=Path(value['stateRoot'])
    if (not root.is_absolute() or root.resolve()!=workstation_owner_root()
        or root.resolve().is_relative_to(Path(native_path).resolve().parent)):
        raise ManagedGPUError('A fixed private workstation owner root is required across project stores.')
    if config.get('comfyModelCopies')!=2 or config.get('studioDenseLeaseContinuity',True) is not True:
        raise ManagedGPUError('Cached native IMAGE requires two existing model copies and a retained image/QC lease.')
    return path.resolve(),value,root.resolve()


def pending_reason(config,native_path):
    """Read-only preflight: block old UNKNOWN/cleanup without buying hardware."""
    try:
        root=ownership_root(config,native_path)
        if root is None:return None
        registry=root/'active-owner.json'
        if registry.exists():
            active=owner.read(registry);label=active['jobId']
            if not isinstance(label,str) or '/' in label or '\\' in label:return 'Invalid cached GPU owner requires reconciliation.'
            path=root/'jobs'/label/'allocation.json'
            state=owner.read(path) if path.exists() else {}
            if not all(state.get(k) is True for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified')):
                return 'An earlier cached GPU owner or UNKNOWN must be reconciled before new work.'
    except (OSError,ValueError,KeyError,TypeError):
        return 'Private cached GPU ownership state needs reconciliation.'
    return None


def ownership_root(config,native_path):
    """Cleanup is independent of enabled/expiry/new-job eligibility."""
    native=owner.read(native_path) if Path(native_path).exists() else {}
    value=native.get('cacheOwnerRoot')
    if value is None:
        profile=config.get('runpodImageCacheProfile')
        if not profile:
            root=workstation_owner_root()
            return root if (root/'active-owner.json').exists() else None
        value=owner.read(profile)['stateRoot']
    root=Path(value)
    if (not root.is_absolute() or root.resolve()!=workstation_owner_root()
        or root.resolve().is_relative_to(Path(native_path).resolve().parent)):
        raise ValueError('Invalid private owner root')
    return root.resolve()


def launch_reason(config,native_path,selection):
    if not supported(config,selection):return None
    try:private_profile(config,native_path)
    except (OSError,ValueError,KeyError,TypeError):return 'Private cached IMAGE profile is expired, changed or unsupported.'
    return pending_reason(config,native_path)


def public_descriptor(config):
    """Sanitized capability only; no credential/cache ID/private local path."""
    enabled=configured(config);ready=False;expires=None
    if enabled:
        try:
            p=owner.verify_profile_path(config['runpodImageCacheProfile'])
            ready=Path(p['stateRoot']).resolve()==workstation_owner_root() and config.get('comfyModelCopies')==2 and config.get('studioDenseLeaseContinuity',True) is True
            expires=p['retentionDeadlineEpoch']-10800
        except (OSError,ValueError,KeyError,TypeError):pass
    normal=False;observation=None
    if ready and config.get('runpodImageCacheValidationReceipt'):
        try:
            proof=owner.read(config['runpodImageCacheValidationReceipt'])
            normal=(proof.get('version')==1 and proof.get('status')=='PASS_NORMAL_NATIVE_CACHE_GPU_RESERVATION'
                and proof.get('policyId')=='jit-image-klein-cache-v1'
                and proof.get('profileSHA256')==owner.sha(config['runpodImageCacheProfile'])
                and proof.get('moduleSHA256')==module_hashes()
                and proof.get('nativeWorkersVerified')==2 and proof.get('mountedRequiredModelsVerified')==3
                and all(proof.get(k) is True for k in ('oneImage','graphAndSettingsExact','ownedDeletion404Verified',
                                                      'noExternalOwnedWorkerReceipt','normalBrokerEntryPoint'))
                and all(owner.finite(proof.get(k)) and proof[k]>0 for k in ('observedAtEpoch','nativeReadySeconds','throughPNGSaveSeconds'))
                and proof['observedAtEpoch']<=time.time()+5)
            if normal:observation={'observedAtEpoch':proof['observedAtEpoch'],'nativeReadySeconds':proof['nativeReadySeconds'],
                'throughPNGSaveSeconds':proof['throughPNGSaveSeconds'],
                'scope':'One fresh normal GPUReservation image job with this exact source/profile; not a future capacity guarantee.'}
        except (OSError,ValueError,KeyError,TypeError):pass
    return {'schemaVersion':1,'policyId':'jit-image-klein-cache-v1','enabled':enabled,'profileReady':ready,
            'imageOnly':True,'provider':'runpod','requiredGPU':owner.GPU,'model':'flux2-klein-4b',
            'workflow':'flux2-klein-4b-auto','modelCopies':2,'requestSlots':4,
            'cachePolicy':'REQUIRED_MOUNTED_SHA_NO_DOWNLOAD','requiresLiveValidation':True,
            'automaticProvisioningConfigured':enabled and ready,'normalBrokerLiveValidated':normal,'normalBrokerObservation':observation,
            'observedAtEpoch':time.time(),'expiresAtEpoch':expires}


def _project(path,project):
    source=Path(path).parent/project/'project.json'
    value=owner.read(source)
    if value.get('id')!=project:raise ManagedGPUError('Cached image source project identity differs.')
    return source,value


def prepare_job(config,path,project,budget,gate):
    """Serialize owner admission and carry MAX historical cumulative spend."""
    gate('Checking saved image plan and cumulative cached GPU allowance')
    profile_path,profile,root=private_profile(config,path)
    source,current=_project(path,project)
    workflow=owner.supported_project_config(current,config)
    owner.local_ports_free(config)
    cap=budget.get('gpuBudgetUSD');requested=budget.get('gpuPreviouslySpentUSD',0)
    saved=current.get('settings',{}).get('cloudWindow',{})
    if cap!=saved.get('gpuBudgetUSD') or requested!=saved.get('gpuPreviouslySpentUSD',0):
        raise ManagedGPUError('Native request must use the project saved GPU cap and prior spend.')
    if not all(owner.finite(v) and v>=0 for v in (cap,requested)) or cap<=0:
        raise ManagedGPUError('Finite saved GPU cap required before cached worker creation.')
    for field in ('runpodActionKeyFile','runpodSSHKeyFile'):
        if not isinstance(config.get(field),str) or not Path(config[field]).is_file():
            raise ManagedGPUError('Existing private Runpod control and SSH paths required.')
    with owner.ownership_lock(root):
        # Directory creation can materialize a Windows MSIX filesystem alias.
        # Resolve again under the same lock before persisting any owner pointer.
        root=root.resolve()
        registry=root/'active-owner.json'
        if registry.exists():
            active=owner.read(registry);old=owner.Job(root,active['jobId']);old_state=old.state() if old.state_path.exists() else {}
            if not all(old_state.get(k) is True for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified')):
                raise ManagedGPUError('Earlier cached allocation/UNKNOWN or unsettled spend blocks new creation.')
        native=owner.read(path) if Path(path).exists() else {}
        ledger=owner.read(root/'spend-ledger.json') if (root/'spend-ledger.json').exists() else {}
        values=[requested,native.get('spentByProject',{}).get(project,0),ledger.get('spentByProject',{}).get(project,0)]
        if not all(owner.finite(v) and v>=0 for v in values):raise ManagedGPUError('Persisted cumulative GPU carry is invalid.')
        previous=max(values);now=time.time();reserve=.04;rate=1.21
        seconds=min(7200,(cap-previous-reserve)/rate*3600,profile['retentionDeadlineEpoch']-10800-now)
        explicit=budget.get('gpuDeadlineEpoch')
        if explicit is not None:
            if not owner.finite(explicit):raise ManagedGPUError('Invalid original caller GPU deadline.')
            seconds=min(seconds,explicit-now)
        if seconds<=200:raise ManagedGPUError('Saved remaining GPU allowance cannot fund cached native startup and cleanup.')
        label='native-image-'+uuid.uuid4().hex[:16];folder=root/'jobs'/label;folder.mkdir(parents=True,exist_ok=False)
        config_path=folder/'source-config.private.json';owner.write(config_path,config,exclusive=True)
        # Independent guard source is immutable per allocation even across a
        # later helper upgrade. Its CLI cannot create or discover/adopt a pod.
        guard=folder/'owner-runtime.py';guard.write_bytes(Path(owner.__file__).read_bytes())
        plan={'jobId':label,'projectId':project,'storeRoot':str(Path(path).resolve().parent),
              'nativeStatePath':str(Path(path).resolve()),'projectPolicySHA256':owner.policy_digest(current),
              'profilePath':str(profile_path),'profileSHA256':owner.sha(profile_path),
              'configSourcePath':str(config_path),'configSourceSHA256':owner.sha(config_path),
              'workflowPath':str(workflow),'workflowSHA256':owner.sha(workflow),
              'guardSourcePath':str(guard),'guardSourceSHA256':owner.sha(guard),
              'gpuAndDiskCapUSD':cap,'previousGPUUpperUSD':previous,'shutdownReserveUSD':reserve,
              'deadlineEpoch':now+seconds,'maximumComputeHourlyUSD':1.19,'containerDiskHourlyReserveUSD':.02,
              'maximumProviderCreateAttempts':1,'maximumConcurrentAllocations':1,'GPU':owner.GPU,
              'cloud':'SECURE','image':owner.IMAGE,'diskGB':80,'volumeId':profile['volumeId'],
              'dataCenter':profile['dataCenter'],'mountPath':'/runpod-volume',
              'setupMaximumSeconds':110,'nativeAdoptionMaximumAgeSeconds':120,
              'nativeModelCopies':2,'handoffMode':'IMMEDIATE_RUNNING_DEFER_NATIVE_CACHE_ONLY_BOOTSTRAP',
              'authorization':'existing authenticated native IMAGE job and saved project GPU cap'}
        owner.write(folder/'plan.json',plan,exclusive=True)
        release={k:plan[k] for k in ('jobId','gpuAndDiskCapUSD','previousGPUUpperUSD','shutdownReserveUSD',
                 'deadlineEpoch','maximumComputeHourlyUSD','maximumProviderCreateAttempts')}
        release.update(nativeJobAuthorized=True,planSHA256=owner.sha(folder/'plan.json'),
                       profileSHA256=owner.sha(profile_path),nativeReadyForImmediateAdoption=True,
                       allowExactOwnedExpiryDeletion=True,capacityProbeAllowed=False)
        owner.write(folder/'native-admission.json',release,exclusive=True)
        owner.write(path,{**native,'cacheOwnerRoot':str(root),'cacheJobId':label})
        owner.write(registry,{'jobId':label,'planSHA256':owner.sha(folder/'plan.json'),'status':'RESERVED','atEpoch':now})
    return owner.Job(root,label)


class CachedProductionGPU(ProductionGPU):
    """Same native bootstrap/quality gate, with one internal fresh-owner handoff."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.cache_job=None;self.cache_closed=False;self.original_config=copy.deepcopy(self.config)
        original_report=self.report
        self.report=lambda state:original_report({k:v for k,v in state.items() if k not in ('cacheOwnerRoot','cacheJobId')})
    def save(self,**values):
        if self.cache_job is None:return super().save(**values)
        job=self.cache_job
        # The lock order is the native RLock then the workstation OS lock.
        # Deadline threads and late old callbacks cannot overwrite a new owner.
        with self.lock:
            with owner.ownership_lock(job.root):
                active=owner.read(job.root/'active-owner.json') if (job.root/'active-owner.json').exists() else {}
                native=owner.read(self.path) if self.path.exists() else {}
                allocation=job.state() if job.state_path.exists() else {}
                if (active.get('jobId')!=job.plan['jobId'] or active.get('planSHA256')!=owner.sha(job.folder/'plan.json')
                    or native.get('cacheJobId')!=job.plan['jobId'] or not owner.same_directory(native.get('cacheOwnerRoot'),job.root)
                    or allocation.get('deletionVerified')):
                    self.cache_stale_save_skipped=True
                    return
                values.update(cacheJobId=job.plan['jobId'],cacheOwnerRoot=str(job.root))
                return super().save(**values)
    def ensure(self):
        # Revalidate current queued selection/extra-weight needs before reuse.
        _,current=_project(self.path,self.project)
        owner.supported_project_config(current,self.original_config)
        if self.owned:return super().ensure()
        if self.cache_closed:raise ManagedGPUError('Cached native lease is closed; no implicit replay.')
        if self.cache_job is not None:raise ManagedGPUError('Earlier cached create is unresolved; no new worker admitted.')
        self.gate('Ready image plans: admitting one private cached worker')
        self.cache_job=prepare_job(self.original_config,self.path,self.project,self.budget,self.gate)
        try:
            handoff=self.cache_job.launch(self.cache_job.folder/'native-admission.json')
            self.config=owner.read(handoff['runtimeConfigPath']);self.pod=handoff['podId']
            self.client=RunpodClient(self.config);self.owned_startup=handoff['ownedWorkerReceipt']
            self.budget=copy.deepcopy(self.budget)|handoff['requiredCloudWindowPatch']
            self.config['runpodRequireMountedCache']=True
            super().ensure()
            self.save(cacheJobId=self.cache_job.plan['jobId'],cacheOwnerRoot=str(self.cache_job.root),
                      cacheMode='REQUIRED_MOUNTED_SHA_VERIFIED',cacheDeadlineEpoch=self.cache_job.plan['deadlineEpoch'])
        except BaseException:
            self.stop('cached native startup failed; no automatic substitute')
            raise
    def check(self):
        if self.cache_job is not None and not self.cache_closed:
            self.cache_job.guard(self.cache_job.state())
        super().check()
    def stop(self,reason='cached image/QC window ended'):
        error=None
        try:super().stop(reason)
        except BaseException as exc:error=exc
        finally:
            try:
                tunnel=self.tunnel
                if tunnel is not None:
                    try:
                        if tunnel.poll() is None:tunnel.terminate()
                        tunnel.wait(timeout=5)
                    except BaseException:
                        if tunnel.poll() is None:tunnel.kill()
            except BaseException as exc:
                if error is None:error=exc
            finally:
                if self.cache_job is not None:
                    try:
                        closed=self.cache_job.cleanup()
                        self.cache_closed=all(closed.get(k) is True for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified'))
                        if self.cache_closed:
                            self.owned=False
                            with owner.ownership_lock(self.cache_job.root):
                                active=owner.read(self.cache_job.root/'active-owner.json')
                                native=owner.read(self.path) if self.path.exists() else {}
                                if (active.get('jobId')==self.cache_job.plan['jobId']
                                    and active.get('planSHA256')==owner.sha(self.cache_job.folder/'plan.json')
                                    and native.get('cacheJobId')==self.cache_job.plan['jobId']):
                                    self.state=native
                                    self.report(dict(native))
                                else:self.cache_stale_save_skipped=True
                    except BaseException as exc:
                        if error is None:error=exc
        if error is not None:raise ManagedGPUError('Cached GPU cleanup/evidence needs reconciliation; no new rental admitted.') from error


def reconcile(config,path):
    """Run before generic STOPPED short-circuit; native STOP does not delete disk."""
    root=ownership_root(config,path)
    if root is None:return None
    registry=root/'active-owner.json'
    if not registry.exists():return None
    active=owner.read(registry);job=owner.Job(root,active['jobId'])
    prior=job.state() if job.state_path.exists() else {}
    closed_owner=all(prior.get(k) is True for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified'))
    if Path(job.plan['nativeStatePath']).resolve()!=Path(path).resolve():
        if closed_owner:return None
        raise ManagedGPUError('Another Studio store owns this cached GPU; no foreign resource controlled.')
    native=owner.read(path) if Path(path).exists() else {}
    native_matches=(native.get('cacheJobId')==job.plan['jobId'] and owner.same_directory(native.get('cacheOwnerRoot'),root)
        and (native.get('status') in (None,'STOPPED') or not prior.get('podId') or native.get('podId')==prior['podId']))
    if not native_matches:
        # A closed cache owner is history. The same native file can now belong
        # to a prepared/unsupported lease, which generic reconcile must handle.
        if closed_owner:return None
        raise ManagedGPUError('Current native lease differs from the unresolved cached owner; no resource controlled.')
    state=job.cleanup()
    if not all(state.get(k) is True for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified')):
        raise ManagedGPUError('Cached GPU cleanup and spend carry remain unresolved.')
    with owner.ownership_lock(root):
        active=owner.read(registry);native=owner.read(path) if Path(path).exists() else {}
        if (active.get('jobId')!=job.plan['jobId'] or active.get('planSHA256')!=owner.sha(job.folder/'plan.json')
            or native.get('cacheJobId')!=job.plan['jobId']):
            raise ManagedGPUError('Cached recovery was superseded by a newer owner; no stale native status published.')
        # Cleanup already finalized native status and MAX carry before making
        # the owner eligible for replacement. Never write it again here.
        merged=dict(native.get('spentByProject',{}))
        ledger=owner.read(root/'spend-ledger.json')
        for project,value in ledger.get('spentByProject',{}).items():
            before=merged.get(project,0)
            if not all(owner.finite(v) and v>=0 for v in (before,value)):raise ManagedGPUError('Invalid cumulative recovery carry.')
            merged[project]=max(before,value)
        return {k:v for k,v in (native|{'spentByProject':merged}).items() if k not in ('cacheOwnerRoot','cacheJobId')}
