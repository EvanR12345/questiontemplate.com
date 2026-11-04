"""Private job-owned cached Runpod creation/expiry engine.

Used only after an authenticated native IMAGE job's saved cap and source gates.
The guard CLI can only reconcile/expire an exact persisted owner; it cannot
create a new worker. Provider bodies, credentials and actual cache IDs stay in
private owner state/config, never the public package profile format.
"""
import argparse
import base64
from contextlib import contextmanager
import datetime
import hashlib
import importlib.util
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

APP=Path(__file__).resolve().parent
GPU='NVIDIA GeForce RTX 5090'
IMAGE='runpod/comfyui:1.3.3-comfyuiv0.30.0-cuda12.8@sha256:6e295bbaa454e06fd1b3def8bfeeb7a5ba2d4d612121676c72038364137d3d75'
FLAGS=getattr(subprocess,'CREATE_NO_WINDOW',0)

def read(path):return json.loads(Path(path).read_text('utf-8-sig'))
def read_bound(path,digest):
    raw=Path(path).read_bytes()
    if hashlib.sha256(raw).hexdigest()!=digest:raise ValueError('Bound private source changed')
    return json.loads(raw.decode('utf-8-sig'))
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,value,exclusive=False):
    path=Path(path).resolve()
    path.parent.mkdir(parents=True,exist_ok=True)
    target=path if exclusive else path.with_name(path.name+'.'+uuid.uuid4().hex+'.writing')
    with target.open('x',encoding='utf-8') as stream:
        json.dump(value,stream,indent=2,allow_nan=False);stream.flush();os.fsync(stream.fileno())
    if not exclusive:target.replace(path)
def same_directory(left,right):
    """Existing directory aliases identify one counter; foreign roots fail closed."""
    try:
        a,b=Path(left),Path(right)
        return a.is_absolute() and b.is_absolute() and a.is_dir() and b.is_dir() and os.path.samefile(a,b)
    except (OSError,ValueError,TypeError):return False

def finite(value):return type(value) in (int,float) and math.isfinite(value)
@contextmanager
def ownership_lock(root):
    root=Path(root).resolve()
    root.mkdir(parents=True,exist_ok=True)
    stream=(root/'ownership.lock').open('a+b')
    if stream.seek(0,os.SEEK_END)==0:stream.write(b'0');stream.flush()
    stream.seek(0);locked=False
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        locked=True;yield
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()
def module(path):
    spec=importlib.util.spec_from_file_location('cached_runtime_'+uuid.uuid4().hex,Path(path))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value


def cloud_expiry_source(profile):
    """Private opt-in source; disabling it preserves the original launch body."""
    enabled=profile.get('cloudExpiryEnabled',False)
    if type(enabled) is not bool:raise ValueError('Cloud expiry opt-in must be boolean')
    if not enabled:return None
    path=profile.get('cloudExpiryScriptPath');digest=profile.get('cloudExpiryScriptSHA256')
    if (not isinstance(path,str) or not Path(path).is_absolute()
        or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)):
        raise ValueError('Explicit private cloud expiry source binding required')
    raw=Path(path).read_bytes()
    if len(raw)>128*1024 or hashlib.sha256(raw).hexdigest()!=digest:
        raise ValueError('Bound cloud expiry source changed')
    compile(raw,str(path),'exec')
    return raw


def cloud_expiry_command(raw):
    """Arm the pinned guard before the image's verified original /start.sh."""
    waiter="""import json,os,pathlib,time
c=json.loads(os.environ['QT_CLOUD_CONFIG']);p=int(os.environ['QT_CLOUD_GUARD_PID']);until=time.monotonic()+60
while time.monotonic()<until:
    os.kill(p,0)
    try:
        h=json.loads(pathlib.Path('/tmp/questiontemplate-cloud-guard.json').read_text())
        assert h['status']=='ARMED' and h['cloudResident'] is True and h['podId']==os.environ['RUNPOD_POD_ID']
        assert h['deadline']==c['deadline'] and 0<=time.time()-h['atEpoch']<=10
        assert h['name']==c['name'] and h['nonce']==c['nonce'] and h['pid']==p
        os.kill(p,0)
        break
    except (OSError,ValueError,KeyError,AssertionError):time.sleep(.2)
else:raise SystemExit(99)
"""
    payload=base64.b64encode(raw).decode('ascii')
    return ("set -eu; umask 077; printf '%s' "+shlex.quote(payload)+
        " | base64 -d > /tmp/questiontemplate-cloud-guard.py; "
        "python3 -u /tmp/questiontemplate-cloud-guard.py > /tmp/questiontemplate-cloud-guard.log 2>&1 & guard=$!; "
        "printf '%s' \"$guard\" > /tmp/questiontemplate-cloud-guard.pid; export QT_CLOUD_GUARD_PID=\"$guard\"; "
        "python3 -c "+shlex.quote(waiter)+"; exec /start.sh")


def cloud_heartbeat_probe(expected):
    """Remote code emits only bounded identity/proof fields, never environment."""
    packed=base64.b64encode(json.dumps(expected,separators=(',',':'),allow_nan=False).encode()).decode()
    code="""import base64,hashlib,json,os,pathlib,time
e=json.loads(base64.b64decode(PACKED));p=int(pathlib.Path('/tmp/questiontemplate-cloud-guard.pid').read_text())
assert p>1;os.kill(p,0)
script=pathlib.Path('/tmp/questiontemplate-cloud-guard.py')
assert hashlib.sha256(script.read_bytes()).hexdigest()==e['scriptSHA256']
cmd=pathlib.Path('/proc/'+str(p)+'/cmdline').read_bytes().split(b'\\0')
if cmd and cmd[-1]==b'':cmd.pop()
assert len(cmd)==3 and cmd[0] in (b'python3',b'/usr/bin/python3',b'/usr/local/bin/python3')
assert cmd[1:]==[b'-u',b'/tmp/questiontemplate-cloud-guard.py']
env=dict(x.split(b'=',1) for x in pathlib.Path('/proc/'+str(p)+'/environ').read_bytes().split(b'\\0') if b'=' in x)
c=json.loads(env[b'QT_CLOUD_CONFIG']);assert env[b'RUNPOD_POD_ID'].decode()==e['podId']
assert env[b'QT_CLOUD_NONCE'].decode()==e['nonce']
assert all(c[k]==e[k] for k in ('name','nonce','created','deadline')) and 'volume' not in c
h=json.loads(pathlib.Path('/tmp/questiontemplate-cloud-guard.json').read_text())
assert h['status']=='ARMED' and h['cloudResident'] is True and h['podId']==e['podId'] and h['deadline']==e['deadline']
assert h['name']==e['name'] and h['nonce']==e['nonce'] and h['pid']==p
assert type(h['atEpoch']) in (int,float) and 0<=time.time()-h['atEpoch']<=10
os.kill(p,0)
print(json.dumps({'version':1,'status':'ARMED','podId':e['podId'],'name':e['name'],'deadline':e['deadline'],
 'atEpoch':h['atEpoch'],'guardPID':p,'guardProcessAlive':True,'cloudResident':True,'nonceMatched':True,
 'scriptSHA256':e['scriptSHA256'],'checkedAtEpoch':time.time()}))
""".replace('PACKED',repr(packed))
    return "python3 -c "+shlex.quote(code)


