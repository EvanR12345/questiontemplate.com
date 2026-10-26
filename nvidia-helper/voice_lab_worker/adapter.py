"""One persistent model runtime. JSON-line IPC is private to service.py.

Official interfaces pinned in manifest.json; no substitute checkpoints or voices.
"""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from preflight import ensure_cuda_layout

ROOT = Path(os.environ.get('QT_LAB_ROOT', '/workspace/questiontemplate-voice-lab')).resolve()
QWEN_SAMPLER_DEFAULTS = {
    'do_sample': True, 'temperature': .9, 'top_p': 1.0, 'top_k': 50, 'repetition_penalty': 1.05,
    'subtalker_dosample': True, 'subtalker_temperature': .9, 'subtalker_top_p': 1.0, 'subtalker_top_k': 50,
}
QWEN_VARIANTS = {'clone': 'base', 'design': 'design', 'preset': 'preset'}
DRAMA_ENVELOPE_MARKER = '@@drama3-space@@'
DRAMA_DESIGN_HOOK = ('\n\n# QuestionTemplate: pinned Fish Audio Space described-character preprocessor.\n'
                     'from drama_space_ext import Drama3SpacePreprocessor as Drama3SpeechPreprocessor  # noqa: E402\n')


def drama_start_command(root):
    """Audition startup avoids optional prefill/first-frame graph capture.

    These are supported by the pinned Drama engine. Decode graphs retain their
    native default; checkpoints, precision, sampling and request limits are
    unchanged. The startup saving has not been measured on a GPU.
    """
    root = Path(root)
    return [str(root / 'envs' / 'drama3' / 'bin' / 'sgl-omni'), 'serve',
            '--model-path', str(root / 'models' / 'drama3'), '--host', '127.0.0.1', '--port', '18767',
            '--tts_engine.factory.max_running_requests', '1',
            '--tts_engine.engine.cuda_graph_backend_prefill', 'disabled',
            '--tts_engine.factory.first_frame_cuda_graphs', 'false',
            '--vocoder.factory.codec_path', str(root / 'models' / 'drama3-codec')]


def install_drama_design_hook(root):
    """Use the author's Dockerfile rebind so spawned stages receive the hook too.

    Only the owned installed Drama stages module is modified, before starting
    sgl-omni. Importing the custom class in this controller would not propagate
    to multiprocessing spawn workers. No model or precision is changed.
    """
    candidates = list((Path(root) / 'envs' / 'drama3' / 'lib').glob(
        'python*/site-packages/sglang_omni/models/fishaudio_drama3/stages.py'))
    editable_root = Path(root) / 'sources' / 'drama3'
    editable_stages = editable_root / 'sglang_omni/models/fishaudio_drama3/stages.py'
    if editable_stages.is_file():
        candidates.append(editable_stages)
    if len(candidates) != 1:
        raise RuntimeError('The owned Drama runtime has no unique preprocessing factory module.')
    stages = candidates[0]
    owned_roots = [(Path(root) / 'envs' / 'drama3').resolve(), editable_root.resolve()]
    resolved_stages = stages.resolve()
    if (not resolved_stages.is_relative_to(Path(root).resolve())
            or not any(resolved_stages.is_relative_to(owned) for owned in owned_roots)):
        raise RuntimeError('The Drama preprocessing factory is outside its owned environment.')
    source = stages.read_text(encoding='utf-8')
    if 'def create_preprocessing_executor' not in source or 'preprocessor = Drama3SpeechPreprocessor(' not in source:
        raise RuntimeError('The installed Drama factory differs from the reviewed author hook interface.')
    if DRAMA_DESIGN_HOOK not in source:
        # uv can hard-link installed modules to its wheel cache. Replace this
        # owned module atomically rather than changing a shared cache inode.
        pending = stages.with_name(stages.name + '.qt-design.pending')
        try:
            pending.write_text(source + DRAMA_DESIGN_HOOK, encoding='utf-8')
            os.chmod(pending, stages.stat().st_mode)
            os.replace(pending, stages)
        finally:
            pending.unlink(missing_ok=True)
    return stages


