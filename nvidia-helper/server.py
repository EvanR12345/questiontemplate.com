"""QuestionTemplate local NVIDIA bridge: Audio + Studio. Bound to localhost only."""
import base64
import hmac
import io
import json
import os
import secrets
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

VOICES = {'am_michael', 'am_fenrir', 'am_puck', 'af_heart', 'af_bella', 'af_nicole', 'bm_george', 'bf_emma'}
PORT = 8765
MAX_BODY = 32 * 1024 * 1024
MODEL_ID = os.environ.get('QT_IMAGE_MODEL', 'stable-diffusion-v1-5/stable-diffusion-v1-5')
INPAINT_ID = os.environ.get('QT_INPAINT_MODEL', 'runwayml/stable-diffusion-inpainting')


class CudaEngine:
    def __init__(self):
        import torch
        from huggingface_hub import hf_hub_download
        from kokoro import KModel
        self.torch, self.download = torch, hf_hub_download
        if not torch.cuda.is_available():
            raise RuntimeError('NVIDIA CUDA is unavailable. Update the NVIDIA driver and run the helper again.')
        index = max(range(torch.cuda.device_count()), key=lambda i: torch.cuda.get_device_properties(i).total_memory)
        self.index = index
        self.device = f'cuda:{index}'
        torch.cuda.set_device(index)
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        torch.backends.cudnn.benchmark = False
        self.model = KModel(repo_id='hexgrad/Kokoro-82M').to(self.device).eval()
        self.voices = {}
        self.warm = set()
        self.gpu = torch.cuda.get_device_name(index)
        self.prepare('am_michael')

    def health(self):
        return {
            'protocol': 2, 'backend': 'cuda', 'gpu': self.gpu, 'sampleRate': 24000,
            'precision': 'float32', 'vocab': self.model.vocab,
            'capabilities': ['audio', 'image'], 'imageModel': MODEL_ID
        }

    def prepare(self, voice):
        if voice not in VOICES:
            raise ValueError('Choose a supported studio voice.')
        if voice not in self.voices:
            path = self.download(repo_id='hexgrad/Kokoro-82M', filename=f'voices/{voice}.pt')
            self.voices[voice] = self.torch.load(path, map_location='cpu', weights_only=True).to(self.device)
        if voice not in self.warm:
            self.render('həlˈoʊ.', voice, 1.0)
            self.warm.add(voice)

    def render(self, phonemes, voice, speed):
        torch = self.torch
        if not phonemes or any(char not in self.model.vocab for char in phonemes):
            raise ValueError('Speech sounds do not match the loaded model.')
        if len(phonemes) + 2 > min(280, self.model.context_length):
            raise ValueError('Section exceeds the model limit. No audio was truncated.')
        with torch.inference_mode():
            audio = self.model(phonemes, self.voices[voice][len(phonemes) - 1], speed)
        if not torch.isfinite(audio).all():
            raise RuntimeError('Model returned invalid audio.')
        return audio.float().cpu().numpy().astype('<f4', copy=False).tobytes()

    def synthesize(self, phonemes, voice, speed):
        self.prepare(voice)
        return self.render(phonemes, voice, speed)


