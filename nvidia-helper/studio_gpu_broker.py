"""One reservation across Runpod, Vast and Lium, with workload verification.

Only confirmed no-capacity outcomes permit another provider. An uncertain paid
operation or runtime failure never silently allocates a second machine.
"""
import copy,json,time
from pathlib import Path
from studio_gpu_providers import GPUCapacityUnavailable,GPUProviderError,GPURequestUncertain
from studio_runpod import ProductionGPU,ManagedGPUUnavailable,configured

PROVIDERS=('runpod','vast','lium')

def reconcile(config,path,clients=None,clock=time.time):
    """Recover only the exact persisted owner, never adopt unrelated resources."""
    path=Path(path)
    state=json.loads(path.read_text()) if path.exists() else {'status':'STOPPED'}
    if state.get('status')=='STOPPED':return state
    name=state.get('provider','runpod')
    if name=='runpod':
        from studio_runpod import reconcile as recover
        return recover(config,path)
    from studio_gpu_providers import VastProvider,LiumProvider,label
    from cost_control import atomic_replace
    if name not in ('vast','lium'):raise GPUProviderError('Unknown persisted GPU provider; no resource controlled.')
    client=(clients or {'vast':VastProvider(config.get('gpuProviderKeyFile')),'lium':LiumProvider(config.get('gpuProviderKeyFile'))})[name]
    owner=label(state.get('name'));pod=state.get('podId')
    if not pod:
        matches=client.resources(owner)
        if len(matches)!=1:raise GPURequestUncertain('The old allocation outcome remains unknown; no second GPU rented.')
        pod=str(matches[0]['id'])
    client.stop(pod,owner)
    ended=clock();cost=max(0,ended-state['started'])*state['hourlyUSD']/3600
    spent=dict(state.get('spentByProject',{}));spent[state['project']]=spent.get(state['project'],0)+cost
    state.update(status='STOPPED',podId=pod,stopped=ended,estimatedUSD=cost,spentByProject=spent,
                 reason='Recovered exact owned allocation; elapsed cost remains an estimate')
    pending=path.with_suffix('.writing');pending.write_text(json.dumps(state));atomic_replace(pending,path)
    return state

def provider_order(config):
    order=config.get('gpuProviderOrder',list(PROVIDERS))
    if not isinstance(order,list) or not order or any(type(p) is not str or p not in PROVIDERS for p in order) or len(set(order))!=len(order):
        raise ValueError('Choose a unique order of Runpod, Vast and Lium.')
    return order

def workload_verified(config,name,workload):
    if workload=='images' and name=='runpod':return configured(config)
    field='gpuImageVerifiedProviders' if workload=='images' else 'gpuRenderVerifiedProviders'
    return name in config.get(field,[])

def gpu_configured(config,workload='images'):
    return any(workload_verified(config,p,workload) for p in provider_order(config))

class GPUReservation:
    def __init__(self,config,path,project,budget,runner,gate,report=lambda *_:None,
                 workload='images',factories=None,**options):
        if workload not in ('images','render'):raise ValueError('Unknown GPU workload.')
        self.config=copy.deepcopy(config);self.path=Path(path);self.project=project;self.budget=budget
        self.runner=runner;self.gate=gate;self.report=report;self.workload=workload;self.options=options
        self.factories=factories;self.lease=None;self.attempts=[]
    @property
    def state(self):
        if self.lease:return self.lease.state
        return json.loads(self.path.read_text()) if self.path.exists() else {'status':'STOPPED'}
    @property
    def owned(self):return bool(self.lease and self.lease.owned)
    @property
    def deadline(self):return self.lease.deadline if self.lease else None
    def save(self,**values):
        if not self.lease:raise ValueError('No reserved worker exists.')
        self.lease.save(**values)
    def check(self):
        if self.lease:self.lease.check()
    def make(self,name):
        config=self.config | {'gpuProviderOrder':[name],'gpuWorkload':self.workload}
        if name=='runpod':
            # Check the prepared pool once, then try the next website if stock
            # is explicitly absent. No long rental-free wait hides alternatives.
            config['runpodAvailabilityWaitSeconds']=0
            kind=ProductionGPU
            if self.workload=='render':
                from studio_cloud_pod_render import RenderLease
                kind=RenderLease
            return kind(config,self.path,self.project,self.budget,self.runner,self.gate,
                lambda state:self.report(state | {'provider':name}),**self.options)
        from studio_cloud_gpu import CloudGPULease
        if self.workload=='images':
            selected=self.options.get('image_selection') or {}
            if selected.get('model','flux2-klein-4b')!='flux2-klein-4b':
                raise GPUProviderError('This marketplace image worker is verified only for Klein4B. No different model or GPU was rented.')
            from studio_cloud_comfy import CloudComfyRunner
            runner=CloudComfyRunner(config,self.path,self.runner,self.gate)
            return CloudGPULease(config,self.path,self.project,self.budget,runner,self.gate,self.report,
                connector=runner.connect,**{k:v for k,v in self.options.items() if k!='connector'})
        return CloudGPULease(config,self.path,self.project,self.budget,self.runner,self.gate,self.report,**self.options)
    def ensure(self):
        if self.lease and self.lease.owned:self.lease.ensure();return
        if self.lease and self.lease.state.get('status') not in (None,'STOPPED'):
            raise GPURequestUncertain('The preceding GPU window is not confirmed stopped; reconcile its saved owner before another rental.')
        for name in provider_order(self.config):
            if not workload_verified(self.config,name,self.workload):
                self.attempts.append({'provider':name,'status':'NOT_VERIFIED','paidRequest':False});continue
            self.gate('Checking '+name+' for the required RTX 5090')
            lease=(self.factories[name]() if self.factories else self.make(name));self.lease=lease
            try:
                lease.ensure()
                # A creator handoff proves ownership of its first running pod
                # only. Later dense repair windows create a fresh controller
                # from the stopped state, including the carried project spend.
                self.options.pop('owned_startup',None)
                lease.save(provider=name,providerAttempts=copy.deepcopy(self.attempts))
                self.attempts.append({'provider':name,'status':'RUNNING'})
                return
            except (ManagedGPUUnavailable,GPUCapacityUnavailable):
                # Ownership is reconciled before constructing another adapter.
                if lease.owned:lease.stop('Confirmed unavailable provider')
                if lease.state.get('status') not in (None,'STOPPED'):
                    raise GPUProviderError('Provider outcome remains unresolved; no fallback rented.')
                close=getattr(getattr(lease,'runner',None),'close',None)
                if callable(close):close()
                self.attempts.append({'provider':name,'status':'NO_ELIGIBLE_CAPACITY'})
                self.lease=None
            except BaseException:
                if lease.owned:lease.stop('Provider startup failed; no automatic substitute')
                close=getattr(getattr(lease,'runner',None),'close',None)
                if callable(close):close()
                raise
        raise GPUCapacityUnavailable('No verified provider currently has an eligible RTX 5090. Saved results and the same model are retained.')
    def stop(self,reason='GPU work ended'):
        if not self.lease:return
        try:self.lease.stop(reason)
        finally:
            runner=getattr(self.lease,'runner',None)
            close=getattr(runner,'close',None)
            if callable(close):close()

class RenderGPUReservation(GPUReservation):
    def __init__(self,*args,**kwargs):super().__init__(*args,workload='render',**kwargs)
