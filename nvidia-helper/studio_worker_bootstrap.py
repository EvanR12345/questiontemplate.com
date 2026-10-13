"""Fixed public Klein weights for explicitly prepared ephemeral workers."""
import base64,json

MODELS = [
    {'url':'https://huggingface.co/black-forest-labs/FLUX.2-klein-4b-fp8/resolve/5b4408e59397a4a37ccb46afe426d8ed86379441/flux-2-klein-4b-fp8.safetensors',
     'destination':'diffusion_models/flux-2-klein-4b-fp8.safetensors','bytes':4070624520,
     'sha256':'97ed34fe0567e436200f2faee3939b88f2b5d99f8af2a4dc16532c4245c0ccb6'},
    {'url':'https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-4b/resolve/5f526678002e43af5551dadb73ce2e8c91b43afe/split_files/text_encoders/qwen_3_4b.safetensors',
     'destination':'text_encoders/qwen_3_4b.safetensors','bytes':8044982048,
     'sha256':'6c671498573ac2f7a5501502ccce8d2b08ea6ca2f661c458e708f36b36edfc5a'},
    {'url':'https://huggingface.co/Comfy-Org/flux2-dev/resolve/ed33133cd56476eac818c0943b6f9419b3e4a3a1/split_files/vae/flux2-vae.safetensors',
     'destination':'vae/flux2-vae.safetensors','bytes':336213556,
     'sha256':'d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5'},
]

def remote_script(copies=1):
    """No configurable URLs, project data, credentials or shell interpolation."""
    if type(copies) is not int or copies not in (1,2):raise ValueError('Only one or two model copies are supported.')
    payload=base64.b64encode(json.dumps(MODELS).encode()).decode()
    script='''import base64,concurrent.futures,hashlib,json,os,pathlib,urllib.request
models=json.loads(base64.b64decode("PAYLOAD"))
root=pathlib.Path('/workspace/runpod-slim/ComfyUI/models')
def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
 return h.hexdigest()
def one(m):
 p=root/m['destination'];p.parent.mkdir(parents=True,exist_ok=True)
 if p.exists() and p.stat().st_size==m['bytes'] and digest(p)==m['sha256']:return
 # Reuse a verified mounted model copy when available. Both processes use
 # this same models tree; neither downloads a second Torch/CUDA environment.
 for cache in (pathlib.Path('/runpod-volume/models'),pathlib.Path('/workspace/models')):
  candidate=cache/m['destination']
  if candidate.is_file() and candidate.stat().st_size==m['bytes'] and digest(candidate)==m['sha256']:
   link=p.with_name(p.name+'.cache-link-'+str(os.getpid()))
   if link.is_symlink():link.unlink()
   link.symlink_to(candidate);os.replace(link,p);return
 tmp=p.with_suffix('.partial');h=hashlib.sha256();n=0
 with urllib.request.urlopen(m['url'],timeout=45) as r,tmp.open('wb') as f:
  for b in iter(lambda:r.read(8*1024*1024),b''):
   f.write(b);h.update(b);n+=len(b)
 if n!=m['bytes'] or h.hexdigest()!=m['sha256']:raise RuntimeError('Model fingerprint mismatch')
 os.replace(tmp,p)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(one,models))
print('MODELS_VERIFIED')
'''.replace('PAYLOAD',payload)
    return cuda_smoke_script(copies) + script + (copy_script() if copies==2 else '')


def cuda_smoke_script(copies=1):
    return '''import pathlib,subprocess,json,time,os
def image_gpu_environment():
 environment=os.environ.copy()
 # SSH login shells need not retain the container's CUDA device/library
 # mapping. Use the already-assigned image environment, never new devices.
 source=dict(entry.split('=',1) for entry in pathlib.Path('/proc/1/environ').read_bytes().decode().split('\\0') if '=' in entry)
 for key in ('CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','NVIDIA_VISIBLE_DEVICES','NVIDIA_DRIVER_CAPABILITIES','LD_LIBRARY_PATH','PYTHONPATH'):
  if key in source:environment[key]=source[key]
 return environment
gpu_environment=image_gpu_environment()
comfy=pathlib.Path('/workspace/runpod-slim/ComfyUI')
until=time.monotonic()+45
while True:
 interpreters=[comfy/name/'bin/python' for name in ('.venv-cu130','.venv-cu128','.venv') if (comfy/name/'bin/python').is_file()]
 if (comfy/'main.py').is_file() and len(interpreters)==1:break
 if time.monotonic()>=until:raise RuntimeError('Official image-managed Comfy runtime is not ready; no model download started')
 time.sleep(.5)
probe="import torch,json; assert torch.cuda.device_count()==1, 'Expected one CUDA GPU'; x=torch.ones(8,device='cuda:0'); assert float((x+1).sum().item())==16; torch.cuda.synchronize(); print(json.dumps({'cuda':torch.version.cuda,'torch':torch.__version__,'gpu':torch.cuda.get_device_name(0)}))"
result=subprocess.run([str(interpreters[0]),'-c',probe],capture_output=True,text=True,timeout=20,env=gpu_environment)
if result.returncode:
 last=next((line.strip() for line in reversed(result.stderr.splitlines()) if line.strip()),'CUDA tensor smoke test failed')
 raise RuntimeError('CUDA smoke test failed before model download: '+last)
runtime=json.loads(result.stdout.strip().splitlines()[-1])
if REQUIRE_5090 and runtime.get('gpu')!='NVIDIA GeForce RTX 5090':raise RuntimeError('CUDA device differs from the required 5090')
print('CUDA_VERIFIED '+json.dumps(runtime))
'''.replace('REQUIRE_5090','True' if copies==2 else 'False')