def drama_generation_payload(body, reference):
    """Exact author Space envelope for design; plain/clone retain stock input."""
    text = body['text']
    parameters = body['parameters']
    if body['mode'] == 'design':
        if reference:
            raise ValueError('Drama character design cannot also use reference audio.')
        character = body.get('character') or {}
        text = DRAMA_ENVELOPE_MARKER + json.dumps({
            'text': text, 'voice_references': 0, 'history': [],
            'character': {'name': character.get('name', 'Narrator'),
                          'description': body['description'], 'scene': character.get('scene') or None},
        }, ensure_ascii=False)
    payload = {'input': text, 'response_format': 'wav', 'stream': False,
               'temperature': parameters['temperature'], 'top_p': parameters['top_p'],
               # Author Space UI uses 0 for all 4097 main-token candidates;
               # the native API receives 4097; codebook heads keep their cap 30.
               'top_k': int(parameters['top_k']) or 4097, 'max_new_tokens': 1300}
    if reference:
        payload['references'] = [{'audio_path': str(reference), 'text': body['reference']['transcript']}]
    return payload


def qwen_variant(mode):
    try:
        return QWEN_VARIANTS[mode]
    except KeyError:
        raise ValueError('Choose cloning, voice design, or a named Qwen preset.') from None


def qwen_generation_options(parameters):
    """Explicit checkpoint defaults; older seed-only jobs retain their behavior."""
    options = {key: parameters.get(key, default) for key, default in QWEN_SAMPLER_DEFAULTS.items()}
    for key in ('top_k', 'subtalker_top_k'):
        options[key] = int(options[key])
    options['max_new_tokens'] = 750  # Visible audition safety cap, not a pacing control.
    return options


def voxcpm_generation_options(body, reference):
    parameters = body['parameters']
    strategy = parameters.get('clone_strategy', 'auto')
    if strategy not in {'auto', 'reference', 'prompt_reference'}:
        raise ValueError('Choose a supported VoxCPM cloning strategy.')
    description = body.get('description', '').strip()
    transcript = (body.get('reference') or {}).get('transcript', '').strip()
    if body['mode'] == 'clone' and strategy == 'prompt_reference':
        if not reference or not transcript:
            raise ValueError('VoxCPM prompt and reference cloning needs a recording and its exact transcript.')
        if description:
            raise ValueError('VoxCPM prompt and reference cloning preserves reference delivery; use reference-only cloning for style instructions.')
    # The official demo removes nested half/full-width parentheses to preserve
    # the model's documented "(control)text" conditioning syntax.
    control = re.sub(r'[()（）]', '', description).strip()
    text = '(' + control + ')' + body['text'] if control else body['text']
    options = {'text': text, 'cfg_value': parameters['cfg_value'],
               'inference_timesteps': int(parameters['inference_timesteps']), 'seed': int(parameters['seed']),
               'normalize': False, 'denoise': False, 'retry_badcase': False, 'max_len': 2048}
    if reference:
        if strategy == 'prompt_reference':
            options.update(reference_wav_path=str(reference), prompt_wav_path=str(reference), prompt_text=transcript)
        elif strategy == 'reference' or not transcript:
            options['reference_wav_path'] = str(reference)
        else:
            # Preserve the original job contract: auto + transcript means
            # continuation only. The explicit new strategy opts into both paths.
            options.update(prompt_wav_path=str(reference), prompt_text=transcript)
    return options


class AuditionLimit(RuntimeError):
    """Generation hit a cap and cannot be presented as a complete audition."""


def check_frame_count(count, limit):
    # Conservative at the final frame because some wrappers remove an EOS row.
    if int(count) >= int(limit) - 1:
        raise AuditionLimit('The model reached its audition output limit. Shorten the script; this output was not marked complete.')


@contextmanager
def qwen_limit_guard(model):
    original = model.generate
    def checked(*args, **kwargs):
        result = original(*args, **kwargs)
        for codes in result[0]:
            check_frame_count(codes.shape[0], kwargs['max_new_tokens'])
        return result
    model.generate = checked
    try:
        yield
    finally:
        model.generate = original


@contextmanager
def vox_limit_guard(model):
    original = model._inference
    def checked(*args, **kwargs):
        generator = original(*args, **kwargs)
        try:
            for result in generator:
                if not kwargs.get('streaming', False):
                    check_frame_count(result[1].shape[0], kwargs['max_len'])
                yield result
        finally:
            generator.close()
    model._inference = checked
    try:
        yield
    finally:
        model._inference = original


