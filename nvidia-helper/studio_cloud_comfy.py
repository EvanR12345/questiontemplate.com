"""Private two-process Klein service on an explicitly owned marketplace GPU.

Reuses the template's Torch environment. Code and weights are pinned, shared by
both model processes, and never downloaded to the laptop by this adapter.
"""
import json,subprocess,time
from pathlib import Path
from urllib.parse import urlparse
from studio_cloud_pod_render import CloudPodRenderer
from studio_gpu_providers import GPUProviderError
from studio_image_workers import model_copies,worker_endpoints
from studio_runpod import bootstrap_diagnostic
from studio_worker_bootstrap import remote_script

COMFY_COMMIT='b1693ecba9f5b65f8c80ab36b195ab963ec92413'


def install_script():
    # Official immutable v0.30.0, matching the prepared Runpod worker version.
    # Constraints explicitly forbid replacing the already working Torch stack.
    return r'''import io,json,os,pathlib,subprocess,sys,tarfile,time,urllib.request,shutil
root=pathlib.Path('/workspace/runpod-slim/ComfyUI');root.parent.mkdir(parents=True,exist_ok=True)
environment=os.environ.copy()
source=dict(p.split('=',1) for p in pathlib.Path('/proc/1/environ').read_bytes().decode().split(chr(0)) if '=' in p)
for key in ('CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','NVIDIA_VISIBLE_DEVICES','NVIDIA_DRIVER_CAPABILITIES','LD_LIBRARY_PATH','PYTHONPATH'):
 if key in source:environment[key]=source[key]
candidates=list(dict.fromkeys(['/opt/conda/bin/python','/usr/local/bin/python','/usr/local/bin/python3',sys.executable]))
python=None
probe="import torch,json,importlib.metadata as m,sys;assert torch.cuda.device_count()==1;assert torch.cuda.get_device_name(0)=='NVIDIA GeForce RTX 5090';installed={d.metadata['Name'].lower():d.version for d in m.distributions()};print(json.dumps({'python':sys.executable,'prefix':sys.prefix,'versions':{p:installed[p] for p in ('torch','torchvision','torchaudio') if p in installed}}))"
diagnostics=[]
for candidate in candidates:
 if not pathlib.Path(candidate).is_file():continue
 until=time.monotonic()+45
 while True:
  test=subprocess.run([candidate,'-c',probe],capture_output=True,text=True,env=environment,timeout=30)
  if test.returncode==0 or 'CUDA unknown error' not in test.stderr or time.monotonic()>=until:break
  time.sleep(3)
 if test.returncode==0:python=candidate;runtime=json.loads(test.stdout.strip().splitlines()[-1]);break
 diagnostics.append(candidate+': '+test.stderr[-400:])
if not python:raise RuntimeError('Template has no compatible existing RTX 5090 Torch runtime; no replacement installed. '+str(diagnostics))
if not shutil.which('cc'):
 for command in (['apt-get','update','-qq'],['apt-get','install','-y','-qq','build-essential']):
  result=subprocess.run(command,capture_output=True,env=environment,timeout=90)
  if result.returncode:raise RuntimeError('Official system compiler setup failed; no image submitted')
commit='COMMIT'
marker=root/'.studio-source-commit'
if root.exists() and (not marker.is_file() or marker.read_text()!=commit):
 raise RuntimeError('Existing unmanaged Comfy source will not be overwritten')
if not root.exists():
 url='https://codeload.github.com/Comfy-Org/ComfyUI/tar.gz/'+commit
 with urllib.request.urlopen(url,timeout=30) as response:raw=response.read(100*1024*1024+1)
 if len(raw)>100*1024*1024:raise RuntimeError('Official source archive is too large')
 root.mkdir()
 with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
  for member in archive.getmembers():
   parts=pathlib.PurePosixPath(member.name).parts[1:]
   if not parts:continue
   if '..' in parts or member.issym() or member.islnk() or not (member.isfile() or member.isdir()):raise RuntimeError('Unsafe source archive member')
   target=root.joinpath(*parts)
   if member.isdir():target.mkdir(parents=True,exist_ok=True)
   else:
    target.parent.mkdir(parents=True,exist_ok=True)
    with archive.extractfile(member) as source,target.open('wb') as dest:
     for block in iter(lambda:source.read(1024*1024),b''):dest.write(block)
 marker.write_text(commit)
constraints=root.parent/'studio-torch-constraints.txt'
constraints.write_text('\n'.join(p+'=='+v for p,v in runtime['versions'].items())+'\n')
ready=root/'.studio-dependencies-ready'
if not ready.is_file():
 command=[python,'-m','pip','install','--disable-pip-version-check','-c',str(constraints),'-r',str(root/'requirements.txt')]
 result=subprocess.run(command,capture_output=True,env=environment,timeout=180)
 if result.returncode:
  result=subprocess.run(['uv','pip','install','--python',python,'-c',str(constraints),'-r',str(root/'requirements.txt')],capture_output=True,env=environment,timeout=180)
 if result.returncode:raise RuntimeError('Pinned Comfy dependencies failed: '+result.stderr.decode(errors='replace')[-1200:])
 ready.write_text(commit)
support=subprocess.run([python,'-c','import torch,torchvision,torchaudio'],capture_output=True,env=environment,timeout=30)
if support.returncode:
 # PyPI support wheels may target another CUDA line than the template's
 # existing Torch. Repair only the two small official matching wheels.
 torch_version=runtime['versions']['torch'];base=torch_version.split('+')[0]
 vision={'2.7.1':'0.22.1','2.11.0':'0.26.0'}.get(base)
 if not vision or '+cu128' not in torch_version:raise RuntimeError('Matching existing Torch support wheels require an explicit supported version; no Torch replaced')
 command=[python,'-m','pip','install','--disable-pip-version-check','--no-deps','--only-binary=:all:',
          '--index-url','https://download.pytorch.org/whl/cu128','torchaudio=='+base+'+cu128','torchvision=='+vision+'+cu128']
 fixed=subprocess.run(command,capture_output=True,env=environment,timeout=90)
 if fixed.returncode:raise RuntimeError('Official matching CUDA support wheel setup failed; Torch retained')
 support=subprocess.run([python,'-c','import torch,torchvision,torchaudio'],capture_output=True,env=environment,timeout=30)
 if support.returncode:raise RuntimeError('Matching Torch support libraries still cannot load; no image dispatched')
 runtime['versions'].update({'torchaudio':base+'+cu128','torchvision':vision+'+cu128'})
test=subprocess.run([python,'-c',probe],capture_output=True,text=True,env=environment,timeout=30)
if test.returncode or any(json.loads(test.stdout.strip().splitlines()[-1])['versions'].get(p)!=v for p,v in runtime['versions'].items()):
 raise RuntimeError('Existing Torch stack changed unexpectedly; no image dispatched')
link=root/'.venv'
if not link.exists():link.symlink_to(runtime['prefix'],target_is_directory=True)
if not (link/'bin/python').is_file():raise RuntimeError('Existing runtime cannot be shared with Comfy')
hardware=subprocess.run(['nvidia-smi','--query-gpu=name,driver_version,memory.total,power.limit,pcie.link.gen.current,pcie.link.width.current','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
print('COMFY_SOURCE_VERIFIED '+json.dumps({'commit':commit,'torchStack':runtime['versions'],'gpuHardware':hardware.stdout.strip(),'cpuLogicalCount':os.cpu_count()}))
'''.replace('COMMIT',COMFY_COMMIT)