def copy_script():
    """Fixed local-only process bootstrap inside the existing owned rental."""
    return '''
import subprocess,time,socket
root=pathlib.Path('/workspace/runpod-slim/ComfyUI').resolve()
def base_process():
 matches=[];sockets=set()
 for table in ('tcp','tcp6'):
  for line in (pathlib.Path('/proc/net')/table).read_text().splitlines()[1:]:
   row=line.split()
   if len(row)>9 and row[3]=='0A' and int(row[1].split(':')[-1],16)==8188:sockets.add(row[9])
 for process in pathlib.Path('/proc').glob('[0-9]*'):
  try:
   # uv/supervisor parents also mention main.py in argv. Only the Python
   # process owning the listening socket is the independently running model.
   if not pathlib.Path(os.readlink(process/'exe')).name.startswith('python'):continue
   owns=any(os.readlink(fd) in {'socket:['+inode+']' for inode in sockets} for fd in (process/'fd').iterdir())
   if not owns:continue
   args=[v for v in (process/'cmdline').read_bytes().decode().split('\\0') if v]
   main=next((v for v in args if v.endswith('main.py')),None)
   cwd=pathlib.Path(os.readlink(process/'cwd'))
   actual=(pathlib.Path(main) if pathlib.Path(main).is_absolute() else cwd/main).parent.resolve() if main else cwd.resolve()
   if actual==root:matches.append((int(process.name),args))
  except (OSError,UnicodeError):continue
 if len(matches)!=1:raise RuntimeError('Unique base Comfy process not verified')
 return matches[0]
def recover_owned_base():
 # Fresh official images can leave SSH available after their default Comfy
 # launcher exits. Use the existing image-managed env; never reinstall Torch.
 # An occupied port or custom runtime configuration is not safe to replace.
 for process in pathlib.Path('/proc').glob('[0-9]*'):
  try:
   if not pathlib.Path(os.readlink(process/'exe')).name.startswith('python'):continue
   args=[v for v in (process/'cmdline').read_bytes().decode().split('\\0') if v]
   main=next((v for v in args if v.endswith('main.py')),None)
   cwd=pathlib.Path(os.readlink(process/'cwd'))
   if main and (pathlib.Path(main) if pathlib.Path(main).is_absolute() else cwd/main).parent.resolve()==root:return False
  except (OSError,UnicodeError):continue
 probe=socket.socket()
 try:probe.bind(('127.0.0.1',8188))
 except OSError:return False
 finally:probe.close()
 configuration=root.parent/'comfyui_args.txt'
 if configuration.is_file() and any(line.strip() and not line.lstrip().startswith('#') for line in configuration.read_text().splitlines()):
  raise RuntimeError('Base Comfy stopped with custom arguments; matching launcher recovery required')
 if not (root/'main.py').is_file():raise RuntimeError('Official Comfy image has not installed main.py')
 envs=[root/name/'bin/python' for name in ('.venv-cu130','.venv-cu128','.venv') if (root/name/'bin/python').is_file()]
 if len(envs)!=1:raise RuntimeError('No unique existing image-managed Comfy interpreter is available')
 python=str(envs[0]);owned=root.parent/'studio-first-worker';owned.mkdir(exist_ok=True)
 environment=dict(gpu_environment);environment['VIRTUAL_ENV']=str(envs[0].parent.parent)
 environment['PATH']=str(envs[0].parent)+':'+environment.get('PATH','')
 command=[python,str(root/'main.py'),'--listen','127.0.0.1','--port','8188','--disable-auto-launch']
 for kind in ('input','output','temp','user'):
  path=owned/kind;path.mkdir(exist_ok=True);command.extend(['--'+kind+'-directory',str(path)])
 with (owned/'process.log').open('ab') as log:
  process=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,env=environment)
 (owned/'process.json').write_text(json.dumps({'pid':process.pid,'port':8188}))
 return True

base_deadline=time.monotonic()+60;recovered=False
while True:
 try:pid,args=base_process();break
 except RuntimeError:
  if not recovered:recovered=recover_owned_base()
  if time.monotonic()>=base_deadline:
   log=root.parent/'studio-first-worker/process.log'
   if log.is_file():
    with log.open('rb') as stream:
     stream.seek(max(0,log.stat().st_size-8192));tail=stream.read().decode(errors='replace')
    last=next((line.strip() for line in reversed(tail.splitlines()) if line.strip()),'No model process diagnostic.')
    raise RuntimeError('Owned base Comfy could not start: '+last)
   raise
  time.sleep(.5)
import shutil
source_environment=dict(entry.split('=',1) for entry in (pathlib.Path('/proc')/str(pid)/'environ').read_bytes().decode().split('\\0') if '=' in entry)
runtime_environment=os.environ.copy()
for key in ('PATH','VIRTUAL_ENV','PYTHONPATH','LD_LIBRARY_PATH','CUDA_VISIBLE_DEVICES','CUDA_DEVICE_ORDER','NVIDIA_VISIBLE_DEVICES','NVIDIA_DRIVER_CAPABILITIES','HF_HOME','XDG_CACHE_HOME'):
 if key in source_environment:runtime_environment[key]=source_environment[key]
# Use the interpreter actually invoked by the verified base process. A stale
# VIRTUAL_ENV variable must not replace an explicit working interpreter.
python=shutil.which(args[0],path=runtime_environment.get('PATH')) or args[0]
boolean={'--gpu-only','--highvram','--normalvram','--lowvram','--novram','--force-fp32','--force-fp16',
 '--fp32-unet','--fp64-unet','--bf16-unet','--fp16-unet','--fp8_e4m3fn-unet','--fp8_e5m2-unet',
 '--fp16-vae','--fp32-vae','--bf16-vae','--cpu-vae','--fp8_e4m3fn-text-enc','--fp8_e5m2-text-enc',
 '--fp16-text-enc','--fp32-text-enc','--force-channels-last','--use-split-cross-attention',
 '--use-quad-cross-attention','--use-pytorch-cross-attention','--use-sage-attention','--disable-xformers',
 '--disable-smart-memory','--disable-cuda-malloc','--deterministic','--cache-classic','--cache-none'}
flags=[v for v in args if v in boolean]
for i,arg in enumerate(args):
 if arg in ('--reserve-vram','--cache-lru','--cuda-device','--preview-method'):
  value=args[i+1]
  if arg=='--preview-method' and value not in ('none','auto','latent2rgb','taesd'):raise RuntimeError('Unknown preview strategy')
  if arg!='--preview-method' and not value.replace('.','',1).isdigit():raise RuntimeError('Invalid numeric runtime flag')
  flags.extend([arg,value])
 if arg=='--fast':
  values=[]
  for value in args[i+1:]:
   if value.startswith('--'):break
   if value not in ('fp16_accumulation','fp8_matrix_mult','cublas_ops','autotune'):raise RuntimeError('Unknown fast runtime flag')
   values.append(value)
  flags.extend([arg,*values])
gpu=subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],text=True,timeout=5).strip()
if gpu!='NVIDIA GeForce RTX 5090':raise RuntimeError('Two-copy default requires exactly one RTX 5090')
worker=root.parent/'studio-second-worker';worker.mkdir(exist_ok=True)
state=worker/'process.json'
second=None
if state.exists():
 saved=json.loads(state.read_text());command=pathlib.Path('/proc')/str(saved['pid'])/'cmdline'
 if command.exists() and str(worker).encode() in command.read_bytes():second=saved['pid']
if second is None:
 with urllib.request.urlopen('http://127.0.0.1:8188/system_stats',timeout=3) as response:json.load(response)
 # Fail before submitting if the second port belongs to another process.
 import socket
 sock=socket.socket();sock.bind(('127.0.0.1',8189));sock.close()
 help_text=subprocess.check_output([python,str(root/'main.py'),'--help'],text=True,timeout=15,env=runtime_environment)
 needed=['--input-directory','--output-directory','--temp-directory','--user-directory','--disable-auto-launch']
 if any(v not in help_text for v in needed):raise RuntimeError('Comfy lacks isolated worker directories')
 command=[python,str(root/'main.py'),'--listen','127.0.0.1','--port','8189','--disable-auto-launch',*flags]
 for kind in ('input','output','temp','user'):
  path=worker/kind;path.mkdir(exist_ok=True);command.extend(['--'+kind+'-directory',str(path)])
 with (worker/'process.log').open('ab') as log:
  process=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,env=runtime_environment)
 second=process.pid;state.write_text(json.dumps({'pid':second,'port':8189}))
deadline=time.monotonic()+60
while True:
 try:
  for port in (8188,8189):
   with urllib.request.urlopen('http://127.0.0.1:'+str(port)+'/system_stats',timeout=2) as response:
    stats=json.load(response)
   if not any('5090' in d.get('name','') for d in stats.get('devices',[])):raise RuntimeError('Comfy device mismatch')
  break
 except Exception:
  if time.monotonic()>=deadline:
   log=worker/'process.log';last='No worker diagnostic available.'
   if log.is_file():
    with log.open('rb') as stream:
     stream.seek(max(0,log.stat().st_size-8192));tail=stream.read().decode(errors='replace')
    last=next((line.strip() for line in reversed(tail.splitlines()) if line.strip()),last)
   raise RuntimeError('Second Comfy process did not become ready: '+last)
  time.sleep(.5)
if pid==second:raise RuntimeError('Model copies do not have independent processes')
print('WORKERS_VERIFIED '+json.dumps({'gpu':gpu,'workers':[{'index':0,'pid':pid,'port':8188},{'index':1,'pid':second,'port':8189}]}))
'''
