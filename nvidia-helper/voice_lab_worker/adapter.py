"""One persistent model runtime. JSON-line IPC is private to service.py.

Official interfaces pinned in manifest.json; no substitute checkpoints or voices.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(os.environ.get('QT_LAB_ROOT', '/workspace/questiontemplate-voice-lab')).resolve()


def emit(event, **values):
    print('QT_EVENT ' + json.dumps({'event': event, **values}), flush=True)


def reference_wav(reference):
    if not reference:
        return None
    raw = base64.b64decode(reference['audioBase64'], validate=True)
    digest = hashlib.sha256(raw).hexdigest()
    folder = ROOT / 'references'
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0o700)
    output = folder / (digest + '.wav')
    if output.exists():
        return output
    source = folder / (digest + '.upload')
    source.write_bytes(raw)
    try:
        probe = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(source)],
                               capture_output=True, text=True, check=True, timeout=20)
        duration = float(json.loads(probe.stdout)['format']['duration'])
        if not .25 <= duration <= 60:
            raise ValueError('Reference audio must last between 0.25 and 60 seconds.')
        pending = folder / (digest + '.pending.wav')
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0',
                        '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(pending)],
                       check=True, capture_output=True, timeout=30)
        os.replace(pending, output)
        return output
    finally:
        source.unlink(missing_ok=True)


def write_audio(output, waveform, sample_rate):
    import numpy as np
    import soundfile as sf
    audio = np.asarray(waveform, dtype=np.float32).squeeze()
    if audio.ndim != 1 or audio.size == 0 or not np.isfinite(audio).all():
        raise ValueError('The model returned invalid audio.')
    duration = audio.size / int(sample_rate)
    if not 0 < duration <= 120:
        raise ValueError('Generated audio exceeds the audition duration limit.')
    if np.max(np.abs(audio)) > 1.01:
        raise ValueError('Generated audio exceeds full scale. Review the private model diagnostics.')
    sf.write(str(output), audio, int(sample_rate), subtype='PCM_16')


class Runtime:
    def __init__(self, model):
        self.kind = model
        self.model = None
        self.variant = None
        self.drama = None
        self.clone_cache = {}

    def load(self, mode):
        variant = ('base' if mode == 'clone' else 'design') if self.kind == 'qwen3' else self.kind
        if self.variant == variant:
            return 0.0
        started = time.monotonic()
        if self.kind == 'drama3':
            self.start_drama()
        else:
            import gc
            import torch
            if not torch.cuda.is_available():
                raise RuntimeError('This worker requires a CUDA GPU; CPU fallback is disabled.')
            if self.model is not None:
                self.model = None
                self.clone_cache.clear()
                gc.collect()
                torch.cuda.empty_cache()
            if self.kind == 'voxcpm2':
                from voxcpm import VoxCPM
                self.model = VoxCPM.from_pretrained(str(ROOT / 'models' / 'voxcpm2'), load_denoiser=False,
                                                   optimize=False, device='cuda:0', local_files_only=True)
            elif self.kind == 'qwen3':
                from qwen_tts import Qwen3TTSModel
                self.model = Qwen3TTSModel.from_pretrained(str(ROOT / 'models' / ('qwen3-' + variant)),
                                                          device_map='cuda:0', dtype=torch.bfloat16,
                                                          attn_implementation='sdpa', local_files_only=True)
            else:
                raise ValueError('Unknown model adapter.')
        self.variant = variant
        return time.monotonic() - started

    def start_drama(self):
        # Current official default is the full FP8-expert/BF16 checkpoint. Int4 is
        # a different checkpoint and is deliberately not substituted here.
        command = [str(ROOT / 'envs' / 'drama3' / 'bin' / 'sgl-omni'), 'serve',
                   '--model-path', str(ROOT / 'models' / 'drama3'), '--host', '127.0.0.1', '--port', '18767',
                   '--tts_engine.factory.max_running_requests', '1',
                   '--tts_engine.factory.first_frame_cuda_graph_max_requests', '1',
                   '--vocoder.factory.codec_path', str(ROOT / 'models' / 'drama3-codec')]
        log = (ROOT / 'logs' / 'drama3-sglang.log').open('a')
        self.drama = subprocess.Popen(command, stdout=log, stderr=log)
        log.close()
        deadline = time.monotonic() + 1100
        while time.monotonic() < deadline:
            if self.drama.poll() is not None:
                raise RuntimeError('Drama3 serving process exited before readiness.')
            try:
                with urllib.request.urlopen('http://127.0.0.1:18767/health', timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, TimeoutError):
                pass
            time.sleep(1)
        raise RuntimeError('Drama3 serving process did not become healthy within the load limit.')

    def generate(self, body):
        directory = Path(body['directory']).resolve()
        if directory.parent != ROOT / 'jobs' or directory.name != body['id']:
            raise ValueError('Invalid output directory.')
        preprocessing = time.monotonic()
        reference = reference_wav(body.get('reference'))
        reference_seconds = time.monotonic() - preprocessing
        load_seconds = self.load(body['mode'])
        emit('loaded', id=body['id'], loadSeconds=load_seconds)
        generation_start = time.monotonic()
        conditioning_seconds = 0.0
        cache_hit = False
        parameters = body['parameters']
        output = directory / 'audio.wav'
        if self.kind == 'drama3':
            payload = {'input': body['text'], 'response_format': 'wav', 'stream': False,
                       'temperature': parameters['temperature'], 'top_p': parameters['top_p'],
                       'top_k': int(parameters['top_k']), 'max_new_tokens': 1300}
            if reference:
                payload['references'] = [{'audio_path': str(reference), 'text': body['reference']['transcript']}]
            request = urllib.request.Request('http://127.0.0.1:18767/v1/audio/speech',
                                             json.dumps(payload).encode(), {'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=180) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError('Drama3 output exceeded the audition size limit.')
            raw = directory / 'drama-output.wav'
            raw.write_bytes(data)
            # Canonical PCM16 WAV allows strict duration validation in stdlib service.
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(raw), '-map', '0:a:0', '-vn',
                            '-ac', '1', '-c:a', 'pcm_s16le', str(output)], capture_output=True, check=True, timeout=30)
            raw.unlink()
        elif self.kind == 'voxcpm2':
            text = body['text']
            if body['description']:
                text = '(' + body['description'] + ')' + text
            kwargs = {'text': text, 'cfg_value': parameters['cfg_value'], 'inference_timesteps': int(parameters['inference_timesteps']),
                      'seed': int(parameters['seed']), 'normalize': False, 'denoise': False,
                      'retry_badcase': False, 'max_len': 2048}
            if reference:
                transcript = body['reference']['transcript'].strip()
                if transcript:
                    kwargs.update(prompt_wav_path=str(reference), prompt_text=transcript)
                else:
                    kwargs['reference_wav_path'] = str(reference)
            audio = self.model.generate(**kwargs)
            write_audio(output, audio, self.model.tts_model.sample_rate)
        else:
            import torch
            torch.manual_seed(int(parameters['seed']))
            torch.cuda.manual_seed_all(int(parameters['seed']))
            kwargs = {'text': body['text'], 'language': body['language'], 'max_new_tokens': 750}
            if body['mode'] == 'clone':
                started = time.monotonic()
                key = hashlib.sha256((str(reference) + '\n' + body['reference']['transcript']).encode()).hexdigest()
                cache_hit = key in self.clone_cache
                if not cache_hit:
                    if len(self.clone_cache) >= 16:
                        self.clone_cache.pop(next(iter(self.clone_cache)))
                    self.clone_cache[key] = self.model.create_voice_clone_prompt(ref_audio=str(reference),
                                                                               ref_text=body['reference']['transcript'],
                                                                               x_vector_only_mode=False)
                prompt = self.clone_cache[key]
                conditioning_seconds = time.monotonic() - started
                wavs, sample_rate = self.model.generate_voice_clone(**kwargs, voice_clone_prompt=prompt)
            else:
                wavs, sample_rate = self.model.generate_voice_design(**kwargs, instruct=body['description'])
            write_audio(output, wavs[0], sample_rate)
        # Avoid recording asynchronous CUDA submission time as completed compute.
        if self.kind != 'drama3':
            import torch
            torch.cuda.synchronize()
        generation_seconds = time.monotonic() - generation_start
        return {'loadSeconds': load_seconds, 'startupSeconds': None,
                'referenceDecodeSeconds': reference_seconds, 'generationSeconds': generation_seconds,
                'conditioningSeconds': conditioning_seconds, 'conditioningCacheHit': cache_hit,
                'precision': 'official FP8 experts / BF16 remainder' if self.kind == 'drama3' else ('bfloat16' if self.kind == 'qwen3' else 'upstream VoxCPM2 default'),
                'backend': 'SGLang-Omni' if self.kind == 'drama3' else ('qwen-tts SDPA' if self.kind == 'qwen3' else 'VoxCPM optimize=False'),
                'parameters': parameters, 'automaticRetries': 0}


def main():
    runtime = Runtime(sys.argv[1])
    for line in sys.stdin:
        try:
            body = json.loads(line)
            metrics = runtime.generate(body)
            emit('done', id=body['id'], metrics=metrics)
        except Exception:
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit('error', id=body.get('id') if isinstance(locals().get('body'), dict) else None)
            # Release a failed runtime rather than attempting an implicit retry.
            return


if __name__ == '__main__':
    main()