def check_drama_finish(headers, limit):
    reason = headers.get('X-Finish-Reason', '').strip().lower()
    if reason not in {'stop', 'eos'}:
        raise AuditionLimit('The model did not confirm a complete audition. Shorten the script; capped or unknown output was not marked complete.')
    count = headers.get('X-Completion-Tokens')
    if count is not None:
        check_frame_count(int(count), limit)
    return reason


def emit(event, **values):
    print('QT_EVENT ' + json.dumps({'event': event, **values}), flush=True)


def drama_cuda_environment(root, inherited=None):
    environment = dict(os.environ if inherited is None else inherited)
    # The base image's nvcc can be older than the CUDA runtime installed by the
    # pinned SGLang wheel stack. DeepGemm JIT needs >=12.9; use that stack's own
    # complete CUDA13 toolkit rather than the image's /usr/local/cuda12.8.
    toolkit = ensure_cuda_layout(root)['toolkit']
    required = [toolkit / 'bin' / 'nvcc', toolkit / 'include' / 'cuda_runtime.h',
                toolkit / 'nvvm' / 'libdevice' / 'libdevice.10.bc']
    if not all(path.is_file() for path in required):
        raise RuntimeError('Drama3 requires the complete CUDA13 toolkit from its pinned runtime environment.')
    environment.update(CUDA_HOME=str(toolkit), CUDA_PATH=str(toolkit), CUDACXX=str(toolkit / 'bin' / 'nvcc'))
    environment['PATH'] = str(toolkit / 'bin') + os.pathsep + str(Path(root) / 'envs' / 'drama3' / 'bin') + os.pathsep + environment.get('PATH', '')
    environment['LD_LIBRARY_PATH'] = str(toolkit / 'lib') + os.pathsep + environment.get('LD_LIBRARY_PATH', '')
    environment['PYTHONPATH'] = str(Path(__file__).resolve().parent) + os.pathsep + environment.get('PYTHONPATH', '')
    cache = Path(root) / 'cache'
    environment.update(SGLANG_CACHE_DIR=str(cache / 'sglang'), SGLANG_JIT_CACHE_DIR=str(cache / 'sglang-jit'),
                       SGLANG_DG_CACHE_DIR=str(cache / 'deep-gemm'), DG_JIT_CACHE_DIR=str(cache / 'deep-gemm'),
                       TORCH_EXTENSIONS_DIR=str(cache / 'torch-extensions'))
    return environment


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
                        '-vn', '-ac', '1', '-c:a', 'pcm_s16le', str(pending)],
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
        variant = qwen_variant(mode) if self.kind == 'qwen3' else self.kind
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
        install_drama_design_hook(ROOT)
        command = drama_start_command(ROOT)
        log = (ROOT / 'logs' / 'drama3-sglang.log').open('a')
        self.drama = subprocess.Popen(command, stdout=log, stderr=log, env=drama_cuda_environment(ROOT))
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
            payload = drama_generation_payload(body, reference)
            request = urllib.request.Request('http://127.0.0.1:18767/v1/audio/speech',
                                             json.dumps(payload).encode(), {'Content-Type': 'application/json'}, method='POST')
            with urllib.request.urlopen(request, timeout=180) as response:
                check_drama_finish(response.headers, payload['max_new_tokens'])
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
            kwargs = voxcpm_generation_options(body, reference)
            with vox_limit_guard(self.model.tts_model):
                audio = self.model.generate(**kwargs)
            write_audio(output, audio, self.model.tts_model.sample_rate)
        else:
            import torch
            torch.manual_seed(int(parameters['seed']))
            torch.cuda.manual_seed_all(int(parameters['seed']))
            kwargs = {'text': body['text'], 'language': body['language'], **qwen_generation_options(parameters)}
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
                with qwen_limit_guard(self.model.model):
                    wavs, sample_rate = self.model.generate_voice_clone(**kwargs, voice_clone_prompt=prompt)
            elif body['mode'] == 'preset':
                with qwen_limit_guard(self.model.model):
                    wavs, sample_rate = self.model.generate_custom_voice(**kwargs, speaker=parameters.get('speaker', 'vivian'),
                                                                         instruct=body.get('description', ''))
            else:
                with qwen_limit_guard(self.model.model):
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
        except Exception as error:
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit('error', id=body.get('id') if isinstance(locals().get('body'), dict) else None,
                 code='AUDITION_LIMIT' if isinstance(error, AuditionLimit) else 'INFERENCE_FAILED')
            # Release a failed runtime rather than attempting an implicit retry.
            return


if __name__ == '__main__':
    main()