class CloudComfyRunner:
    def __init__(self,config,state_path,provider,gate):
        if model_copies(config)!=2:raise GPUProviderError('The marketplace image service requires two verified model copies.')
        self.config=config;self.path=Path(state_path);self.provider=provider;self.gate=gate
        self.tunnel=None;self.process=None;self.ssh=None;self.report={}
        # The renderer's connection probe already pins one host identity per
        # allocation and validates the direct IP/port before any source transfer.
        self.connection=CloudPodRenderer(None,None,config,gate,state_path)

    def connect(self,pod):
        self.connection.connect(pod);self.ssh=self.connection.ssh

    def prepare(self,pod):
        self.gate('Preparing pinned Klein image service on '+pod['provider'])
        began=time.monotonic();bootstrap=remote_script(2)
        if self.config.get('comfyNativeFP8Matmul') is True:
            # Only the existing FP8 checkpoint uses native FP8 matrix multiply;
            # no new quantizer, model, step count or prompt is substituted.
            original="'--port','8188','--disable-auto-launch']"
            if original not in bootstrap:raise GPUProviderError('Pinned worker launcher no longer matches the tested runtime override.')
            bootstrap=bootstrap.replace(original,"'--port','8188','--disable-auto-launch','--fast','fp8_matrix_mult']",1)
        script=install_script()+bootstrap
        self.process=subprocess.Popen([*self.ssh,'python3 -'],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
            text=True,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        first=True
        try:
            while True:
                self.gate('Preparing two cloud image-model processes')
                if time.monotonic()-began>420:raise GPUProviderError('Image service setup exceeded its bounded window.')
                try:output,errors=self.process.communicate(input=script if first else None,timeout=1);break
                except subprocess.TimeoutExpired:first=False
            if self.process.returncode or 'MODELS_VERIFIED' not in output:
                raise GPUProviderError('Cloud image service setup failed: '+bootstrap_diagnostic(errors)[-1000:])
            workers=next((json.loads(line.removeprefix('WORKERS_VERIFIED ')) for line in output.splitlines() if line.startswith('WORKERS_VERIFIED ')),None)
            source=next((json.loads(line.removeprefix('COMFY_SOURCE_VERIFIED ')) for line in output.splitlines() if line.startswith('COMFY_SOURCE_VERIFIED ')),None)
            if workers is None or source is None:raise GPUProviderError('Independent image processes and pinned source were not verified.')
            reset=getattr(self.provider,'begin_worker_lifetime',None)
            if callable(reset):reset()
            self.provider.confirm_workers(workers)
            forwards=[]
            for index,url in enumerate(worker_endpoints(self.config)):
                forwards.extend(['-L',f'127.0.0.1:{urlparse(url).port}:127.0.0.1:{8188+index}'])
            self.tunnel=subprocess.Popen([*self.ssh[:-1],'-o','ExitOnForwardFailure=yes','-o','ServerAliveInterval=15','-N',*forwards,self.ssh[-1]],
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            until=time.monotonic()+30
            while True:
                self.gate('Verifying private image-worker tunnel')
                if self.tunnel.poll() is not None:raise GPUProviderError('Private image tunnel could not bind; no image dispatched.')
                try:
                    health=self.provider.healthCheck(model='flux2-klein-4b',workflow='flux2-klein-4b-auto')
                    if health.get('installed'):break
                except Exception:pass
                if time.monotonic()>=until:raise GPUProviderError('Cloud image-worker health check did not pass.')
                time.sleep(.5)
            source['nativeFP8Matmul']=self.config.get('comfyNativeFP8Matmul') is True
            self.report={'prepareSeconds':time.monotonic()-began,'workers':workers,'source':source}
        finally:
            if self.process.poll() is None:self.process.kill();self.process.communicate()

    def close(self):
        if self.tunnel and self.tunnel.poll() is None:
            self.tunnel.terminate()
            try:self.tunnel.wait(timeout=5)
            except subprocess.TimeoutExpired:self.tunnel.kill();self.tunnel.wait()