def validate_cloud_heartbeat(value,expected,now):
    if (not isinstance(value,dict) or value.get('version')!=1 or value.get('status')!='ARMED'
        or any(value.get(k)!=expected[k] for k in ('podId','name','deadline','scriptSHA256'))
        or not all(value.get(k) is True for k in ('guardProcessAlive','cloudResident','nonceMatched'))
        or type(value.get('guardPID')) is not int or value['guardPID']<=1
        or not finite(value.get('atEpoch')) or not 0<=now-value['atEpoch']<=10
        or not finite(value.get('checkedAtEpoch')) or not 0<=now-value['checkedAtEpoch']<=10
        or expected['deadline']<=now):
        raise ValueError('Exact fresh cloud expiry heartbeat required before native handoff')
    return {k:value[k] for k in ('version','status','podId','name','deadline','atEpoch','guardPID',
        'guardProcessAlive','cloudResident','nonceMatched','scriptSHA256','checkedAtEpoch')}



def cache_cleanup_margin(profile):
    """A proven cloud guard can retire the GPU before the cache's expiry.

    Legacy profiles keep the three-hour workstation recovery margin. The
    shorter margin requires the exact tested script and immutable live proof.
    """
    path=profile.get('cloudExpiryLiveProbePath');digest=profile.get('cloudExpiryLiveProbeSHA256')
    if path is None and digest is None:return 10800
    raw=cloud_expiry_source(profile)
    if (raw is None or not isinstance(path,str) or not Path(path).is_absolute()
        or not isinstance(digest,str) or not re.fullmatch(r'[a-f0-9]{64}',digest)):
        raise ValueError('Source-bound live cloud expiry proof required')
    proof_path=Path(path)
    if proof_path.is_symlink() or proof_path.stat().st_size>65536 or sha(proof_path)!=digest:
        raise ValueError('Live cloud expiry proof changed')
    proof=read(proof_path);script_sha=hashlib.sha256(raw).hexdigest()
    if (proof.get('status')!='PASS_CLOUD_ONLY_EXPIRY' or proof.get('noLocalDeleteIssued') is not True
        or proof.get('independentPod404') is not True or proof.get('independentVolume404') is not True
        or proof.get('guardSHA256')!=script_sha):
        raise ValueError('Independent cloud-only deletion proof required')
    arm_path=Path(proof.get('armedReceiptPath',''))
    if (not arm_path.is_absolute() or arm_path.is_symlink() or arm_path.parent.resolve()!=proof_path.parent.resolve()
        or arm_path.stat().st_size>65536 or sha(arm_path)!=proof.get('armedReceiptSHA256')):
        raise ValueError('Exact earlier cloud arming proof required')
    arm=read(arm_path)
    if (arm.get('status')!='ARMED' or arm.get('sourceSHA256')!=script_sha
        or any(arm.get(k) is not True for k in ('guardProcessVerified','cloudResident','nonceMatched'))
        or any(arm.get(k)!=proof.get(k) for k in ('podId','name','deadline'))
        or type(arm.get('pid')) is not int or arm['pid']<=1 or not arm.get('nonce')
        or not all(finite(v) for v in (proof.get('created'),proof.get('deadline'),proof.get('confirmedAt'),arm.get('checkedAtEpoch'),arm.get('atEpoch')))
        or not proof['created']<=arm['checkedAtEpoch']<proof['deadline']<=proof['confirmedAt']
        or not 0<=arm['checkedAtEpoch']-arm['atEpoch']<12):
        raise ValueError('Live cloud arming and subsequent expiry do not match')
    if (not isinstance(proof.get('podId'),str) or not re.fullmatch(r'[a-z0-9]{8,40}',proof['podId'])
        or not isinstance(proof.get('name'),str) or not proof['name'].startswith('studio-')
        or not 0<proof['deadline']-proof['created']<=14400 or proof['confirmedAt']>time.time()+5):
        raise ValueError('Live cloud expiry identity or interval is invalid')
    return 120


def verify_profile_path(path,now=None):
    value=read(path);now=time.time() if now is None else now
    if (value.get('schemaVersion')!=1 or value.get('enabled') is not True
        or not re.fullmatch(r'[a-z0-9]{8,40}',value.get('volumeId',''))
        or value.get('dataCenter')!='EU-RO-1' or value.get('sizeGB')!=80
        or value.get('type')!='STANDARD' or value.get('mountPath')!='/runpod-volume'
        or not finite(value.get('retentionDeadlineEpoch')) or now+cache_cleanup_margin(value)>=value['retentionDeadlineEpoch']
        or not isinstance(value.get('volumeName'),str) or not value['volumeName']
        or not re.fullmatch(r'[a-f0-9]{64}',value.get('cacheManifestSHA256',''))
        or sha(value['cacheManifestPath'])!=value['cacheManifestSHA256']):
        raise ValueError('Explicit current retained-cache profile/manifest required')
    cloud_expiry_source(value)
    if type(value.get('allowOneCapacityProbe',False)) is not bool:
        raise ValueError('Capacity probe opt-in must be boolean')
    return value


def policy_digest(project):
    """Bind saved native image policy/cap, not concurrent timing/output writes.

    Actual prompts, selected sources and compiled graphs are still owned and
    validated by each normal image ExecutionJournal/pre-dispatch contract.
    """
    settings=project.get('settings',{});window=settings.get('cloudWindow',{})
    value={'project':project.get('id'),'image':settings.get('image',{}),
           'gpuBudgetUSD':window.get('gpuBudgetUSD'),'gpuPreviouslySpentUSD':window.get('gpuPreviouslySpentUSD',0)}
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()

