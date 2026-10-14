"""One durably owned allocation across GPU marketplaces, with no blind retries."""
import copy,json,math,threading,time,uuid
from pathlib import Path
from cost_control import atomic_replace
from studio_gpu_providers import GPUProviderError,GPUCapacityUnavailable,GPURequestUncertain,GPUConnectNotReady,label,number


class CloudGPULease:
    def __init__(self,config,path,project,budget,runner,gate,report=lambda *_:None,connector=None,
                 clock=time.time,clients=None,**_):
        from studio_gpu_providers import VastProvider,LiumProvider
        self.config=config;self.path=Path(path);self.project=project;self.budget=budget
        self.runner=runner;self.gate=gate;self.report=report;self.connector=connector;self.clock=clock
        secret=config.get('gpuProviderKeyFile')
        self.clients=clients or {'vast':VastProvider(secret),'lium':LiumProvider(secret)}
        self.lock=threading.RLock();self.finished=threading.Event();self.owned=False
        self.state=json.loads(self.path.read_text()) if self.path.exists() else {'status':'STOPPED'}
        if self.state.get('status')!='STOPPED':
            raise GPURequestUncertain('An earlier GPU allocation remains unresolved. Reconcile it before renting another.')
        self.pod=None;self.deadline=None

    def save(self,**values):
        with self.lock:
            self.state.update(values);self.state['renderOnly']=True
            pending=self.path.with_suffix('.writing')
            with pending.open('w',encoding='utf-8') as f:
                json.dump(self.state,f);f.flush()
            atomic_replace(pending,self.path)
            self.report(copy.deepcopy(self.state))

    def check(self):
        if self.finished.is_set():raise GPUProviderError('The cloud allocation is already closed.')
        if self.deadline and self.clock()>=self.deadline:
            self.stop('Render spending deadline reached')
            raise GPUProviderError('Render spending deadline reached; completed results retained.')

    def watch(self,finished):
        while not finished.wait(1):
            if self.deadline and self.clock()>=self.deadline:
                try:self.stop('Independent spending watchdog')
                except Exception:
                    # A failed stop never authorizes another allocation. Keep
                    # trying the same owned resource while the helper survives.
                    self.finished.wait(3)

    def ensure(self):
        if self.owned and self.state.get('status')=='RUNNING':return
        cap=number(self.budget.get('gpuBudgetUSD'),.001)
        order=self.config.get('gpuProviderOrder',['vast','lium'])
        if not isinstance(order,list) or not order or any(type(p) is not str or p not in self.clients for p in order) or len(set(order))!=len(order):
            raise GPUProviderError('Choose the configured Vast/Lium provider order.')
        max_rate=number(self.config.get('gpuMaxHourlyUSD',1.19),.01)
        disk=self.config.get('gpuWorkingDiskGB',25)
        if type(disk) is not int or not 25<=disk<=1000:raise GPUProviderError('Set a cloud working disk of 25–1000 GB.')
        previous=number(self.state.get('spentByProject',{}).get(self.project,0))
        errors=[]
        for provider_name in order:
            client=self.clients[provider_name]
            try:
                client.key()
                offers=client.offers(disk,max_rate)
                offers=[o for o in offers if o.get('downloadMbps',0)>=self.config.get('gpuMinDownloadMbps',0)
                        and o.get('reliability',0)>=self.config.get('gpuMinReliability',0)]
            except GPUProviderError as error:
                errors.append(str(error));continue  # Read-only failure is safe.
            for offer in offers[:3]:
                self.gate('Checking '+provider_name+' capacity')
                rate=number(offer['hourlyUSD'],.001)
                seconds=(cap-previous)/rate*3600-90
                if seconds<60:raise GPUProviderError('Remaining cap cannot cover startup and safe shutdown.')
                identity=uuid.uuid4().hex;owned_label=label('studio-cloud-'+identity)
                self.deadline=self.clock()+seconds;self.client=client;self.started=self.clock()
                self.state={'spentByProject':dict(self.state.get('spentByProject',{}))}
                self.owned=True
                self.save(status='SUBMISSION_UNKNOWN',provider=provider_name,project=self.project,
                    name=owned_label,podId=None,started=self.started,deadline=self.deadline,
                    hourlyUSD=rate,capUSD=cap,offerId=offer['id'],diskGB=disk)
                threading.Thread(target=self.watch,args=(self.finished,),name='studio-cloud-gpu-watch',daemon=True).start()
                try:
                    if provider_name=='vast':
                        self.pod=client.create(offer,owned_label,self.config.get('gpuRenderImage',
                            'pytorch/pytorch:2.7.1-cuda12.8-cudnn9-runtime'))
                    else:
                        template=self.config.get('liumRenderTemplate')
                        if not template:
                            # No paid request happened. Do not silently select
                            # an image containing models or a public notebook.
                            self.owned=False;self.finished.set();self.save(status='STOPPED')
                            errors.append('Lium render template is not configured.');break
                        public_key=Path(self.config['runpodSSHKeyFile']+'.pub').read_text().strip()
                        self.pod=client.create(offer,owned_label,template,public_key,identity)
                    self.save(podId=self.pod,status='STARTING')
                    if provider_name=='lium':client.schedule_stop(self.pod,owned_label,self.deadline)
                    # Cold container pulls are distinct from unavailable GPUs.
                    # They remain bounded by the actual spending deadline.
                    startup_until=min(self.deadline-20,self.started+600)
                    while self.clock()<startup_until:
                        self.check();self.gate('Starting '+provider_name+' render worker')
                        row=client.owned(self.pod,owned_label)
                        live_rate=number(row.get('dph_total') if provider_name=='vast' else row.get('price'),.001)
                        if live_rate>max_rate:
                            raise GPUProviderError('Allocated worker exceeds the approved hourly ceiling; stopping it.')
                        if live_rate!=self.state['hourlyUSD']:
                            self.deadline=self.started+(cap-previous)/live_rate*3600-90
                            self.save(hourlyUSD=live_rate,deadline=self.deadline)
                            if provider_name=='lium':client.schedule_stop(self.pod,owned_label,self.deadline)
                        if row.get('actual_status',row.get('status','')).lower()=='running':
                            try:connection=client.connection(row)
                            except GPUProviderError:
                                self.finished.wait(1);continue
                            pod={'id':self.pod,'ssh':{'direct':connection},'provider':provider_name}
                            try:self.connector(pod)
                            except GPUConnectNotReady:
                                self.finished.wait(1);continue
                            self.runner.prepare(pod);self.check()
                            self.save(status='RUNNING',ready=self.clock())
                            return
                        self.finished.wait(1)
                    raise GPUProviderError('Cloud worker startup exceeded its bounded window; this is not proof of no GPU capacity.')
                except GPUCapacityUnavailable:
                    self.owned=False;self.finished.set();self.save(status='STOPPED',reason='Offer explicitly unavailable')
                    self.finished=threading.Event()
                    continue
                except BaseException:
                    self.stop('Cloud startup failed or interrupted')
                    # Runtime errors and uncertain create responses must not
                    # drain the budget by moving to another provider.
                    raise
        raise GPUCapacityUnavailable('; '.join(errors) or 'No compatible RTX 5090 offer is available.')

    def stop(self,reason='Cloud rendering completed'):
        with self.lock:
            if not self.owned:return
            try:
                pod=self.state.get('podId')
                if not pod:
                    matches=self.client.resources(self.state['name'])
                    if len(matches)!=1:
                        raise GPURequestUncertain('Allocation outcome remains unknown. No second GPU rented.')
                    pod=str(matches[0]['id']);self.save(podId=pod)
                for attempt in range(5):
                    try:self.client.stop(pod,self.state['name']);break
                    except GPURequestUncertain:
                        if attempt==4:raise
                        time.sleep(2)
                ended=self.clock();cost=max(0,ended-self.state['started'])*self.state['hourlyUSD']/3600
                spent=dict(self.state.get('spentByProject',{}))
                spent[self.project]=spent.get(self.project,0)+cost
                self.save(status='STOPPED',stopped=ended,estimatedUSD=cost,spentByProject=spent,reason=reason,
                    retainedStorageBilled=self.state['provider']=='vast')
                self.owned=False;self.finished.set()
            except BaseException:
                self.save(status='SHUTDOWN_UNCONFIRMED',reason='Owned rental shutdown could not be confirmed. Billing may continue.')
                raise
