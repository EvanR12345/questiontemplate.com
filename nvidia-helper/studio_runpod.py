"""Own one explicitly configured reusable pod during a production run.

REST v2 contract verified against Runpod's live OpenAPI schema. Never creates
or deletes pods, adopts externally running rentals, or exposes a Comfy port.
The helper must stay running for the independent deadline watchdog.
"""
import ipaddress,json,math,os,re,subprocess,threading,time,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlparse
from cost_control import atomic_replace


class ManagedGPUError(RuntimeError):pass


def configured(config):return bool(config.get('runpodManagedPodId') or config.get('runpodPreparedPodIds'))


def candidate_ids(config):
    """Only explicitly prepared workers; never discover/adopt account-wide pods."""
    values=config.get('runpodPreparedPodIds') or [config.get('runpodManagedPodId')]
    if not isinstance(values,list) or not 1<=len(values)<=20 or any(
            not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',v) for v in values):
        raise ManagedGPUError('Configure one to twenty verified prepared worker IDs.')
    return list(dict.fromkeys(values))


def reconcile(config,path,client=None):
    """Confirm/stop only the exact saved owned rental after an interruption."""
    path=Path(path)
    if not path.exists():return {'status':'STOPPED','reason':'No saved managed rental'}
    state=json.loads(path.read_text())
    if state.get('status')=='STOPPED':return state
    pod=state.get('podId')
    if pod not in candidate_ids(config):
        raise ManagedGPUError('The interrupted rental belongs to another configured pod. Restore that configuration before reconciling.')
    client=client or RunpodClient(config)
    current=client.request(pod)
    if current.get('id')!=pod:raise ManagedGPUError('Rental reconciliation identity mismatch.')
    if current.get('status') not in ('EXITED','ERROR'):
        client.request(pod,'stop');current=client.request(pod)
    if current.get('status') not in ('EXITED','ERROR'):
        raise ManagedGPUError('Runpod has not confirmed shutdown. The saved rental remains unresolved.')
    ended=time.time();seconds=max(0,ended-state['started']);cost=seconds*state['hourlyUSD']/3600
    spent=dict(state.get('spentByProject',{}));spent[state['project']]=spent.get(state['project'],0)+cost
    state.update(status='STOPPED',stopped=ended,seconds=seconds,estimatedUSD=cost,
                 spentByProject=spent,reason='Recovered interrupted rental; verify actual billing')
    pending=path.with_suffix('.writing')
    with pending.open('w') as f:json.dump(state,f);f.flush();os.fsync(f.fileno())
    atomic_replace(pending,path)
    return state


class RunpodClient:
    def __init__(self,config):self.config=config
    def gpu_catalog(self):
        """Read live regional stock with the explicitly configured CLI credential."""
        cli=Path(self.config.get('runpodCLI',''))
        if not cli.is_file():raise ManagedGPUError('Configured Runpod inventory CLI is unavailable.')
        try:key=Path(self.config['runpodActionKeyFile']).read_text(encoding='utf-8-sig').strip()
        except (OSError,KeyError):raise ManagedGPUError('Existing private inventory credential is unavailable.') from None
        if not key.startswith('rpa_'):raise ManagedGPUError('Invalid private inventory credential.')
        environment=os.environ.copy();environment['RUNPOD_API_KEY']=key
        try:
            result=subprocess.run([str(cli),'gpu','list','--include-unavailable'],env=environment,
                capture_output=True,text=True,timeout=20,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise ValueError()
            rows=json.loads(result.stdout)
            if not isinstance(rows,list):raise ValueError()
            return rows
        except (OSError,ValueError,subprocess.TimeoutExpired):
            raise ManagedGPUError('Live GPU availability could not be verified. No new worker started.') from None
    def request(self,pod,action=None):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{8,64}',pod):raise ValueError('Invalid configured Runpod pod identity.')
        if action not in (None,'start','stop'):raise ValueError('Only start and stop are supported.')
        if action and self.config.get('runpodActionKeyFile'):
            # Explicit legacy control path for an already approved GraphQL-only
            # credential. Never switch credentials automatically after a 403.
            # Reads keep their configured REST key and normalized pod contract.
            cli=Path(self.config.get('runpodCLI',''))
            if not cli.is_file():raise ManagedGPUError('Configured Runpod control CLI is unavailable.')
            try:key=Path(self.config['runpodActionKeyFile']).read_text(encoding='utf-8-sig').strip()
            except OSError:raise ManagedGPUError('Existing private worker control credential is unavailable.') from None
            if not key.startswith('rpa_'):raise ManagedGPUError('Invalid private worker control credential.')
            environment=os.environ.copy();environment['RUNPOD_API_KEY']=key
            try:
                result=subprocess.run([str(cli),'pod',action,pod],env=environment,
                    capture_output=True,text=True,timeout=20,
                    creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except (OSError,subprocess.TimeoutExpired):
                raise ManagedGPUError('Runpod control outcome is uncertain; check the saved rental state before retrying.') from None
            if result.returncode:
                # Keep raw CLI bodies private. Expose only a short provider
                # reason, redacting credentials BEFORE truncation.
                try:failure=json.loads(result.stderr or result.stdout)
                except (ValueError,TypeError):failure={}
                detail=failure.get('error','') if isinstance(failure,dict) else ''
                detail=detail if isinstance(detail,str) else ''
                detail=detail.replace(key,'[redacted]')
                detail=re.sub(r'rpa_[A-Za-z0-9_-]+','[redacted]',detail)
                detail=re.sub(r'https?://\S+','[provider URL]',detail)[:300]
                raise ManagedGPUError('Existing Runpod worker control failed. '+detail+' Saved ownership and results are retained.')
            return self.request(pod)
        try:key=json.loads(Path(self.config['runpodKeyFile']).read_text(encoding='utf-8'))['runpodApiKey']
        except (OSError,KeyError,ValueError):raise ManagedGPUError('Save a Runpod management credential in Cloud setup.') from None
        request=urllib.request.Request('https://api.runpod.io/v2/pods/'+pod+('/action' if action else ''),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','User-Agent':'QuestionTemplateStudio/1.0'},
            data=json.dumps({'action':action}).encode() if action else None)
        try:
            with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)
        except urllib.error.HTTPError as error:
            raise ManagedGPUError('Runpod denied worker control (HTTP '+str(error.code)+'). Verify the configured pod and management grant; saved work is retained.') from None
        except OSError:raise ManagedGPUError('Runpod control connection interrupted. Check the saved rental state before retrying.') from None


class ProductionGPU:
    def __init__(self,config,path,project,budget,provider,gate,report=lambda *_:None,client=None,connector=None,clock=time.time,image_selection=None):
        self.config=config;self.path=Path(path);self.project=project;self.budget=budget
        self.provider=provider;self.gate=gate;self.report=report;self.clock=clock
        self.client=client or RunpodClient(config);self.connector=connector or self.connect
        self.lock=threading.RLock();self.start_lock=threading.Lock();self.finished=threading.Event();self.tunnel=None;self.error=None
        self.state=json.loads(self.path.read_text()) if self.path.exists() else {}
        if self.state.get('status') not in (None,'STOPPED'):
            raise ManagedGPUError('An earlier managed rental needs reconciliation. Check its saved pod state before another run; no second worker started.')
        self.owned=False;self.pod=config.get('runpodManagedPodId');self.rented=False
        self.image_selection=image_selection

    def select_prepared_worker(self):
        """Bounded, cancellable free reads before purchasing one rental.

        A start action is eligibility, not a capacity guarantee. A failed/uncertain
        start still follows ownership and shutdown reconciliation; never buys a
        second worker automatically. Each registered worker needs the same model.
        """
        ids=candidate_ids(self.config)
        required=self.config.get('runpodRequiredGPUType','')
        wait=self.config.get('runpodAvailabilityWaitSeconds',0)
        if not isinstance(required,str) or len(required)>128 or type(wait) not in (int,float) or not math.isfinite(wait) or not 0<=wait<=86400:
            raise ManagedGPUError('Invalid GPU availability policy.')
        # Preserve the established single-worker behavior unless opted in.
        if not required and not self.config.get('runpodPreparedPodIds'):
            return self.client.request(self.pod)
        until=self.clock()+wait
        while True:
            self.gate('Waiting for a prepared '+(required or 'image GPU')+' worker; no rental started')
            stock=None
            if self.config.get('runpodCheckRegionalStock'):
                stock={(g.get('gpuId'),d.get('dataCenterId')) for g in self.client.gpu_catalog()
                    if isinstance(g,dict) for d in g.get('dataCenterAvailability',[])
                    if isinstance(d,dict) and d.get('stockStatus') in ('Low','Medium','High')}
            eligible=[]
            for pid in ids:
                self.gate('Checking prepared image worker availability; no rental started')
                pod=self.client.request(pid)
                gpu=pod.get('gpu') or {}
                if pod.get('id')!=pid or required and gpu.get('id')!=required:continue
                if stock is not None and (gpu.get('id'),pod.get('dataCenterId')) not in stock:continue
                if pod.get('status') not in ('EXITED','ERROR') or 'start' not in pod.get('actions',[]):continue
                rate=pod.get('cost')
                if type(rate) not in (int,float) or not math.isfinite(rate) or rate<=0:continue
                maximum=self.config.get('runpodMaxHourlyUSD')
                if maximum is not None:
                    if type(maximum) not in (int,float) or not math.isfinite(maximum) or maximum<=0:
                        raise ManagedGPUError('Set a positive maximum hourly GPU price.')
                    if rate>maximum:continue
                eligible.append((rate,pid,pod))
            if eligible:
                _,self.pod,pod=min(eligible,key=lambda item:(item[0],item[1]))
                return pod
            if self.clock()>=until:
                raise ManagedGPUError('No eligible prepared GPU became available within the wait limit. No rental started and no different GPU substituted.')
            # Cancellation is checked at least once a second, even while waiting.
            for _ in range(min(30,max(1,math.ceil(until-self.clock())))):
                self.gate('Waiting for GPU availability; no rental started')
                if self.finished.wait(1):raise ManagedGPUError('GPU availability wait cancelled.')
                if self.clock()>=until:break

    def save(self,**values):
        with self.lock:
            self.state.update(values)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.writing')
            with temporary.open('w',encoding='utf-8') as f:
                json.dump(self.state,f);f.flush();os.fsync(f.fileno())
            atomic_replace(temporary,self.path)
            self.report(dict(self.state))

    def ensure(self):
        with self.start_lock:
            if self.error:raise ManagedGPUError(self.error)
            if self.owned:return
            if self.rented:raise ManagedGPUError('This rental was already closed; start another saved production job for additional images.')
            self.gate('Checking the configured image GPU')
            pod=self.select_prepared_worker()
            if pod.get('id')!=self.pod or pod.get('status') not in ('EXITED','ERROR') or 'start' not in pod.get('actions',[]):
                raise ManagedGPUError('Managed generation requires its configured stopped pod. Studio will not adopt or stop a separately running GPU.')
            rate=pod.get('cost');cap=self.budget.get('gpuBudgetUSD');previous=self.budget.get('gpuPreviouslySpentUSD',0)
            carried=self.state.get('spentByProject',{}).get(self.project,0)
            if any(type(n) not in (int,float) or not math.isfinite(n) or n<0 for n in (rate,cap,previous,carried)) or rate<=0:
                raise ManagedGPUError('Set a finite GPU spending cap before automatic worker startup.')
            previous=max(previous,carried)
            seconds=(cap-previous)/rate*3600-90
            if seconds<60:raise ManagedGPUError('GPU cap cannot cover startup and the 90-second shutdown allowance; no rental started.')
            self.started=self.clock();self.rate=rate;self.previous=previous
            self.deadline=self.started+seconds
            # A stopped rental's elapsed cost, readiness and stopped timestamp
            # belong to its saved project history, not this new rental window.
            self.state={'spentByProject':dict(self.state.get('spentByProject',{}))}
            # Save ownership BEFORE dispatch: a lost start response is uncertain,
            # not permission to buy a second rental or discard the shutdown duty.
            self.owned=True;self.rented=True
            self.save(podId=self.pod,project=self.project,status='STARTING',started=self.started,
                      hourlyUSD=rate,capUSD=cap,deadline=self.deadline)
            threading.Thread(target=self.watch,name='studio-rental-deadline',daemon=True).start()
            try:
                self.client.request(self.pod,'start')
                until=min(self.deadline,self.started+300)
                while self.clock()<until:
                    self.check();self.gate('Starting the image GPU and private connection')
                    current=self.client.request(self.pod)
                    if current.get('status')=='RUNNING':
                        required=self.config.get('runpodRequiredGPUType')
                        if required and (current.get('gpu') or {}).get('id')!=required:
                            raise ManagedGPUError('Started worker GPU differs from the required GPU; shutting down without generating images.')
                        if not (current.get('ssh',{}).get('direct') or {}).get('host'):
                            self.finished.wait(1);continue
                        self.connector(current)
                        if self.tunnel and self.tunnel.poll() is not None:
                            raise ManagedGPUError('Private SSH connection failed. Check the dedicated key and localhost port; no public image port was opened.')
                        self.bootstrap(current)
                        self.provider._health=(0,None);self.provider._server_memory=(0,None)
                        self.provider._node_catalog=(0,None)
                        health=(self.provider.healthCheck(**self.image_selection) if self.image_selection else self.provider.healthCheck())
                        if health.get('installed'):
                            self.check()
                            if not self.owned:raise ManagedGPUError('Rental closed during worker startup.')
                            self.save(status='RUNNING',ready=self.clock());return
                    self.finished.wait(1)
                raise ManagedGPUError('Image GPU did not become ready within its startup window.')
            except BaseException:
                self.stop('startup failed');raise

    def connect(self,pod):
        if self.tunnel and self.tunnel.poll() is None:return
        ssh=pod.get('ssh',{}).get('direct') or {}
        address=ssh.get('host');port=ssh.get('port');username=ssh.get('username')
        try:ipaddress.ip_address(address)
        except (ValueError,TypeError):raise ManagedGPUError('Runpod has not returned a direct SSH address yet.') from None
        if type(port) is not int or not 1<=port<=65535:raise ManagedGPUError('Runpod has not returned an SSH port yet.')
        if username!='root':raise ManagedGPUError('The configured image worker must use its verified root SSH account.')
        key=Path(self.config.get('runpodSSHKeyFile',''))
        if not key.is_file():raise ManagedGPUError('Configure the existing dedicated SSH key; no new credential was created.')
        endpoint=urlparse(self.config.get('comfyEndpoint','http://127.0.0.1:8188'))
        if endpoint.scheme!='http' or endpoint.hostname!='127.0.0.1' or not endpoint.port:
            raise ManagedGPUError('Managed ComfyUI must use the private localhost SSH tunnel.')
        args=['ssh','-i',str(key),'-p',str(port),'-o','BatchMode=yes','-o','StrictHostKeyChecking=accept-new',
              '-o','UserKnownHostsFile='+str(self.path.with_suffix('.known-hosts')),
              '-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-N','-L',
              f'127.0.0.1:{endpoint.port}:127.0.0.1:8188','root@'+address]
        self.tunnel=subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)

    def bootstrap(self,pod):
        """Rebuild only explicitly opted-in ephemeral worker models after a stop.

        This runs inside the owned rental watchdog, never before the spending
        gate. No credentials or project assets are sent to the worker here.
        """
        ids=self.config.get('runpodBootstrapKleinPodIds',[])
        if self.pod not in ids or getattr(self,'bootstrapped',False):return
        if self.pod not in candidate_ids(self.config):
            raise ManagedGPUError('Bootstrap worker is outside the prepared pool.')
        self.check();self.gate('Preparing verified image model weights on the cloud worker')
        direct=pod.get('ssh',{}).get('direct') or {}
        try:ipaddress.ip_address(direct.get('host'))
        except (TypeError,ValueError):raise ManagedGPUError('Private bootstrap SSH address is unavailable.') from None
        port=direct.get('port')
        if type(port) is not int or not 1<=port<=65535 or direct.get('username')!='root':
            raise ManagedGPUError('Invalid bootstrap SSH connection.')
        from studio_worker_bootstrap import remote_script
        args=['ssh','-i',self.config['runpodSSHKeyFile'],'-p',str(port),'-o','BatchMode=yes',
            '-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new',
            '-o','UserKnownHostsFile='+str(self.path.with_suffix('.known-hosts')),
            'root@'+direct['host'],'python3 -']
        until=min(self.deadline-20,self.started+300)
        # RUNNING and an advertised port can precede a usable SSH service.
        # Probe only transport readiness; no inference is dispatched/retried.
        while True:
            self.check();self.gate('Waiting for the private image-worker connection')
            if not self.owned or self.clock()>=until:
                raise ManagedGPUError('Private model preparation connection exceeded startup window.')
            probe=subprocess.run(args[:-1]+['test -d /workspace/runpod-slim/ComfyUI/models'],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                timeout=8,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if probe.returncode==0:break
            self.finished.wait(2)
        process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
            text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        first=True
        try:
            while True:
                self.check();self.gate('Preparing verified image model weights on the cloud worker')
                if not self.owned or self.clock()>=until:
                    raise ManagedGPUError('Model preparation exceeded the bounded startup window.')
                try:
                    output,_=process.communicate(input=remote_script() if first else None,timeout=1)
                    break
                except subprocess.TimeoutExpired:first=False
            if process.returncode or 'MODELS_VERIFIED' not in output:
                raise ManagedGPUError('Cloud model preparation failed; no image request submitted.')
            self.bootstrapped=True
        finally:
            if process.poll() is None:process.kill();process.communicate()

    def check(self):
        if self.error:raise ManagedGPUError(self.error)
        if self.owned and self.clock()>=self.deadline:
            self.error='GPU spending window reached; saved images and request IDs are retained.'
            self.stop('budget window reached');raise ManagedGPUError(self.error)

    def watch(self):
        while not self.finished.wait(1):
            if self.clock()>=self.deadline:
                self.error='GPU spending window reached; saved images and request IDs are retained.'
                self.stop('budget window reached');return

    def stop(self,reason='images finished'):
        with self.lock:
            if not self.owned:return
            self.finished.set();last=None
            for _ in range(3):
                try:
                    pod=self.client.request(self.pod)
                    if pod.get('id')!=self.pod:raise ManagedGPUError('Rental shutdown identity mismatch.')
                    if pod.get('status') not in ('EXITED','ERROR'):
                        self.client.request(self.pod,'stop');pod=self.client.request(self.pod)
                    if pod.get('status') not in ('EXITED','ERROR'):raise ManagedGPUError('Runpod has not confirmed shutdown.')
                    ended=self.clock();cost=max(0,ended-self.started)*self.rate/3600
                    spent=dict(self.state.get('spentByProject',{}));spent[self.project]=self.previous+cost
                    self.save(status='STOPPED',stopped=ended,seconds=ended-self.started,estimatedUSD=cost,
                              spentByProject=spent,reason=reason)
                    self.owned=False
                    if self.tunnel and self.tunnel.poll() is None:self.tunnel.terminate()
                    return
                except Exception as e:last=e
            self.error='GPU shutdown could not be verified. Stop the saved pod in Runpod now; billing may continue.'
            self.save(status='SHUTDOWN_UNCONFIRMED',reason=reason)
            raise ManagedGPUError(self.error) from last
