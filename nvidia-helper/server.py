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
            'capabilities': ['audio', 'image', 'image-queue'], 'imageModel': MODEL_ID
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


from image_engine import ImageEngine
from image_queue import ImageQueue
from pathlib import Path
import mimetypes
from urllib.parse import unquote

WEB_ROOT = Path(os.environ.get('QT_WEB_ROOT', str(Path(__file__).resolve().parent / 'web'))).resolve()

def allowed_origin(origin):
    if origin in ('https://questiontemplate.com', 'https://www.questiontemplate.com'):
        return True
    parsed = urlparse(origin or '')
    return parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')


def make_handler(audio_engine, key, queue_root=None, image_factory=None):
    gpu_lock = getattr(audio_engine, "lock", threading.Lock())
    image_engine = None

    def get_image_engine():
        nonlocal image_engine
        if image_engine is None:
            image_engine = (image_factory or ImageEngine)(
                audio_engine.torch, audio_engine.device, audio_engine.index, audio_engine.gpu, MODEL_ID, INPAINT_ID
            )
        return image_engine

    def before_image():
        audio_engine.model.to('cpu')
        audio_engine.voices = {name: voice.to('cpu') for name, voice in audio_engine.voices.items()}
        audio_engine.torch.cuda.empty_cache()

    def before_audio():
        if image_engine is not None:
            image_engine.unload()
        if hasattr(audio_engine, 'model'):
            audio_engine.model.to(audio_engine.device)
            audio_engine.voices = {name: voice.to(audio_engine.device) for name, voice in audio_engine.voices.items()}

    queue = ImageQueue(queue_root or Path(__file__).resolve().parent / 'outputs',
                       lambda data, checkpoint: get_image_engine().generate(data, checkpoint), gpu_lock,
                       before_image if hasattr(audio_engine, 'model') else lambda: None)

    class Handler(BaseHTTPRequestHandler):
        image_queue = queue
        def log_message(self, *_):
            pass

        def reply(self, code, payload, binary=False, content_type=None):
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
            self.send_header('Content-Type', content_type or ('application/octet-stream' if binary else 'application/json'))
            if binary and content_type is None:
                self.send_header('X-Sample-Rate', '24000')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass

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
            path = urlparse(self.path).path
            if path.startswith('/app/'):
                file = (WEB_ROOT / unquote(path[5:])).resolve()
                if not file.is_relative_to(WEB_ROOT) or any(part.startswith('.') for part in file.relative_to(WEB_ROOT).parts):
                    self.reply(404, {'error':'File not found.'})
                    return
                if file.is_dir():
                    file = file / 'index.html'
                try:
                    data = file.read_bytes()
                except (OSError, ValueError):
                    self.reply(404, {'error':'File not found.'})
                    return
                content_type = 'text/javascript' if file.suffix in ('.mjs','.js') else mimetypes.guess_type(str(file))[0] or 'application/octet-stream'
                self.reply(200, data, binary=True, content_type=content_type)
                return
            if not self.permitted():
                return
            if path == '/health':
                self.reply(200, audio_engine.health())
            elif path == '/image/health':
                self.reply(200, get_image_engine().health())
            elif path == '/image/queue':
                self.reply(200, queue.snapshot())
            elif path.startswith('/image/result/'):
                try:
                    self.reply(200, queue.result_path(path.rsplit('/', 1)[-1]).read_bytes(), binary=True, content_type='image/png')
                except (ValueError, FileNotFoundError) as error:
                    self.reply(404, {'error': str(error)})
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
                if not isinstance(body, dict):
                    raise ValueError('Request must be an object.')
                path = self.path
                # Controls must never wait behind the inference lock.
                if path == '/image/queue':
                    self.reply(202, queue.enqueue(body))
                    return
                if path == '/image/control':
                    self.reply(200, queue.control(body.get('action')))
                    return
                audio_request = path in ('/prepare', '/synthesize')
                if audio_request:
                    queue.control('yield-audio')
                if not gpu_lock.acquire(timeout=15 if audio_request else 0):
                    self.reply(409, {'error': 'GPU is generating. Pause the image queue and cancel its current image before switching to Audio.'})
                    return
                try:
                    if path in ('/prepare', '/synthesize'):
                        queue.control('pause')
                        before_audio()
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
                        before_image()
                        self.reply(200, get_image_engine().legacy(body))
                    elif path == '/image/inpaint':
                        before_image()
                        self.reply(200, get_image_engine().legacy({**body, 'operation':'inpaint'}))
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
                    message = type(error).__name__ + ': ' + str(error)[:800]
                self.reply(503, {'error': message})
    return Handler


def main():
    print('Loading QuestionTemplate local NVIDIA helper.', flush=True)
    audio_engine = CudaEngine()
    key_path = Path(__file__).resolve().parent / '.pairing-key'
    key = key_path.read_text().strip() if key_path.exists() else secrets.token_hex(32)
    if len(key) != 64 or any(c not in '0123456789abcdef' for c in key):
        key = secrets.token_hex(32)
    key_path.write_text(key)
    # Keep the reconnect credential readable only by the current Windows user.
    if os.name == 'nt':
        import subprocess
        account = os.environ.get('USERDOMAIN', '') + '\\' + os.environ.get('USERNAME', '')
        subprocess.run(['icacls', str(key_path), '/inheritance:r', '/grant:r', account + ':F'], capture_output=True)
    server = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(audio_engine, key))
    server.daemon_threads = True
    site = 'https://questiontemplate.com/manga.html'
    link = site + '#native=' + key
    print(
        '\nReady on ' + audio_engine.gpu +
        '. Audio and Studio now share this one helper.\n'
        'Keep this window open. Refreshing or switching pages will reconnect automatically.\n'
        'Open this link once to pair:\n' + link,
        flush=True
    )
    if os.environ.get('QT_NO_BROWSER') != '1':
        webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.RequestHandlerClass.image_queue.close()
        server.server_close()


if __name__ == '__main__':
    main()