def supported_project_config(project,config):
    """Only the exact native Klein/cache path; preserve all quality settings."""
    def selection(value):
        if (value.get('provider','comfyui')!='comfyui' or value.get('model','flux2-klein-4b')!='flux2-klein-4b'
            or value.get('workflow','flux2-klein-4b-auto')!='flux2-klein-4b-auto'
            or any(value.get(k) for k in ('loras','controlnets','ipAdapter','fallbackEnabled'))):
            raise ValueError('Only native Klein auto without extra model weights is supported')
    image=project.get('settings',{}).get('image',{})
    if config.get('studioDenseLeaseContinuity',True) is not True:
        raise ValueError('One retained image/QC lease is required; caller policy is not changed')
    if any(image.get(k)!=v for k,v in {'provider':'comfyui','model':'flux2-klein-4b','workflow':'flux2-klein-4b-auto'}.items()):
        raise ValueError('Caller must explicitly select native Comfy Klein auto')
    selection(image)
    shots=[s for c in project.get('chapters',[]) for scene in c.get('scenes',[]) for s in scene.get('shots',[])]+project.get('intro',{}).get('shots',[])
    for shot in shots:
        if any(shot.get(k) not in (None,v) for k,v in {'imageProvider':'comfyui','imageModel':'flux2-klein-4b','workflow':'flux2-klein-4b-auto'}.items()):
            raise ValueError('Per-shot model/workflow needs a different reviewed launcher')
        selection(shot.get('generationSettings',{}))
    sys.path.insert(0,str(APP))
    from studio_image_workers import model_copies,worker_endpoints
    prospective=config|{'runpodManagedPodId':'future-job-owned','runpodPreparedPodIds':['future-job-owned'],'runpodRequiredGPUType':GPU}
    if model_copies(prospective)!=2:raise ValueError('Native managed5090 two-copy policy must already be selected')
    if len(worker_endpoints(prospective))!=2:raise ValueError('Adjacent private localhost tunnel endpoints required')
    workflow_path=Path(config.get('comfyWorkflow','')).resolve()
    if not workflow_path.is_file():raise ValueError('Existing reviewed native workflow bundle required')
    bundle=read(workflow_path)
    if bundle.get('automaticWorkflows',{}).get('flux2-klein-4b-auto',{}).get('model')!='flux2-klein-4b':
        raise ValueError('Automatic workflow is not the supported native Klein model')
    allowed={'CFGGuider','CLIPLoader','CLIPTextEncode','ConditioningZeroOut','EmptyFlux2LatentImage',
             'Flux2Scheduler','ImageScaleToTotalPixels','KSamplerSelect','LoadImage','RandomNoise','ReferenceLatent',
             'SamplerCustomAdvanced','SaveImage','UNETLoader','VAEDecode','VAEEncode','VAELoader'}
    variants=[v for v in bundle.get('variants',[]) if v.get('model')=='flux2-klein-4b']
    expected={'UNETLoader':('unet_name','flux-2-klein-4b-fp8.safetensors'),
              'CLIPLoader':('clip_name','qwen_3_4b.safetensors'),'VAELoader':('vae_name','flux2-vae.safetensors')}
    if not variants:raise ValueError('Native Klein workflow variants absent')
    for variant in variants:
        nodes=variant.get('prompt',{})
        if not nodes or any(node.get('class_type') not in allowed for node in nodes.values()):
            raise ValueError('Custom loader/LoRA/ControlNet workflow is outside the verified cache path')
        for kind,(field,name) in expected.items():
            loaders=[n for n in nodes.values() if n.get('class_type')==kind]
            if len(loaders)!=1 or loaders[0].get('inputs',{}).get(field)!=name:
                raise ValueError('Workflow requests weights outside native cached Klein')
    return workflow_path


def local_ports_free(config):
    sys.path.insert(0,str(APP))
    from studio_image_workers import worker_endpoints
    prospective=config|{'runpodManagedPodId':'future-job-owned','runpodPreparedPodIds':['future-job-owned'],'runpodRequiredGPUType':GPU}
    sockets=[]
    try:
        for endpoint in worker_endpoints(prospective):
            parsed=urllib.parse.urlparse(endpoint)
            if parsed.scheme!='http' or parsed.hostname!='127.0.0.1' or not parsed.port:raise ValueError('Private local ports required')
            probe=socket.socket();sockets.append(probe)
            probe.bind(('127.0.0.1',parsed.port))
    except OSError:raise ValueError('Caller localhost tunnel ports occupied; no process killed or GPU created') from None
    finally:
        for probe in sockets:probe.close()


class APIError(RuntimeError):
    def __init__(self,status,code=None):super().__init__('Private provider request failed');self.status=status;self.code=code


