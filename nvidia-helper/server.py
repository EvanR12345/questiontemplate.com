"""Local CUDA speech bridge. Run start-windows.bat; never expose this port."""
import hmac
import json
import os
import secrets
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

VOICES = {'am_michael', 'am_fenrir', 'am_puck', 'af_heart', 'af_bella', 'af_nicole', 'bm_george', 'bf_emma'}
PORT = 8765
MAX_BODY = 8192


class CudaEngine:
    def __init__(self):
        import torch
        from huggingface_hub import hf_hub_download
        from kokoro import KModel
        self.torch, self.download = torch, hf_hub_download
        if not torch.cuda.is_available():
            raise RuntimeError('NVIDIA CUDA is unavailable. Update the NVIDIA driver and run start-windows.bat again. CPU fallback is not used by this helper.')
        index = max(range(torch.cuda.device_count()), key=lambda i: torch.cuda.get_device_properties(i).total_memory)
        self.device = f'cuda:{index}'
        torch.cuda.set_device(index)
        torch.set_num_threads(min(4, os.cpu_count() or 1))
        # Full precision preserves quality; GTX 1650 has no Tensor Cores.
        torch.backends.cudnn.benchmark = False
        self.model = KModel(repo_id='hexgrad/Kokoro-82M').to(self.device).eval()
        self.voices = {}
        self.warm = set()
        self.lock = threading.Lock()
        self.gpu = torch.cuda.get_device_name(index)
        self.prepare('am_michael')

    def health(self):
        return {'protocol': 1, 'backend': 'cuda', 'gpu': self.gpu,
                'sampleRate': 24000, 'precision': 'float32', 'vocab': self.model.vocab}

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
        return audio.float().numpy().astype('<f4', copy=False).tobytes()

    def synthesize(self, phonemes, voice, speed):
        self.prepare(voice)
        return self.render(phonemes, voice, speed)


def allowed_origin(origin):
    if origin in ('https://questiontemplate.com', 'https://www.questiontemplate.com'):
        return True
    parsed = urlparse(origin or '')
    return parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', 'localhost')


def make_handler(engine, key):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log scripts, phonemes, pairing keys, or request URLs.

        def reply(self, code, payload, audio=False):
            data = payload if audio else json.dumps(payload).encode()
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
            self.send_header('Content-Type', 'application/octet-stream' if audio else 'application/json')
            if audio:
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
                self.reply(401, {'error': 'Pairing expired. Open the helper’s link and reconnect.'})
                return False
            return True

        def do_OPTIONS(self):
            if not allowed_origin(self.headers.get('Origin')):
                self.reply(403, {'error': 'This origin is not allowed.'})
            else:
                self.reply(200, {})

        def do_GET(self):
            if self.permitted():
                self.reply(200, engine.health()) if self.path == '/health' else self.reply(404, {'error': 'Unknown endpoint.'})

        def do_POST(self):
            if not self.permitted():
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= MAX_BODY:
                    self.reply(413, {'error': 'Section is too large.'})
                    return
                body = json.loads(self.rfile.read(length))
                voice = body.get('voice')
                if voice not in VOICES:
                    raise ValueError('Choose a supported studio voice.')
                if not engine.lock.acquire(blocking=False):
                    self.reply(409, {'error': 'NVIDIA helper is already recording in another tab. Stop that recording first.'})
                    return
                try:
                    if self.path == '/prepare':
                        engine.prepare(voice)
                        self.reply(200, engine.health())
                    elif self.path == '/synthesize':
                        phonemes, speed = body.get('phonemes'), body.get('speed')
                        if not isinstance(phonemes, str) or not 1 <= len(phonemes) <= 278:
                            raise ValueError('Invalid speech section. No audio was truncated.')
                        if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not .5 <= speed <= 2:
                            raise ValueError('Choose a speaking speed between 0.5 and 2.')
                        self.reply(200, engine.synthesize(phonemes, voice, speed), audio=True)
                    else:
                        self.reply(404, {'error': 'Unknown endpoint.'})
                finally:
                    engine.lock.release()
            except (ValueError, TypeError, json.JSONDecodeError):
                self.reply(400, {'error': 'Invalid voice or speech section. No audio was truncated.'})
            except Exception as error:
                message = 'CUDA ran out of memory. Close other GPU apps and reconnect.' if 'out of memory' in str(error).lower() else 'CUDA generation failed. Check the helper window and restart it.'
                print('CUDA request failed:', type(error).__name__, flush=True)
                self.reply(503, {'error': message})
    return Handler


def main():
    print('Loading the local NVIDIA voice engine. First setup downloads model files.', flush=True)
    engine = CudaEngine()
    key = secrets.token_hex(32)  # New key each launch; never stored on disk.
    server = ThreadingHTTPServer(('127.0.0.1', PORT), make_handler(engine, key))
    server.daemon_threads = True
    link = 'https://questiontemplate.com/#native=' + key
    print('\nReady on ' + engine.gpu + '. Keep this window open.\nOpen this link to connect:\n' + link, flush=True)
    webbrowser.open(link)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
