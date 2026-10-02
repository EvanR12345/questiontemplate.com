from __future__ import annotations
import base64, io, json, os, secrets, threading, time, traceback, webbrowser
from http import HTTPStatus
from urllib.parse import parse_qs, urlparse

import torch
from PIL import Image
from diffusers import StableDiffusionPipeline, StableDiffusionInpaintPipeline, DPMSolverMultistepScheduler

PORT = 8771
MODEL_ID = os.environ.get('QT_IMAGE_MODEL', 'stable-diffusion-v1-5/stable-diffusion-v1-5')
INPAINT_ID = os.environ.get('QT_INPAINT_MODEL', 'runwayml/stable-diffusion-inpainting')
KEY = secrets.token_urlsafe(32)
ORIGINS = {'https://questiontemplate.com', 'https://www.questiontemplate.com', 'http://localhost:8000', 'http://127.0.0.1:8000'}

class Engine:
    def __init__(self):
        if not torch.cuda.is_available():
            raise RuntimeError('CUDA is not available. Update the NVIDIA driver and make sure the CUDA PyTorch build installed correctly.')
        self.gpu = torch.cuda.get_device_name(0)
        self.low_vram = torch.cuda.get_device_properties(0).total_memory < 5 * 1024**3
        self.pipe = None
        self.inpaint = None
        self.ip_loaded = False
        self.lock = threading.Lock()

    def _configure(self, pipe):
        pipe.scheduler = DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
        pipe.enable_attention_slicing('max' if self.low_vram else 'auto')
        pipe.enable_vae_slicing()
        try: pipe.enable_vae_tiling()
        except Exception: pass
        if self.low_vram:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to('cuda')
        return pipe

    def load(self):
        if self.pipe is not None: return
        dtype = torch.float16
        self.pipe = StableDiffusionPipeline.from_pretrained(MODEL_ID, torch_dtype=dtype, safety_checker=None, requires_safety_checker=False)
        self._configure(self.pipe)

    def load_ip(self):
        self.load()
        if self.ip_loaded: return
        try:
            self.pipe.load_ip_adapter('h94/IP-Adapter', subfolder='models', weight_name='ip-adapter_sd15.bin')
            self.ip_loaded = True
        except Exception as error:
            raise RuntimeError('Character consistency model could not load. Generate without references or restart after freeing GPU/RAM. ' + str(error))

    def load_inpaint(self):
        if self.inpaint is not None: return
        self.inpaint = StableDiffusionInpaintPipeline.from_pretrained(INPAINT_ID, torch_dtype=torch.float16, safety_checker=None, requires_safety_checker=False)
        self._configure(self.inpaint)

    @staticmethod
    def decode_image(value: str) -> Image.Image:
        raw = value.split(',', 1)[-1]
        return Image.open(io.BytesIO(base64.b64decode(raw))).convert('RGB')

    @staticmethod
    def collage(values: list[str]) -> Image.Image | None:
        imgs = [Engine.decode_image(v) for v in values[:3] if v]
        if not imgs: return None
        size = 384
        thumbs=[]
        for image in imgs:
            image.thumbnail((size,size), Image.Resampling.LANCZOS)
            canvas=Image.new('RGB',(size,size),'white')
            canvas.paste(image,((size-image.width)//2,(size-image.height)//2))
            thumbs.append(canvas)
        out=Image.new('RGB',(size*len(thumbs),size),'white')
        for i,image in enumerate(thumbs): out.paste(image,(i*size,0))
        return out

    @staticmethod
    def encode(image: Image.Image) -> str:
        out=io.BytesIO(); image.save(out,'PNG',optimize=True)
        return 'data:image/png;base64,'+base64.b64encode(out.getvalue()).decode()

    def generate(self, data):
        with self.lock, torch.inference_mode():
            self.load()
            refs=data.get('reference_images') or []
            kwargs={
                'prompt': data.get('prompt',''), 'negative_prompt': data.get('negative',''),
                'width': clamp8(data.get('width',512),384,768), 'height': clamp8(data.get('height',512),384,768),
                'num_inference_steps': max(8,min(50,int(data.get('steps',26)))),
                'guidance_scale': max(1,min(14,float(data.get('guidance',7.5)))),
            }
            seed=int(data.get('seed') or secrets.randbelow(2**31-1)); kwargs['generator']=torch.Generator(device='cuda').manual_seed(seed)
            if refs:
                self.load_ip(); self.pipe.set_ip_adapter_scale(max(0,min(1,float(data.get('reference_strength',0.7)))))
                kwargs['ip_adapter_image']=self.collage(refs)
            started=time.perf_counter()
            image=self.pipe(**kwargs).images[0]
            if self.ip_loaded:
                try: self.pipe.set_ip_adapter_scale(0)
                except Exception: pass
            return {'image':self.encode(image),'seed':seed,'seconds':time.perf_counter()-started}

    def do_inpaint(self, data):
        with self.lock, torch.inference_mode():
            self.load_inpaint()
            image=self.decode_image(data['image']); mask=self.decode_image(data['mask']).convert('L')
            image=image.resize((clamp8(image.width,384,768),clamp8(image.height,384,768)),Image.Resampling.LANCZOS)
            mask=mask.resize(image.size,Image.Resampling.NEAREST)
            seed=int(data.get('seed') or secrets.randbelow(2**31-1)); started=time.perf_counter()
            result=self.inpaint(prompt=data.get('prompt',''), negative_prompt=data.get('negative',''), image=image, mask_image=mask,
                num_inference_steps=max(8,min(50,int(data.get('steps',28)))), guidance_scale=max(1,min(14,float(data.get('guidance',7.5)))),
                generator=torch.Generator(device='cuda').manual_seed(seed)).images[0]
            return {'image':self.encode(result),'seed':seed,'seconds':time.perf_counter()-started}

def clamp8(value,lo,hi):
    n=max(lo,min(hi,int(value))); return n-n%8

ENGINE=None

def response(handler, status=200, payload=None):
    raw=json.dumps(payload or {}).encode()
    handler.send_response(status)
    origin=handler.headers.get('Origin','')
    if origin in ORIGINS: handler.send_header('Access-Control-Allow-Origin',origin)
    handler.send_header('Access-Control-Allow-Private-Network','true')
    handler.send_header('Access-Control-Allow-Headers','Content-Type')
    handler.send_header('Access-Control-Allow-Methods','GET,POST,OPTIONS')
    handler.send_header('Cache-Control','no-store')
    handler.send_header('Content-Type','application/json')
    handler.send_header('Content-Length',str(len(raw)))
    handler.end_headers(); handler.wfile.write(raw)

def make_app():
    from http.server import BaseHTTPRequestHandler
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args): pass
        def do_OPTIONS(self): response(self,204,{})
        def authorized(self): return parse_qs(urlparse(self.path).query).get('key',[''])[0]==KEY
        def do_GET(self):
            if not self.authorized(): return response(self,401,{'error':'Invalid helper key. Launch the site from the helper window.'})
            if urlparse(self.path).path!='/health': return response(self,404,{'error':'Not found'})
            try:
                global ENGINE
                ENGINE=ENGINE or Engine()
                response(self,200,{'ok':True,'gpu':ENGINE.gpu,'model':MODEL_ID,'mode':'low-VRAM CUDA' if ENGINE.low_vram else 'CUDA'})
            except Exception as error: response(self,500,{'error':str(error)})
        def do_POST(self):
            if not self.authorized(): return response(self,401,{'error':'Invalid helper key.'})
            try:
                length=int(self.headers.get('Content-Length','0')); data=json.loads(self.rfile.read(length) or b'{}')
                global ENGINE; ENGINE=ENGINE or Engine(); path=urlparse(self.path).path
                if path=='/generate': result=ENGINE.generate(data)
                elif path=='/inpaint': result=ENGINE.do_inpaint(data)
                else: return response(self,404,{'error':'Not found'})
                response(self,200,result)
            except torch.cuda.OutOfMemoryError:
                torch.cuda.empty_cache(); response(self,507,{'error':'GPU memory ran out. Close GPU apps, use 512×512, or generate without character references.'})
            except Exception as error:
                traceback.print_exc(); response(self,500,{'error':str(error)})
    return Handler

def main():
    from http.server import ThreadingHTTPServer
    print('\nQuestionTemplate Image Helper')
    print('Loading uses your NVIDIA GPU. First generation downloads the model.\n')
    url=f'https://questiontemplate.com/studio.html?imageKey={KEY}'
    threading.Timer(1.2,lambda:webbrowser.open(url)).start()
    server=ThreadingHTTPServer(('127.0.0.1',PORT),make_app())
    print('Ready on http://127.0.0.1:%d' % PORT)
    print('Keep this window open while generating images.')
    try: server.serve_forever()
    except KeyboardInterrupt: pass

if __name__=='__main__': main()
