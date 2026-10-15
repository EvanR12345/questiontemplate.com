"""Owned render-only GPU lease and direct worker-to-Google object delivery."""
import base64,copy,datetime,gzip,hashlib,io,json,math,mimetypes,re,subprocess,threading,time,zipfile
from pathlib import Path
from types import SimpleNamespace
from studio_runpod import ProductionGPU,ManagedGPUError,bootstrap_diagnostic

SOURCES=('studio_voice_provider.py','studio_cloud_inputs.py','studio_cloud_render_plan.py','studio_remote_render_worker.py','studio_render.py','studio_gpu_render.py','studio_gpu_codec.py',
    'studio_gpu_motion.py','studio_gpu_runtime.py','studio_data.py','studio_qc.py','studio_video_settings.py',
    'studio_render_inputs.py','studio_branding.py','studio_engagement.py','render_queue.py','studio_automation.py','studio_director_limits.py')

def cloud_result(result, uploaded, provider, revision):
    """Map every playable output to its verified immutable cloud object."""
    value=copy.deepcopy(result)
    def mapped(item):
        if isinstance(item,list):
            for child in item:mapped(child)
            return
        if not isinstance(item,dict):return
        path=item.get('path')
        if isinstance(path,str) and path.endswith('.mp4'):
            if path not in uploaded:raise ValueError('A reported video has no verified cloud output.')
            item.update(path=uploaded[path]['path'],cloud=True,renderLocation=provider,sourceRevision=revision)
        for key,child in item.items():
            if key!='path':mapped(child)
    if result.get('path') not in uploaded:raise ValueError('A reported video has no verified cloud output.')
    mapped(value)
    return value

def control_packet(value):
    raw=json.dumps(value,separators=(',',':')).encode()
    if len(raw)>64*1024*1024:raise ValueError('Render metadata exceeds the safe transfer size; split the project into parts.')
    packet=json.dumps({'encoding':'gzip-json','sha256':hashlib.sha256(raw).hexdigest(),
        'payload':base64.b64encode(gzip.compress(raw)).decode()},separators=(',',':'))+'\n'
    if len(packet)>16*1024*1024:raise ValueError('Render control exceeds the safe transfer size; split the project into parts.')
    return packet


def render_assets(project):
    """Only saved media inputs; existing MP4s cannot turn this into a cache test."""
    paths=set()
    for chapter in project['chapters']:
        if not chapter['sourceText'].strip():continue
        paths.add(chapter.get('audio',{}).get('path'))
        for scene in chapter['scenes']:
            for shot in scene['shots']:
                paths.add(shot.get('imagePath'))
                for ref in shot.get('referenceImages',[]):paths.add(ref.get('path'))
        for person in chapter.get('people',[]):
            for ref in person.get('references',[]):paths.add(ref.get('path'))
    for person in project['characters']:
        for ref in person.get('references',[]):paths.add(ref.get('path'))
    for location in project.get('locations',[]):
        for ref in location.get('references',[]):paths.add(ref.get('path'))
    for ref in project.get('styleReferences',[]):paths.add(ref.get('path'))
    intro=project['intro']
    if intro['enabled']:
        paths.update((intro.get('audioPath'),intro.get('visualPath')))
        for shot in intro.get('shots',[]):paths.add(shot.get('imagePath'))
    watermark=project['settings'].get('watermark',{})
    if watermark.get('enabled') and watermark.get('type')=='image':paths.add(watermark.get('imagePath'))
    engagement=project['settings'].get('engagement',{})
    if engagement.get('outroEnabled'):paths.add(engagement.get('outroAudioPath'))
    return sorted(p for p in paths if p)


class RenderLease(ProductionGPU):
    owned_startup_pattern=r'studio-render-only-5090-[a-f0-9]{12}'
    def bootstrap(self,pod):self.runner.prepare(pod)
    def save(self,**values):super().save(**(values | {'renderOnly':True}))