class ImageEngine:
    def __init__(self, torch, device, index, gpu):
        self.torch, self.device, self.index, self.gpu = torch, device, index, gpu
        self.low_vram = torch.cuda.get_device_properties(index).total_memory < 5 * 1024**3
        self.pipe = None
        self.inpaint = None
        self.ip_loaded = False
        self.Image = None
        self.DPMSolverMultistepScheduler = None

    def health(self):
        return {
            'ok': True, 'backend': 'cuda', 'gpu': self.gpu, 'model': MODEL_ID,
            'mode': 'low-VRAM CUDA' if self.low_vram else 'CUDA', 'sharedHelper': True
        }

    def _imports(self):
        if self.Image is not None:
            return
        from PIL import Image
        from diffusers import StableDiffusionPipeline, StableDiffusionInpaintPipeline, DPMSolverMultistepScheduler
        self.Image = Image
        self.StableDiffusionPipeline = StableDiffusionPipeline
        self.StableDiffusionInpaintPipeline = StableDiffusionInpaintPipeline
        self.DPMSolverMultistepScheduler = DPMSolverMultistepScheduler

    def _configure(self, pipe):
        pipe.scheduler = self.DPMSolverMultistepScheduler.from_config(pipe.scheduler.config, use_karras_sigmas=True)
        pipe.enable_attention_slicing('max' if self.low_vram else 'auto')
        pipe.enable_vae_slicing()
        try:
            pipe.enable_vae_tiling()
        except Exception:
            pass
        if self.low_vram:
            pipe.enable_model_cpu_offload(gpu_id=self.index)
        else:
            pipe.to(self.device)
        return pipe

    def load(self):
        if self.pipe is not None:
            return
        self._imports()
        self.pipe = self.StableDiffusionPipeline.from_pretrained(
            MODEL_ID, torch_dtype=self.torch.float16, safety_checker=None, requires_safety_checker=False
        )
        self._configure(self.pipe)

    def load_ip(self):
        self.load()
        if self.ip_loaded:
            return
        self.pipe.load_ip_adapter('h94/IP-Adapter', subfolder='models', weight_name='ip-adapter_sd15.bin')
        self.ip_loaded = True

    def load_inpaint(self):
        if self.inpaint is not None:
            return
        self._imports()
        self.inpaint = self.StableDiffusionInpaintPipeline.from_pretrained(
            INPAINT_ID, torch_dtype=self.torch.float16, safety_checker=None, requires_safety_checker=False
        )
        self._configure(self.inpaint)

    def decode_image(self, value):
        self._imports()
        raw = value.split(',', 1)[-1]
        return self.Image.open(io.BytesIO(base64.b64decode(raw))).convert('RGB')

    def collage(self, values):
        imgs = [self.decode_image(v) for v in values[:3] if v]
        if not imgs:
            return None
        size = 384
        thumbs = []
        for image in imgs:
            image.thumbnail((size, size), self.Image.Resampling.LANCZOS)
            canvas = self.Image.new('RGB', (size, size), 'white')
            canvas.paste(image, ((size - image.width)//2, (size - image.height)//2))
            thumbs.append(canvas)
        out = self.Image.new('RGB', (size * len(thumbs), size), 'white')
        for i, image in enumerate(thumbs):
            out.paste(image, (i * size, 0))
        return out

    @staticmethod
    def _clamp8(value, lo, hi):
        n = max(lo, min(hi, int(value)))
        return n - n % 8

    def encode(self, image):
        out = io.BytesIO()
        image.save(out, 'PNG', optimize=True)
        return 'data:image/png;base64,' + base64.b64encode(out.getvalue()).decode()

    def generate(self, data):
        torch = self.torch
        self.load()
        refs = data.get('reference_images') or []
        kwargs = {
            'prompt': str(data.get('prompt', ''))[:12000],
            'negative_prompt': str(data.get('negative', ''))[:4000],
            'width': self._clamp8(data.get('width', 512), 384, 768),
            'height': self._clamp8(data.get('height', 512), 384, 768),
            'num_inference_steps': max(8, min(50, int(data.get('steps', 26)))),
            'guidance_scale': max(1, min(14, float(data.get('guidance', 7.5))))
        }
        seed = int(data.get('seed') or secrets.randbelow(2**31 - 1))
        kwargs['generator'] = torch.Generator(device=self.device).manual_seed(seed)
        if refs:
            self.load_ip()
            self.pipe.set_ip_adapter_scale(max(0, min(1, float(data.get('reference_strength', 0.7)))))
            kwargs['ip_adapter_image'] = self.collage(refs)
        started = time.perf_counter()
        with torch.inference_mode():
            image = self.pipe(**kwargs).images[0]
        if self.ip_loaded:
            try:
                self.pipe.set_ip_adapter_scale(0)
            except Exception:
                pass
        return {'image': self.encode(image), 'seed': seed, 'seconds': time.perf_counter() - started}

    def do_inpaint(self, data):
        torch = self.torch
        self.load_inpaint()
        image = self.decode_image(data['image'])
        mask = self.decode_image(data['mask']).convert('L')
        w = self._clamp8(image.width, 384, 768)
        h = self._clamp8(image.height, 384, 768)
        image = image.resize((w, h), self.Image.Resampling.LANCZOS)
        mask = mask.resize(image.size, self.Image.Resampling.NEAREST)
        seed = int(data.get('seed') or secrets.randbelow(2**31 - 1))
        started = time.perf_counter()
        with torch.inference_mode():
            result = self.inpaint(
                prompt=str(data.get('prompt', ''))[:12000],
                negative_prompt=str(data.get('negative', ''))[:4000],
                image=image, mask_image=mask,
                num_inference_steps=max(8, min(50, int(data.get('steps', 28)))),
                guidance_scale=max(1, min(14, float(data.get('guidance', 7.5)))),
                generator=torch.Generator(device=self.device).manual_seed(seed)
            ).images[0]
        return {'image': self.encode(result), 'seed': seed, 'seconds': time.perf_counter() - started}


def allowed_origin(origin):
    if origin in ('https://questiontemplate.com', 'https://www.questiontemplate.com'):
        return True
    parsed = urlparse(origin or '')
    return parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')


def make_handler(audio_engine, key):
    gpu_lock = threading.Lock()
    image_engine = None

    def get_image_engine():
        nonlocal image_engine
        if image_engine is None:
            image_engine = ImageEngine(
                audio_engine.torch, audio_engine.device, audio_engine.index, audio_engine.gpu
            )
        return image_engine

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, code, payload, binary=False):
            data = payload if binary else json.dumps(payload).encode()
            self.send_response(code)
            origin = self.headers.get('Origin', '')
            if allowed_origin(origin):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
                self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type')
                self.send_header('Access-Control-Expose-Headers', 'X-Sample-Rate')
                self.send_header('Access-Control-Allow-Private-Network', 'true')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Type', 'application/octet-stream' if binary else 'application/json')
            if binary:
                self.send_header('X-Sample-Rate', '24000')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def permitted(self):
            origin = self.headers.get('Origin')
            if origin and not allowed_origin(origin):
                self.reply(403, {'error': 'This origin is not allowed.'})
                return False
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + key):
                self.reply(401, {'error': 'Pairing expired. Open the helper link again.'})
                return False
            return True

        def do_OPTIONS(self):
            if not allowed_origin(self.headers.get('Origin')):
                self.reply(403, {'error': 'This origin is not allowed.'})
            else:
                self.reply(200, {})

        def do_GET(self):
            if not self.permitted():
                return
            if self.path == '/health':
                self.reply(200, audio_engine.health())
            elif self.path == '/image/health':
                self.reply(200, get_image_engine().health())
            else:
                self.reply(404, {'error': 'Unknown endpoint.'})

        def do_POST(self):
            if not self.permitted():
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    self.reply(413, {'error': 'Request is too large.'})
                    return
                body = json.loads(self.rfile.read(length))
                path = self.path
                if not gpu_lock.acquire(blocking=False):
                    self.reply(409, {'error': 'The NVIDIA helper is busy in another tab. Wait for that job to finish.'})
                    return
                try:
                    if path == '/prepare':
                        voice = body.get('voice')
                        audio_engine.prepare(voice)
                        self.reply(200, audio_engine.health())
                    elif path == '/synthesize':
                        voice = body.get('voice')
                        phonemes, speed = body.get('phonemes'), body.get('speed')
                        if voice not in VOICES:
                            raise ValueError('Choose a supported studio voice.')
                        if not isinstance(phonemes, str) or not 1 <= len(phonemes) <= 278:
                            raise ValueError('Invalid speech section.')
                        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not .5 <= speed <= 2:
                            raise ValueError('Choose a speaking speed between 0.5 and 2.')
                        self.reply(200, audio_engine.synthesize(phonemes, voice, speed), binary=True)
                    elif path == '/image/generate':
                        self.reply(200, get_image_engine().generate(body))
                    elif path == '/image/inpaint':
                        self.reply(200, get_image_engine().do_inpaint(body))
                    else:
                        self.reply(404, {'error': 'Unknown endpoint.'})
                finally:
                    gpu_lock.release()
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
                self.reply(400, {'error': str(error) or 'Invalid request.'})
            except Exception as error:
                message = str(error)
                if 'out of memory' in message.lower():
                    try:
                        audio_engine.torch.cuda.empty_cache()
                    except Exception:
                        pass
                    message = 'GPU memory ran out. Close GPU apps, use 512×512, or generate without character references.'
                else:
                    print('Local helper request failed:', type(error).__name__, flush=True)
                    traceback.print_exc()
                    message = 'Local NVIDIA generation failed. Check the helper window and try again.'
                self.reply(503, {'error': message})
    return Handler


def main():
    print('Loading QuestionTemplate local NVIDIA helper.', flush=True)
    audio_engine = CudaEngine()
    key = secrets.token_hex(32)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(audio_engine, key))
    server.daemon_threads = True
    link = 'https://questiontemplate.com/#native=' + key
    print(
        '\nReady on ' + audio_engine.gpu +
        '. Audio and Studio now share this one helper.\n'
        'Keep this window open. Refreshing or switching pages will reconnect automatically.\n'
        'Open this link once to pair:\n' + link,
        flush=True
    )
    webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