class Job:
    def __init__(self,root,job):
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{3,47}',job or ''):raise ValueError('Invalid job label')
        self.root=Path(root).resolve();self.folder=self.root/'jobs'/job;self.plan=read(self.folder/'plan.json');self.state_path=self.folder/'allocation.json'
        if self.plan['jobId']!=job:raise ValueError('Job identity differs')
    def verify_profile(self):
        value=verify_profile_path(self.plan['profilePath'])
        if sha(self.plan['profilePath'])!=self.plan['profileSHA256']:raise ValueError('Job cache profile changed')
        return value
    def settle(self,state):
        with ownership_lock(self.root):
            path=self.root/'spend-ledger.json'
            ledger=read(path) if path.exists() else {'version':1,'spentByProject':{}}
            spent=ledger.setdefault('spentByProject',{})
            native=read(self.plan['nativeStatePath']) if Path(self.plan['nativeStatePath']).exists() else {}
            native_spent=native.get('spentByProject',{}).get(self.plan['projectId'],0)
            values=[spent.get(self.plan['projectId'],0),self.cost(state),native_spent]
            if not all(finite(v) and v>=0 for v in values):raise ValueError('Non-finite persisted GPU carry')
            spent[self.plan['projectId']]=max(values)
            ledger.update(lastJobId=self.plan['jobId'],atEpoch=time.time())
            write(path,ledger)
            return spent[self.plan['projectId']]
    def finalize_native(self,state):
        """Publish native closure/carry BEFORE allowing another workstation owner."""
        with ownership_lock(self.root):
            active=read(self.root/'active-owner.json') if (self.root/'active-owner.json').exists() else {}
            current=(active.get('jobId')==self.plan['jobId'] and active.get('planSHA256')==sha(self.folder/'plan.json'))
            path=Path(self.plan['nativeStatePath']);native=read(path) if path.exists() else {}
            matching=(native.get('cacheJobId')==self.plan['jobId'] and same_directory(native.get('cacheOwnerRoot'),self.root))
            if not current:
                raise ValueError('Stale cached owner cannot finalize a newer native lease')
            if not matching:
                if state.get('providerAttempted') or native:
                    raise ValueError('Native recovery pointer differs; no foreign lease state overwritten')
            else:
                ledger=read(self.root/'spend-ledger.json');spent=dict(native.get('spentByProject',{}))
                for project,value in ledger.get('spentByProject',{}).items():
                    before=spent.get(project,0)
                    if not all(finite(v) and v>=0 for v in (before,value)):raise ValueError('Invalid native cumulative carry')
                    spent[project]=max(before,value)
                native.update(status='STOPPED',cacheDeletionVerified=True,spentByProject=spent,
                    cacheCumulativeUpperUSD=spent[self.plan['projectId']],
                    cacheWindowUpperUSD=max(0,self.cost(state)-self.plan['previousGPUUpperUSD']))
                write(path,native)
            state.update(nativeFinalizedVerified=True,guardRetiredVerified=True)
            self.save(state)
            return state
    def state(self):
        value=read(self.state_path)
        if value.get('planSHA256')!=sha(self.folder/'plan.json'):raise ValueError('Owned state lost its immutable job binding')
        # The ACK is durable before the allocation update. Recover only that
        # exact ACK if the following atomic state write failed, never a name.
        ack_path=self.folder/'create-ack.json'
        if value.get('podId') is None and ack_path.exists():
            ack=read(ack_path);intent=read(self.folder/'create-intent.json')
            if (ack.get('requestedName')!=value.get('name') or ack.get('planSHA256')!=value['planSHA256']
                or intent.get('name')!=value.get('name') or intent.get('planSHA256')!=value['planSHA256']):
                raise ValueError('Acknowledged allocation lost its exact intent binding')
            value['podId']=ack['podId']
        elif (self.folder/'create-intent.json').exists() and not value.get('definitivelyRejected'):
            value['providerAttempted']=True
        return value
    def save(self,value,exclusive=False):write(self.state_path,value,exclusive)
    def claim(self):
        # One active allocation across this opt-in launcher. A different job's
        # unresolved create is never a reason to rent another worker.
        with ownership_lock(self.root):
            registry=self.root/'active-owner.json'
            if registry.exists():
                prior=read(registry);label=prior.get('jobId','')
                if not re.fullmatch(r'[a-z0-9][a-z0-9-]{3,47}',label):raise ValueError('Invalid previous launcher owner')
                path=self.root/'jobs'/label/'allocation.json'
                same=(label==self.plan['jobId'] and prior.get('planSHA256')==sha(self.folder/'plan.json'))
                if not same and (not path.exists() or not all(read(path).get(k) for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified'))):
                    raise ValueError('Another launcher allocation/UNKNOWN remains unresolved')
            write(registry,{'jobId':self.plan['jobId'],'planSHA256':sha(self.folder/'plan.json'),
                            'status':'ACTIVE','atEpoch':time.time()})
    def api(self,path,body=None,method='GET'):
        if not path.startswith('/') or '..' in path:raise ValueError('Invalid provider route')
        cfg=read(self.plan['configSourcePath']);key=Path(cfg['runpodActionKeyFile']).read_text('utf-8-sig').strip()
        request=urllib.request.Request('https://api.runpod.io/v2'+path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method=method)
        try:
            with urllib.request.urlopen(request,timeout=20) as response:
                raw=response.read();return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            raw=error.read(8000).decode(errors='replace').replace(key,'[redacted]')
            # Detail never enters stdout or the job's public receipts.
            write(self.folder/('api-error-'+uuid.uuid4().hex+'.private.json'),{'status':error.code,'detail':raw})
            try:parsed=json.loads(raw);code=parsed.get('code')
            except (ValueError,AttributeError):parsed={};code=None
            if not code and parsed.get('status')==error.code:
                expected={400:'Bad Request',403:'Forbidden',409:'Conflict',422:'Unprocessable Entity'}
                if parsed.get('title')==expected.get(error.code):code='definite_client_rejection'
            raise APIError(error.code,code) from None
    def verify_release(self,release):
        p=self.verify_profile();plan=self.plan
        if (release.get('nativeJobAuthorized') is not True or release.get('jobId')!=plan['jobId']
            or release.get('planSHA256')!=sha(self.folder/'plan.json')
            or release.get('profileSHA256')!=sha(self.plan['profilePath']) or plan['profileSHA256']!=sha(self.plan['profilePath'])
            or release.get('nativeReadyForImmediateAdoption') is not True
            or release.get('allowExactOwnedExpiryDeletion') is not True):raise ValueError('New exact immediate-adoption launch release required')
        if release.get('capacityProbeAllowed',False) is not (p.get('allowOneCapacityProbe') is True):
            raise ValueError('Capacity probe requires the exact bound private-profile opt-in')
        for field in ('gpuAndDiskCapUSD','previousGPUUpperUSD','shutdownReserveUSD','deadlineEpoch',
                      'maximumComputeHourlyUSD','maximumProviderCreateAttempts'):
            if release.get(field)!=plan[field]:raise ValueError('Released financial contract differs')
        if (sha(plan['configSourcePath'])!=plan['configSourceSHA256']
            or policy_digest(read(Path(plan['storeRoot'])/plan['projectId']/'project.json'))!=plan['projectPolicySHA256']
            or sha(plan['workflowPath'])!=plan['workflowSHA256']
            or Path(plan['guardSourcePath']).resolve()!=self.folder/'owner-runtime.py'
            or sha(plan['guardSourcePath'])!=plan['guardSourceSHA256']):
            raise ValueError('Caller config/project changed since its ready plan')
        self.guard(None);return p
    def guard(self,state=None,now=None):
        now=time.time() if now is None else now;plan=self.plan
        if now+20>=plan['deadlineEpoch']:raise ValueError('Original job financial cutoff reached')
        if state is not None:
            if self.state_path.exists() and self.state().get('deletionVerified'):raise ValueError('Owned allocation is already deleted')
            upper=self.cost(state,now)
            if upper>=plan['gpuAndDiskCapUSD']-plan['shutdownReserveUSD']:raise ValueError('Cumulative job shutdown reserve reached')
        return True
    def cost(self,state,now=None):
        now=time.time() if now is None else now
        if state.get('providerAttempted') and not state.get('podId') and not state.get('definitivelyRejected'):
            return self.plan['gpuAndDiskCapUSD']
        seconds=max(0,state.get('deletedAtEpoch',now)-state['startedEpoch']) if state.get('providerAttempted') and not state.get('definitivelyRejected') else 0
        return self.plan['previousGPUUpperUSD']+seconds*(self.plan['maximumComputeHourlyUSD']+.02)/3600
    def create_body(self,state,public):
        if not public.startswith('ssh-ed25519 ') or 'PRIVATE' in public:raise ValueError('Existing public SSH key required')
        body={'name':state['name'],'cloud':'SECURE','image':self.plan['image'],
                'gpu':{'id':GPU,'count':1,'minCudaVersion':'12.8'},'dataCenterIds':[self.plan['dataCenter']],
                'disk':80,'mounts':{'network':[{'volumeId':self.plan['volumeId'],'path':'/runpod-volume'}]},
                'ports':['22/tcp'],'startSsh':True,'startJupyter':False,'env':{'PUBLIC_KEY':public}}
        profile=self.verify_profile();raw=cloud_expiry_source(profile)
        if raw is not None:
            config=read_bound(self.plan['configSourcePath'],self.plan['configSourceSHA256'])
            key=Path(config['runpodActionKeyFile']).read_text('utf-8-sig').strip()
            if not key:raise ValueError('Existing private cloud control credential required')
            values={'name':state['name'],'nonce':self.plan['jobId'],'created':state['startedEpoch'],
                    'deadline':self.plan['deadlineEpoch']}
            if (not finite(values['created']) or not finite(values['deadline'])
                or not 0<values['deadline']-values['created']<=4*3600):raise ValueError('Invalid original cloud expiry window')
            body['env'].update(QT_CLOUD_CONFIG=json.dumps(values,separators=(',',':'),allow_nan=False),
                QT_CLOUD_API_KEY=key,QT_CLOUD_NONCE=values['nonce'])
            body.update(entrypoint=['/bin/bash','-lc'],cmd=[cloud_expiry_command(raw)])
        return body

    def verify_cloud_handoff(self,state,pod):
        profile=self.verify_profile();raw=cloud_expiry_source(profile)
        if raw is None:return None
        expected={'podId':state['podId'],'name':state['name'],'nonce':self.plan['jobId'],
            'created':state['startedEpoch'],'deadline':self.plan['deadlineEpoch'],
            'scriptSHA256':hashlib.sha256(raw).hexdigest()}
        if 'env' not in pod:
            from studio_cloud_expiry_guard import all_pods
            matches=[x for x in all_pods(self.api) if x.get('id')==expected['podId']]
            if len(matches)!=1 or matches[0].get('name')!=expected['name']:
                raise ValueError('Exact owned cloud expiry inventory differs')
            pod=pod|{'env':matches[0].get('env',{})}
        env=pod.get('env',{})
        try:cloud_config=json.loads(env.get('QT_CLOUD_CONFIG',''))
        except (ValueError,TypeError):raise ValueError('Exact owned cloud expiry configuration unreadable') from None
        if (pod.get('id')!=expected['podId'] or pod.get('name')!=expected['name']
            or env.get('QT_CLOUD_NONCE')!=expected['nonce']
            or cloud_config!={k:expected[k] for k in ('name','nonce','created','deadline')}):
            raise ValueError('Exact owned provider cloud expiry configuration differs')
        self.guard(state)
        result=subprocess.run(self.ssh_args(pod)+[cloud_heartbeat_probe(expected)],capture_output=True,text=True,
            timeout=min(5,max(1,state['setupDeadlineEpoch']-time.time())),creationflags=FLAGS)
        if result.returncode or len(result.stdout)>4096:raise ValueError('Cloud expiry heartbeat probe failed')
        try:value=json.loads(result.stdout)
        except (ValueError,TypeError):raise ValueError('Cloud expiry heartbeat proof malformed') from None
        proof=validate_cloud_heartbeat(value,expected,time.time())
        write(self.folder/'cloud-expiry-proof.json',proof,exclusive=True)
        return proof
    def exact(self,state):
        if not re.fullmatch(r'studio-two-copy-5090-[a-f0-9]{12}',state.get('name','')):raise ValueError('Unowned allocation name')
        if not state.get('podId'):raise ValueError('Unknown create cannot be adopted, resumed or deleted')
        ack=read(self.folder/'create-ack.json');intent=read(self.folder/'create-intent.json')
        if (ack.get('podId')!=state['podId'] or ack.get('requestedName')!=state['name']
            or intent.get('name')!=state['name'] or intent.get('planSHA256')!=sha(self.folder/'plan.json')):
            raise ValueError('Exact authenticated create receipt/intent required')
        return state['podId']
    def get_owned(self,state):
        pid=self.exact(state)
        try:pod=self.api('/pods/'+pid)
        except APIError as error:
            if error.status==404:return None
            raise
        if pod.get('id')!=pid or pod.get('name')!=state['name']:raise ValueError('Exact created ownership changed')
        return pod
    def validate_pod(self,pod,state):
        if (pod is None or pod.get('id')!=self.exact(state) or pod.get('name')!=state['name']
            or pod.get('gpu',{}).get('id')!=GPU or pod.get('gpu',{}).get('count')!=1
            or pod.get('cloud')!='SECURE' or pod.get('dataCenterId')!=self.plan['dataCenter']
            or not finite(pod.get('cost')) or not 0<pod['cost']<=min(self.plan['maximumComputeHourlyUSD'],state['actualCatalogPriceUSD'])):raise ValueError('Actual GPU/region/pool/price differs from the reviewed fresh catalog')
        if not any(v.get('volumeId')==self.plan['volumeId'] and v.get('path')=='/runpod-volume'
                   for v in pod.get('mounts',{}).get('network',[])):raise ValueError('Exact verified cache is not mounted')
    def scheduled(self,state,retire=False):
        task=state['taskName'];arguments='-B "'+str(self.plan['guardSourcePath'])+'" expire --root "'+str(self.root)+'" --job '+self.plan['jobId']
        if not re.fullmatch(r'QuestionTemplate-CachedJob-Expire-[a-f0-9]{12}',task):raise ValueError('Unowned financial task')
        q=lambda value:"'"+str(value).replace("'","''")+"'"
        if retire:
            if not state.get('deletionVerified') and not state.get('definitivelyRejected') and state.get('providerAttempted'):
                raise ValueError('Unconfirmed liability cannot retire its guard')
            code="$t=Get-ScheduledTask -TaskName NAME -ErrorAction SilentlyContinue;if($t){if($t.Actions[0].Execute -ne EXE -or $t.Actions[0].Arguments -ne ARGS){throw 'Unowned task'};Unregister-ScheduledTask -TaskName NAME -Confirm:$false -ErrorAction Stop};if(Get-ScheduledTask -TaskName NAME -ErrorAction SilentlyContinue){throw 'Retirement unconfirmed'};'RETIRED'"
        else:
            code="""$a=New-ScheduledTaskAction -Execute EXE -Argument ARGS
$t=New-ScheduledTaskTrigger -Once -At ([datetime]AT)
$l=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$s=New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName NAME -Action $a -Trigger @($t,$l) -Settings $s -ErrorAction Stop|Out-Null
Get-ScheduledTask -TaskName NAME|Select-Object TaskName,State,@{n='Execute';e={$_.Actions[0].Execute}},@{n='Arguments';e={$_.Actions[0].Arguments}},@{n='DeadlinePresent';e={@($_.Triggers|Where-Object{$_.StartBoundary -and ([datetime]$_.StartBoundary) -eq ([datetime]AT)}).Count -eq 1}},@{n='StartWhenAvailable';e={$_.Settings.StartWhenAvailable}},@{n='WakeToRun';e={$_.Settings.WakeToRun}}|ConvertTo-Json -Compress"""
        at=time.strftime('%Y-%m-%dT%H:%M:%S',time.localtime(math.ceil(self.plan['deadlineEpoch'])))
        for key,value in [('NAME',task),('EXE',sys.executable),('ARGS',arguments),('AT',at)]:code=code.replace(key,q(value))
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',code],capture_output=True,text=True,timeout=25,creationflags=FLAGS)
        if result.returncode:raise RuntimeError('Independent durable financial task failed')
        if retire:
            if 'RETIRED' not in result.stdout:raise ValueError('Exact guard retirement unconfirmed')
            return
        proof=json.loads(result.stdout)
        if (proof.get('TaskName')!=task or proof.get('State')=='Disabled' or proof.get('Execute')!=sys.executable
            or proof.get('Arguments')!=arguments or not all(proof.get(k) is True for k in ('DeadlinePresent','StartWhenAvailable','WakeToRun'))):raise ValueError('Financial task readback mismatch')
        write(self.folder/'scheduled-cutoff.json',proof)
    def arm(self,state):
        self.scheduled(state)
        process=subprocess.Popen([sys.executable,'-B',str(self.plan['guardSourcePath']),'watch','--root',str(self.root),'--job',self.plan['jobId']],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=FLAGS)
        end=time.monotonic()+12
        while True:
            beat=read(self.folder/'heartbeat.json') if (self.folder/'heartbeat.json').exists() else {}
            if (process.poll() is None and process.pid in (beat.get('pid'),beat.get('parentPID'))
                and beat.get('name')==state['name'] and 0<=time.time()-beat.get('atEpoch',0)<3):break
            if process.poll() is not None or time.monotonic()>=end:raise ValueError('Independent watchdog not proven; no POST')
            time.sleep(.1)
        state.update(watchdogArmed=True,watchdogLauncherPID=process.pid);self.save(state)
    def launch(self,release_path):
        release=read(release_path);profile_data=self.verify_release(release)
        if self.state_path.exists():raise ValueError('Durable allocation intent exists; no replay')
        local_ports_free(read_bound(self.plan['configSourcePath'],self.plan['configSourceSHA256']))
        volume=self.api('/network-volumes/'+self.plan['volumeId'])
        if any(volume.get(k)!=v for k,v in {'id':profile_data['volumeId'],'name':profile_data['volumeName'],'size':80,'dataCenter':'EU-RO-1','type':'STANDARD'}.items()):raise ValueError('Actual retained volume changed')
        quote=self.api('/catalog/gpus/'+urllib.parse.quote(GPU,safe='')+'?include=AVAILABILITY&product=POD&cloud=SECURE&count=1&minCudaVersion=12.8')
        price=quote.get('price',{}).get('secure')
        available=any(r.get('id')==self.plan['dataCenter'] and r.get('availability') in ('HIGH','MEDIUM','LOW') for r in (quote.get('dataCenters') or []))
        if quote.get('id')!=GPU or not quote.get('secure') or not finite(price) or not 0<price<=self.plan['maximumComputeHourlyUSD']:
            raise ValueError('Fresh reviewed 5090 catalog price/pool unavailable')
        if not available and release.get('capacityProbeAllowed') is not True:raise ValueError('No cache-region capacity; no create intent')
        token=uuid.uuid4().hex[:12];now=time.time()
        if now+180>=self.plan['deadlineEpoch']:raise ValueError('Insufficient original launch window')
        state={'status':'ARMING','name':'studio-two-copy-5090-'+token,'podId':None,'startedEpoch':now,
               'providerAttempted':False,'watchdogArmed':False,'deadlineEpoch':self.plan['deadlineEpoch'],
               'taskName':'QuestionTemplate-CachedJob-Expire-'+token,'releaseSHA256':sha(release_path),
               'actualCatalogPriceUSD':price,'setupDeadlineEpoch':min(now+110,self.plan['deadlineEpoch']-90),
               'planSHA256':sha(self.folder/'plan.json')}
        self.claim();self.save(state,exclusive=True)
        try:
            self.arm(state);self.guard(state)
            config=read(self.plan['configSourcePath']);public=Path(config['runpodSSHKeyFile']+'.pub').read_text('utf-8').strip()
            body=self.create_body(state,public)
            # Original continuous clock and cap are never rebased at POST/setup.
            state=self.state();self.guard(state)
            if not state.get('watchdogArmed') or state.get('deletionVerified'):raise ValueError('Create admission lost its independent guard')
            self.verify_release(release)
            local_ports_free(read_bound(self.plan['configSourcePath'],self.plan['configSourceSHA256']))
            write(self.folder/'create-intent.json',{'name':state['name'],'planSHA256':state['planSHA256'],
                  'startedEpoch':state['startedEpoch'],'bodySHA256':hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest(),
                  'maximumPOSTs':1},exclusive=True)
            state.update(status='CREATE_SUBMITTING_UNKNOWN',providerAttempted=True);self.save(state)
            try:
                reply=self.api('/pods',body,'POST')
                if not isinstance(reply.get('id'),str) or not re.fullmatch(r'[a-z0-9]{8,40}',reply['id']):raise ValueError('Create acknowledgement lacks exact identity')
                write(self.folder/'create-ack.json',{'podId':reply['id'],'requestedName':state['name'],
                      'planSHA256':state['planSHA256'],'atEpoch':time.time()},exclusive=True)
                state=self.state();state['podId']=reply['id'];state['status']='CREATE_ACKNOWLEDGED';self.save(state)
            except BaseException as error:
                state=self.state()
                if isinstance(error,APIError) and error.status in (400,403,409,422) and error.code in ('bad_request','forbidden','conflict','unprocessable_entity','validation_error','definite_client_rejection'):
                    result=self.api('/pods');items=result if isinstance(result,list) else result.get('data',result.get('pods',[]))
                    if isinstance(items,list) and not any(p.get('name')==state['name'] for p in items):
                        state.update(status='DEFINITIVELY_REJECTED_NO_ALLOCATION',definitivelyRejected=True);self.save(state)
                # Never adopt/delete a resource discovered by an uncertain name.
                raise
            pod=self.get_owned(state);self.validate_pod(pod,state)
            created_datetime=datetime.datetime.fromisoformat(pod['createdAt'].replace('Z','+00:00'))
            if created_datetime.tzinfo is None:raise ValueError('Provider creation time requires an explicit zone')
            created=created_datetime.timestamp()
            if not finite(created) or abs(created-state['startedEpoch'])>30:raise ValueError('Actual creation clock differs from owned intent')
            state=self.state();state['actualCreatedEpoch']=created;state['status']='ALLOCATED';self.save(state)
            return self.setup_handoff(state)
        except BaseException:
            state=self.state()
            if state.get('podId') or not state.get('providerAttempted') or state.get('definitivelyRejected'):
                self.cleanup()
            else:
                state['status']='UNKNOWN_CREATE_MANUAL_RECONCILIATION_REQUIRED';self.save(state)
                self.settle(state)
            raise
    def ssh_args(self,pod):
        endpoint=pod.get('ssh',{}).get('direct') or {};host=endpoint.get('host');ipaddress.ip_address(host)
        if endpoint.get('username')!='root' or type(endpoint.get('port')) is not int or not 0<endpoint['port']<65536:raise ValueError('Exact direct SSH not ready')
        config=read(self.plan['configSourcePath'])
        return ['ssh','-i',config['runpodSSHKeyFile'],'-p',str(endpoint['port']),'-o','BatchMode=yes',
                '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(self.folder/'known-hosts'),
                '-o','ConnectTimeout=5','root@'+host]
    def setup_handoff(self,state):
        # Original creation age is checked by native startup_pod BEFORE its own
        # CUDA/required-Klein-hash/two-process bootstrap. Do not place that whole
        # initialization in front of the120s receipt-admission window.
        self.verify_profile()
        until=min(state['setupDeadlineEpoch'],state['actualCreatedEpoch']+110)
        while time.time()<until:
            self.guard(state);pod=self.get_owned(state);self.validate_pod(pod,state)
            try:
                args=self.ssh_args(pod)
                check=subprocess.run(args+['test -d /workspace/runpod-slim/ComfyUI/models && test -d /runpod-volume/models'],
                    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    timeout=min(5,max(1,until-time.time())),creationflags=FLAGS)
                if check.returncode==0 and pod.get('status')=='RUNNING':break
            except (KeyError,ValueError,subprocess.TimeoutExpired):pass
            time.sleep(1)
        else:raise ValueError('Fresh RUNNING SSH/mount directory handoff expired')
        self.guard(state);pod=self.get_owned(state);self.validate_pod(pod,state)
        if pod.get('status')!='RUNNING' or not 0<=time.time()-state['actualCreatedEpoch']<110:
            raise ValueError('Original creation age expired; no timestamp rebase')
        cloud_proof=self.verify_cloud_handoff(state,pod)
        self.verify_profile()
        if (sha(self.plan['configSourcePath'])!=self.plan['configSourceSHA256']
            or policy_digest(read(Path(self.plan['storeRoot'])/self.plan['projectId']/'project.json'))!=self.plan['projectPolicySHA256']
            or sha(self.plan['workflowPath'])!=self.plan['workflowSHA256']):
            raise ValueError('Caller changed its native ready project/config during startup')
        write(self.folder/'mount-transport-proof.json',{'podId':state['podId'],
              'volumeId':self.plan['volumeId'],'mountPath':'/runpod-volume','modelsDirectoryProbe':True,
              'status':'RUNNING','atEpoch':time.time(),'cacheVerification':'PENDING_NATIVE_CACHE_ONLY_BOOTSTRAP',
              'freshWorkerWeightSHAClaim':False,'freshEightWeightSHAClaim':False},exclusive=True)
        # The preview file is non-authoritative. Derive from one exact hashed
        # source read, so proposal edits cannot bypass model/two-worker policy.
        config=read_bound(self.plan['configSourcePath'],self.plan['configSourceSHA256'])
        config.update(gpuProviderOrder=['runpod'],runpodRequiredGPUType=GPU,
                      runpodManagedPodId=state['podId'],runpodPreparedPodIds=[state['podId']],
                      runpodBootstrapKleinPodIds=[state['podId']],runpodMaxHourlyUSD=self.plan['maximumComputeHourlyUSD'],runpodRequireMountedCache=True)
        supported_project_config(read(Path(self.plan['storeRoot'])/self.plan['projectId']/'project.json'),config)
        write(self.folder/'runtime-config.private.json',config,exclusive=True)
        carried=self.plan['previousGPUUpperUSD']+max(0,state['actualCreatedEpoch']-state['startedEpoch'])*(self.plan['maximumComputeHourlyUSD']+.02)/3600
        result={'status':'FRESH_RUNNING_HANDOFF_READY','jobId':self.plan['jobId'],'podId':state['podId'],
                'runtimeConfigPath':str(self.folder/'runtime-config.private.json'),'deadlineEpoch':self.plan['deadlineEpoch'],
                'ownedWorkerReceipt':{'project':self.plan['projectId'],'podId':state['podId'],'name':state['name'],
                                      'started':state['actualCreatedEpoch'],'capUSD':self.plan['gpuAndDiskCapUSD']},
                'requiredCloudWindowPatch':{'gpuBudgetUSD':self.plan['gpuAndDiskCapUSD'],'gpuPreviouslySpentUSD':carried},
                'applyBudgetPatchBeforeNativeAdoption':True,'nativeAdoptBeforeEpoch':state['actualCreatedEpoch']+120,
                'installedConfigChanged':False,'GPUStoppedBeforeHandoff':False,'expiryWatchdogRetainsCleanupDuty':True}
        result.update(cacheVerification='PENDING_NATIVE_BOOTSTRAP',freshWorkerWeightSHAClaim=False,
                      freshEightWeightSHAClaim=False,nativeBootstrapRequiredBeforeDispatch=True,
                      nativeRequiredModel='flux2-klein-4b',nativeModelCopies=2)
        if cloud_proof is not None:
            result.update(cloudResidentExpiryConfigured=True,cloudResidentExpiryArmed=True)
        write(self.folder/'handoff.json',result,exclusive=True)
        state=self.state();state.update(status='HANDOFF_RUNNING',handoffEpoch=time.time());self.save(state)
        return result
    def cleanup(self):
        if not self.state_path.exists():
            if any((self.folder/name).exists() for name in ('create-intent.json','create-ack.json')):
                raise ValueError('Missing allocation state with paid intent remains UNKNOWN')
            state={'status':'NO_ALLOCATION','planSHA256':sha(self.folder/'plan.json'),
                   'providerAttempted':False,'startedEpoch':time.time(),'deletionVerified':True}
            self.save(state,exclusive=True)
        state=self.state()
        if state.get('deletionVerified'):
            if all(state.get(k) for k in ('settlementVerified','guardRetiredVerified','nativeFinalizedVerified')):return state
            if not state.get('settlementVerified'):
                self.settle(state);state['settlementVerified']=True;self.save(state)
            if state.get('taskName'):self.scheduled(state,True)
            return self.finalize_native(state)
        if not state.get('podId'):
            if state.get('providerAttempted') and not state.get('definitivelyRejected'):
                raise ValueError('UNKNOWN create cannot be adopted/deleted; reservation and guard retained')
            state.update(status='NO_ALLOCATION',deletionVerified=True);self.save(state);self.settle(state)
            state['settlementVerified']=True;self.save(state);self.scheduled(state,True)
            return self.finalize_native(state)
        pod=self.get_owned(state)
        if pod is not None:
            try:self.api('/pods/'+state['podId'],method='DELETE')
            except APIError as error:
                if error.status!=404:raise
            until=time.monotonic()+25
            while self.get_owned(state) is not None:
                if time.monotonic()>=until:raise ValueError('Exact owned delete unconfirmed')
                time.sleep(1)
        state=self.state();state.update(status='DELETED',deletionVerified=True,deletedAtEpoch=time.time(),
                                       get404Verified=True);self.save(state)
        write(self.folder/'final-cost.json',{'gpuAndDiskConservativeUpperUSD':self.cost(state),
            'capUSD':self.plan['gpuAndDiskCapUSD'],'priorUSD':self.plan['previousGPUUpperUSD'],
            'cacheVolumeDeleted':False,'invoiceVerified':False})
        self.settle(state)
        state['settlementVerified']=True;self.save(state)
        self.scheduled(state,True);return self.finalize_native(state)
    def adopted_or_closed(self,state):
        path=Path(self.plan['nativeStatePath'])
        if not path.exists():return False,False
        lease=read(path)
        exact=(lease.get('podId')==state.get('podId') and lease.get('project')==self.plan['projectId']
               and lease.get('started')==state.get('actualCreatedEpoch') and lease.get('startupHandoff') is True
               and lease.get('capUSD')==self.plan['gpuAndDiskCapUSD'])
        return exact and lease.get('status') in ('STARTING','RUNNING'),exact and lease.get('status')=='STOPPED'
    def expire(self):
        state=self.state()
        if state.get('deletionVerified'):return self.cleanup()
        if not state.get('providerAttempted'):return self.cleanup() if time.time()>=self.plan['deadlineEpoch'] else state
        if not state.get('podId'):raise ValueError('UNKNOWN create requires manual reconciliation; never allocate/adopt/delete a substitute')
        adopted,closed=self.adopted_or_closed(state)
        cutoff=time.time()>=self.plan['deadlineEpoch'] or self.cost(state)>=self.plan['gpuAndDiskCapUSD']-self.plan['shutdownReserveUSD']
        missed=state.get('actualCreatedEpoch') and time.time()>=state['actualCreatedEpoch']+120 and not adopted
        if cutoff or missed or closed:return self.cleanup()
        return state
    def watch(self):
        while True:
            state=self.state();write(self.folder/'heartbeat.json',{'pid':os.getpid(),'parentPID':os.getppid(),'name':state['name'],'atEpoch':time.time()})
            if all(state.get(k) for k in ('deletionVerified','settlementVerified','guardRetiredVerified','nativeFinalizedVerified')):return
            try:self.expire()
            except Exception as error:write(self.folder/'guard-error.json',{'errorType':type(error).__name__,'unconfirmedLiabilityRetained':True,'atEpoch':time.time()})
            time.sleep(1)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['watch','expire','cleanup']);parser.add_argument('--root',required=True);parser.add_argument('--job',required=True)
    args=parser.parse_args();job=Job(args.root,args.job)
    try:
        value=job.watch() if args.action=='watch' else job.expire() if args.action=='expire' else job.cleanup()
        print(json.dumps({'status':value.get('status','WATCHER_CLOSED') if isinstance(value,dict) else 'WATCHER_CLOSED'}))
    except BaseException as error:
        print(json.dumps({'status':'UNCONFIRMED_LIABILITY_RETAINED','errorType':type(error).__name__}));raise SystemExit(1) from None
if __name__=='__main__':main()