class CloudPodRenderer:
    def __init__(self,store,archive,config,gate,state_path,progress=lambda *_:None,lease_factory=RenderLease):
        self.store,self.archive,self.config,self.gate=store,archive,copy.deepcopy(config),gate
        self.state_path=Path(state_path);self.progress=progress;self.ready=False;self.ssh=None
        self.report={};self.uploaded={};self.process=None;self.zip_bytes=None
        self.lease_factory=lease_factory

    def ssh_args(self,pod):
        from studio_runpod import ipaddress
        details=pod.get('ssh',{}).get('direct') or {}
        address=details.get('host');port=details.get('port');user=details.get('username')
        try:ipaddress.ip_address(address)
        except (ValueError,TypeError):raise ManagedGPUError('No verified direct worker SSH address.') from None
        if type(port) is not int or not 1<=port<=65535 or not re.fullmatch(r'[a-z_][a-z0-9_-]*',user or ''):
            raise ManagedGPUError('Invalid owned worker SSH connection.')
        # Pin the host key for this exact owned allocation. A freshly rented
        # container may reuse a former container's public IP/port but has its
        # own SSH identity. Never waive a changed key within one allocation.
        allocation=hashlib.sha256(str(pod['id']).encode()).hexdigest()[:20]
        known=self.state_path.with_name('.render-hosts-'+allocation)
        return ['ssh','-i',self.config['runpodSSHKeyFile'],'-o','BatchMode=yes','-o','ConnectTimeout=12',
                '-o','StrictHostKeyChecking=accept-new','-o','UserKnownHostsFile='+str(known),'-p',str(port),user+'@'+address]

    def connect(self,pod):
        self.ssh=self.ssh_args(pod)
        from studio_gpu_providers import GPUConnectNotReady
        try:
            result=subprocess.run([*self.ssh,'true'],capture_output=True,timeout=20,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except subprocess.TimeoutExpired:
            raise GPUConnectNotReady('Owned renderer SSH is still starting; no media was transferred.') from None
        if result.returncode:
            detail=result.stderr.decode(errors='replace').lower()
            if any(x in detail for x in ('connection refused','connection timed out','connection reset','connection closed','no route to host')):
                raise GPUConnectNotReady('Owned renderer SSH is still starting; no media was transferred.')
            raise ManagedGPUError('Owned renderer SSH authentication or host verification failed; no media was transferred.')

    def bundle(self):
        output=io.BytesIO();folder=Path(__file__).resolve().parent
        with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
            for name in SOURCES:
                info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,(folder/name).read_bytes())
        return output.getvalue()

    def prepare(self,pod):
        began=time.monotonic();raw=self.bundle();self.zip_bytes=raw
        # No image weights or new Torch runtime. Reuse the official image's
        # one existing CUDA environment, with its assigned device mapping.
        bootstrap=r'''import sys,base64,pathlib,zipfile,io,os,subprocess,json,time,shutil
raw=base64.b64decode(sys.stdin.read());root=pathlib.Path('/workspace/studio-render-code-CODEHASH');root.mkdir(exist_ok=True)
with zipfile.ZipFile(io.BytesIO(raw)) as archive:
 for info in archive.infolist():
  if '/' in info.filename or not info.filename.endswith('.py'):raise RuntimeError('Invalid code member')
 archive.extractall(root)
comfy=pathlib.Path('/workspace/runpod-slim/ComfyUI')
envs=[comfy/name/'bin/python' for name in ('.venv-cu128','.venv-cu130','.venv') if (comfy/name/'bin/python').is_file()]
envs += [pathlib.Path('/opt/conda/bin/python'),pathlib.Path(sys.executable)]
envs=list(dict.fromkeys(str(p) for p in envs if pathlib.Path(p).is_file()))
if not envs:raise RuntimeError('Existing GPU runtime unavailable; no Torch installed')
environment=os.environ.copy();source=dict(p.split('=',1) for p in pathlib.Path('/proc/1/environ').read_bytes().decode().split(chr(0)) if '=' in p)
for key in ('CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','NVIDIA_VISIBLE_DEVICES','NVIDIA_DRIVER_CAPABILITIES','LD_LIBRARY_PATH','PYTHONPATH','RUNPOD_POD_ID'):
 if key in source:environment[key]=source[key]
environment['PYTHONDONTWRITEBYTECODE']='1';environment['STUDIO_GPU_CLOUD']='1'
python=None
for candidate in envs:
 existing=subprocess.run([candidate,'-c','import torch'],capture_output=True,timeout=25,env=environment)
 if existing.returncode==0:python=candidate;break
if not python:raise RuntimeError('Existing template has no Torch runtime; no additional Torch installed')
environment['PATH']=str(pathlib.Path(python).parent)+':'+environment.get('PATH','')
probe="import torch,json;assert torch.cuda.device_count()==1;assert torch.cuda.get_device_name(0)=='NVIDIA GeForce RTX 5090';x=torch.ones(8,device='cuda');assert float((x+1).sum().item())==16;print(json.dumps({'gpu':torch.cuda.get_device_name(0),'cuda':torch.version.cuda,'vramBytes':torch.cuda.get_device_properties(0).total_memory}))"
probe_until=time.monotonic()+45
while True:
 test=subprocess.run([python,'-c',probe],capture_output=True,text=True,timeout=25,env=environment)
 if not test.returncode:break
 if 'CUDA unknown error' not in test.stderr or time.monotonic()>=probe_until:
  driver=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
  raise RuntimeError('CUDA probe failed on '+driver.stdout.strip()+': '+test.stderr[-500:])
 time.sleep(3)
if not pathlib.Path('/usr/bin/ffmpeg').is_file():
 result=subprocess.run(['bash','-lc','apt-get update -qq && apt-get install -y -qq ffmpeg fonts-dejavu-core'],capture_output=True,timeout=120)
 if result.returncode:raise RuntimeError('Official FFmpeg setup failed')
check=subprocess.run([python,'-c','import PIL,numpy,PyNvVideoCodec'],capture_output=True,env=environment)
if check.returncode:
 # Only the small matching codec wheel. Dependencies already belong to the
 # image-managed runtime; --no-deps forbids duplicate Torch/CUDA downloads.
 result=subprocess.run([python,'-m','pip','install','--no-deps','--only-binary=:all:','PyNvVideoCodec==2.2.3'],capture_output=True,env=environment,timeout=120)
 if result.returncode:
  result=subprocess.run(['uv','pip','install','--python',python,'--no-deps','--only-binary=:all:','PyNvVideoCodec==2.2.3'],capture_output=True,env=environment,timeout=120)
 if result.returncode:raise RuntimeError('Small official NVIDIA codec wheel could not install; no runtime replaced')
smoke="import sys,json,torch;sys.path.insert(0,"+repr(str(root))+");from studio_gpu_codec import render_frames;x=torch.zeros((720,1280,3),device='cuda',dtype=torch.uint8);r=render_frames(x,"+repr(str(root/'encoder-smoke.mp4'))+",'static',frames=1,fps=30);print(json.dumps({'frames':r['frames'],'encoder':r['encoder']}))"
encoded=subprocess.run([python,'-c',smoke],capture_output=True,text=True,timeout=45,env=environment)
if encoded.returncode:
 detail=encoded.stderr[-800:]
 raise RuntimeError('Real NVENC startup failed before source transfer. Check host video support and NVIDIA_DRIVER_CAPABILITIES=compute,video,utility. '+detail)
smoke_result=json.loads(encoded.stdout.strip().splitlines()[-1])
if smoke_result.get('frames')!=1:raise RuntimeError('NVENC did not encode the smoke-test frame')
(root/'launch.json').write_text(json.dumps({'python':python,'environment':{k:v for k,v in environment.items() if k in ('CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','NVIDIA_VISIBLE_DEVICES','NVIDIA_DRIVER_CAPABILITIES','LD_LIBRARY_PATH','PYTHONPATH','RUNPOD_POD_ID','VIRTUAL_ENV','PATH','PYTHONDONTWRITEBYTECODE','STUDIO_GPU_CLOUD')}}))
print(json.dumps({'ready':True,'code':str(root),'runtime':json.loads(test.stdout.strip().splitlines()[-1]),'encoderSmoke':smoke_result}))
'''.replace('CODEHASH',hashlib.sha256(raw).hexdigest()[:20])
        encoded=base64.b64encode(bootstrap.encode()).decode()
        command="python3 -c \"import base64;exec(base64.b64decode('"+encoded+"'))\""
        result=subprocess.run([*self.ssh,command],input=base64.b64encode(raw),capture_output=True,timeout=290,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:
            detail=bootstrap_diagnostic(result.stderr.decode(errors='replace'))[-1200:]
            if 'Real NVENC startup failed before source transfer' in detail and 'returned error 2' in detail:
                from studio_gpu_providers import GPUEncoderUnavailable
                raise GPUEncoderUnavailable('The owned host could not open its NVIDIA video encoder; no source media was transferred.')
            raise ManagedGPUError('Cloud renderer setup failed: '+detail)
        record=json.loads(result.stdout.decode().strip().splitlines()[-1])
        self.code=record['code'];self.ready=record['ready'] is True
        self.report.update(runtime=record['runtime'],encoderSmoke=record['encoderSmoke'],prepareSeconds=time.monotonic()-began,codeSHA256=hashlib.sha256(raw).hexdigest())

    def healthCheck(self,**_):return {'installed':self.ready}
    def invalidate_backend_cache(self):pass

    def capabilities(self,project):
        manifest,etag=self.archive.manifest(project['id'],True)
        if not manifest or manifest['project']['revision']!=project['revision']:
            raise ValueError('Save this exact project revision and its assets to Google before cloud rendering.')
        assets=[]
        for name in render_assets(project):
            local=self.store.asset_local(project['id'],name)
            record=self.archive.asset_record(project['id'],name)
            if record is None:raise ValueError('A rendering input is not verified in Google storage.')
            if local.is_file():
                checksum=hashlib.sha256()
                with local.open('rb') as source:
                    for block in iter(lambda:source.read(4*1024*1024),b''):checksum.update(block)
                if local.stat().st_size!=record['bytes'] or checksum.hexdigest()!=record['sha256']:
                    raise ValueError('A local input differs from its saved Google copy. Save it before cloud rendering.')
            assets.append({k:record[k] for k in ('sha256','bytes')} | {'path':name})
        for alias,record in manifest.get('renderCache',{}).items():
            self.store.asset_local(project['id'],alias)
            if not alias.endswith('.mp4') or alias in {r['path'] for r in assets}:continue
            if not isinstance(record,dict) or not record.get('path','').startswith('cloud-renders/'):continue
            assets.append({k:record[k] for k in ('sha256','bytes')} | {'path':alias,
                'objectPath':record['path'],'optionalCache':True})
        return assets,etag

    def upload_capability(self,pid,event):
        path=event.get('path');checksum=event.get('sha256');size=event.get('bytes')
        if not isinstance(path,str) or not path.endswith('.mp4') or not re.fullmatch(r'[a-f0-9]{64}',checksum or '') or type(size) is not int or size<=0:
            raise ValueError('Invalid cloud render output identity.')
        self.store.asset_local(pid,path)
        public_path=self.upload_prefix+'/'+path
        self.store.asset_local(pid,public_path)
        key=f'studio/assets/{pid}/{checksum}/{public_path}'
        blob=self.archive.objects.bucket.get_blob(key,timeout=30)
        record={'key':key,'sha256':checksum,'bytes':size,'path':public_path}
        self.uploaded[path]=record
        if blob is not None:
            if blob.size!=size or (blob.metadata or {}).get('sha256')!=checksum:raise ValueError('Existing cloud output identity mismatch.')
            return {'path':path,'sha256':checksum,'exists':True}
        headers={'Content-Type':mimetypes.guess_type(path)[0] or 'video/mp4',
                 'x-goog-if-generation-match':'0','x-goog-meta-sha256':checksum}
        url=self.archive.objects.bucket.blob(key).generate_signed_url(version='v4',
            expiration=datetime.timedelta(seconds=min(604800,max(1800,getattr(self,'execution_deadline',time.time()+1800)-time.time()+60))),method='PUT',content_type=headers['Content-Type'],
            headers={k:v for k,v in headers.items() if k!='Content-Type'})
        return {'path':path,'sha256':checksum,'url':url,'headers':headers}

    def source_capability(self,event):
        path=event.get('path')
        record=getattr(self,'source_records',{}).get(path)
        if record is None:raise ValueError('Worker requested an input outside this saved export.')
        current=self.archive.asset_record(self.source_project,record.get('objectPath',path))
        if not current or any(current.get(k)!=record.get(k) for k in ('bytes','sha256')):
            raise ValueError('Saved cloud input changed during rendering; current edits retained.')
        return {k:record[k] for k in ('path','bytes','sha256')} | {'url':self.archive.objects.url(current['key'])}

    def run(self,project,identity,budget_usd,owned_startup=None,prepared_sources=None):
        if not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('Invalid cloud render identity.')
        if type(budget_usd) not in (int,float) or not math.isfinite(budget_usd) or not 0<budget_usd<=100:raise ValueError('Set a finite render-only spending cap.')
        if project['settings']['video']['width']!=1280 or project['settings']['video']['height']!=720 or project['settings']['video']['fps']!=30:
            raise ValueError('This native cloud renderer has only been prepared for 720p30; no silent format substitution.')
        assets,self.source_etag=prepared_sources or self.capabilities(project)
        from studio_cloud_render_plan import render_plan
        plan=render_plan(project,sum(r['bytes'] for r in assets if not r.get('optionalCache')),self.config)
        configured_disk=self.config.get('gpuWorkingDiskGB',25)
        if configured_disk<plan['recommendedDiskGB']:
            raise ValueError(f"Cloud export needs about {plan['recommendedDiskGB']} GB working disk under the conservative plan; configured {configured_disk} GB. Change the working disk before renting.")
        self.report['resourcePlan']=plan
        self.source_records={r['path']:r for r in assets};self.source_project=project['id']
        self.upload_prefix='cloud-renders/'+identity
        # Rendering downloads no diffusion weights. The image lane's high
        # model-download threshold must not exclude adequate render workers.
        config=self.config | {'runpodAvailabilityWaitSeconds':90,'runpodCheckRegionalStock':False,
            'gpuMinDownloadMbps':self.config.get('gpuRenderMinDownloadMbps',50)}
        owner=project['id']+'-render-'+identity
        lease=self.lease_factory(config,self.state_path,owner,{'gpuBudgetUSD':budget_usd},self,self.gate,
            report=lambda value:self.progress('rental',value),connector=self.connect,owned_startup=owned_startup)
        lease.runner=self
        began=time.monotonic();completed=None
        try:
            lease.ensure();self.report['startupSeconds']=time.monotonic()-began
            lease.save(renderOnly=True,sourceProject=project['id'],renderJob=identity)
            self.execution_deadline=min(lease.deadline,time.time()+plan['suggestedExecutionSeconds'])
            spec={'id':identity,'project':project,'assets':assets,'deadline':self.execution_deadline,
                  'scratchEstimateBytes':plan['scratchEstimateBytes']}
            launch="import pathlib,json,os;root=pathlib.Path('"+self.code+"');p=json.loads((root/'launch.json').read_text());env=os.environ.copy();env.update(p['environment']);os.execve(p['python'],[p['python'],str(root/'studio_remote_render_worker.py')],env)"
            encoded=base64.b64encode(launch.encode()).decode()
            command="python3 -c \"import base64;exec(base64.b64decode('"+encoded+"'))\""
            errors=self.state_path.with_suffix('.private-worker-error.log')
            with errors.open('wb') as stderr:
                self.process=subprocess.Popen([*self.ssh,command],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                    stderr=stderr,text=True,bufsize=1,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.process.stdin.write(control_packet(spec));self.process.stdin.flush()
                events=[];failure=[];finished=threading.Event()
                def receive():
                    try:
                        for line in self.process.stdout:
                            if len(line)>1024*1024:raise ValueError('Invalid render telemetry size.')
                            event=json.loads(line)
                            events.append(event)
                    except BaseException as error:failure.append(error)
                    finally:finished.set()
                reader=threading.Thread(target=receive,daemon=True);reader.start()
                cursor=0
                while not finished.is_set() or cursor<len(events):
                    self.gate('Cloud GPU rendering saved footage');lease.check()
                    if time.time()>=spec['deadline']:raise RuntimeError('Cloud render spending or execution deadline reached.')
                    while cursor<len(events):
                        event=events[cursor];cursor+=1;kind=event.get('event')
                        if kind=='source':
                            capability=self.source_capability(event)
                            self.process.stdin.write(json.dumps(capability)+'\n');self.process.stdin.flush()
                        elif kind=='asset':
                            capability=self.upload_capability(project['id'],event)
                            self.process.stdin.write(json.dumps(capability)+'\n');self.process.stdin.flush()
                        elif kind=='uploaded':
                            record=self.uploaded[event['path']]
                            blob=self.archive.objects.bucket.get_blob(record['key'],timeout=30)
                            if blob is None or blob.size!=record['bytes'] or (blob.metadata or {}).get('sha256')!=record['sha256']:
                                raise ValueError('Cloud render upload verification failed.')
                            self.progress('assetSaved',{'originalPath':event['path'],**record})
                        elif kind=='progress':self.progress('progress',event)
                        elif kind=='failed':raise RuntimeError('Cloud rendering failed: '+bootstrap_diagnostic(event.get('detail','')))
                        elif kind=='complete':completed=event
                        else:raise ValueError('Unknown cloud render event.')
                    finished.wait(.05)
                if failure:raise failure[0]
                if self.process.wait(timeout=10) or completed is None:raise RuntimeError('Cloud renderer did not confirm completion.')
            self.report.update(completed)
        finally:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:self.process.kill()
            lease.stop('render-only cloud job finished or interrupted')
            self.report['rental']=(copy.deepcopy(lease.state) if lease.state.get('project')==owner else
                {'status':'STOPPED','noAllocation':True,'estimatedUSD':0,'providerAttempts':copy.deepcopy(getattr(lease,'attempts',[]))})
        record=self.uploaded[completed['result']['path']]
        # Stream verification uses bounded memory, not a saved laptop MP4.
        verify_started=time.monotonic()
        self.archive.objects.verify_object(record['key'],record['sha256'],record['bytes'])
        self.report['cloudHashVerificationSeconds']=time.monotonic()-verify_started
        self.report['files']=copy.deepcopy(self.uploaded)
        self.report['result']=cloud_result(completed['result'],self.uploaded,lease.state.get('provider','runpod'),project['revision'])
        return self.report
