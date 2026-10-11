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

def remote_script():
    """No configurable URLs, project data, credentials or shell interpolation."""
    payload=base64.b64encode(json.dumps(MODELS).encode()).decode()
    return '''import base64,concurrent.futures,hashlib,json,os,pathlib,urllib.request
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
 tmp=p.with_suffix('.partial');h=hashlib.sha256();n=0
 with urllib.request.urlopen(m['url'],timeout=45) as r,tmp.open('wb') as f:
  for b in iter(lambda:r.read(8*1024*1024),b''):
   f.write(b);h.update(b);n+=len(b)
 if n!=m['bytes'] or h.hexdigest()!=m['sha256']:raise RuntimeError('Model fingerprint mismatch')
 os.replace(tmp,p)
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:list(pool.map(one,models))
print('MODELS_VERIFIED')
'''.replace('PAYLOAD',payload)
