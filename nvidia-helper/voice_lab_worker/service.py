"""Private, durable audition worker. Bind only to localhost; reach via SSH tunnel.

The HTTP process has no ML imports. Model runtimes use separate persistent Python
environments, and only one model process occupies the GPU at a time.
"""
import argparse
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / 'manifest.json').read_text())
MAX_BODY = 16 * 1024 * 1024
ID = re.compile(r'[A-Za-z0-9_-]{8,80}')
FINAL = {'completed', 'failed', 'cancelled'}
MODES = {'drama3': {'default', 'clone'}, 'voxcpm2': {'default', 'design', 'clone'}, 'qwen3': {'design', 'clone', 'preset'}}
LANGUAGES = {'english', 'chinese', 'japanese', 'korean', 'german', 'french', 'russian', 'portuguese', 'spanish', 'italian', 'auto'}
CONTROLS = {'drama3': {'temperature': (0.1, 2, 1), 'top_p': (0.1, 1, .9), 'top_k': (1, 100, 30)},
            'voxcpm2': {'cfg_value': (.1, 10, 2), 'inference_timesteps': (1, 50, 10), 'seed': (0, 2147483647, 42)},
            'qwen3': {'seed': (0, 2147483647, 42), 'temperature': (.1, 2, .9),
                      'top_p': (.1, 1, 1), 'top_k': (1, 100, 50), 'repetition_penalty': (.8, 2, 1.05),
                      'subtalker_temperature': (.1, 2, .9), 'subtalker_top_p': (.1, 1, 1),
                      'subtalker_top_k': (1, 100, 50)}}
BOOLEAN_CONTROLS = {'qwen3': {'do_sample': True, 'subtalker_dosample': True}}
SELECT_CONTROLS = {'voxcpm2': {'clone_strategy': (('auto', 'reference', 'prompt_reference'), 'auto')},
                   'qwen3': {'speaker': (('vivian', 'serena', 'uncle_fu', 'dylan', 'eric', 'ryan',
                                         'aiden', 'ono_anna', 'sohee'), 'vivian')}}


class Conflict(ValueError):
    pass


def checkpoint_for(model, mode):
    items = MANIFEST['models'][model]
    if model != 'qwen3':
        return items[0]
    folder = {'clone': 'qwen3-base', 'design': 'qwen3-design', 'preset': 'qwen3-preset'}.get(mode)
    checkpoint = next((item for item in items if item['folder'] == folder), None)
    if checkpoint is None:
        raise ValueError('The requested Qwen checkpoint variant is not configured.')
    return checkpoint


def validate(body):
    if not isinstance(body, dict):
        raise ValueError('A JSON job object is required.')
    job_id, model = body.get('id'), body.get('model')
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise ValueError('Invalid idempotency ID.')
    if model not in MODES or body.get('mode') not in MODES[model]:
        raise ValueError('Choose a supported model and mode.')
    text = body.get('text')
    if not isinstance(text, str) or not text.strip() or len(text) > 2500:
        raise ValueError('Audition text must contain 1 to 2500 characters.')
    description = body.get('description', '')
    if not isinstance(description, str) or len(description) > 1000:
        raise ValueError('Voice description exceeds the audition limit.')
    if body['mode'] == 'design' and not description.strip():
        raise ValueError('Voice design needs a description.')
    language = body.get('language', 'auto')
    if not isinstance(language, str) or language.lower() not in LANGUAGES:
        raise ValueError('Unsupported language selection.')
    if model != 'qwen3' and language.lower() != 'auto':
        raise ValueError('This adapter infers language from text; select Auto.')
    params = body.get('parameters', {})
    if not isinstance(params, dict) or set(params) - (set(CONTROLS[model])
            | set(BOOLEAN_CONTROLS.get(model, {})) | set(SELECT_CONTROLS.get(model, {}))):
        raise ValueError('Unsupported model control.')
    clean_params = {}
    for key, (minimum, maximum, default) in CONTROLS[model].items():
        value = params.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
            raise ValueError('A model control exceeds its audition limits.')
        if key in {'seed', 'top_k', 'subtalker_top_k', 'inference_timesteps'} and int(value) != value:
            raise ValueError('This model control requires a whole number.')
        clean_params[key] = value
    for key, default in BOOLEAN_CONTROLS.get(model, {}).items():
        value = params.get(key, default)
        if type(value) is not bool:
            raise ValueError('This model control must be enabled or disabled.')
        clean_params[key] = value
    for key, (choices, default) in SELECT_CONTROLS.get(model, {}).items():
        value = params.get(key, default)
        if not isinstance(value, str) or value not in choices:
            raise ValueError('Choose a supported built-in voice.' if key == 'speaker' else 'Choose a supported clone strategy.')
        clean_params[key] = value
    reference = None
    if body['mode'] == 'clone':
        ref = body.get('reference')
        if not isinstance(ref, dict) or not isinstance(ref.get('audioBase64'), str):
            raise ValueError('Voice cloning needs a reference recording.')
        try:
            raw = base64.b64decode(ref['audioBase64'], validate=True)
        except (ValueError, TypeError):
            raise ValueError('Invalid reference audio.') from None
        if not 16 <= len(raw) <= 10 * 1024 * 1024:
            raise ValueError('Reference audio exceeds its size limit.')
        transcript = ref.get('transcript', '')
        if not isinstance(transcript, str) or len(transcript) > 4000:
            raise ValueError('Invalid reference transcript.')
        if model in {'drama3', 'qwen3'} and not transcript.strip():
            raise ValueError('This model needs an accurate reference transcript.')
        if model == 'voxcpm2' and clean_params['clone_strategy'] == 'prompt_reference':
            if not transcript.strip():
                raise ValueError('Prompt + reference cloning requires the reference transcript.')
            if description.strip():
                raise ValueError('Prompt + reference cloning cannot use a voice description. Choose Reference only for style control.')
        reference = {'audioBase64': ref['audioBase64'], 'transcript': transcript, 'mime': 'application/octet-stream'}
    return {'id': job_id, 'model': model, 'mode': body['mode'], 'text': text.strip(),
            'language': language.lower(), 'description': description.strip(),
            'parameters': clean_params, 'reference': reference}


class Store:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(self.root / 'jobs.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, digest TEXT NOT NULL, body TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, metrics TEXT NOT NULL, error TEXT)')
        # A process that died mid-inference cannot be assumed not to have generated.
        self.db.execute("UPDATE jobs SET status='failed',error='Worker restarted during inference. Review the partial result before starting a new audition.',updated=? WHERE status='running'", (time.time(),))
        self.db.commit()

    def submit(self, body):
        body = validate(body)
        encoded = json.dumps(body, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        with self.lock:
            old = self.db.execute('SELECT * FROM jobs WHERE id=?', (body['id'],)).fetchone()
            if old:
                if old['digest'] != digest:
                    raise Conflict('That idempotency ID already belongs to different input.')
                return self.public(old)
            if self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] >= 8:
                raise Conflict('The audition queue is full. Finish or cancel existing auditions first.')
            now = time.time()
            self.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)', (body['id'], digest, encoded, 'queued', now, now, '{}', None))
            self.db.commit()
            return self.get(body['id'])

    @staticmethod
    def public(row):
        return {'id': row['id'], 'status': row['status'], 'created': row['created'], 'updated': row['updated'],
                'metrics': json.loads(row['metrics']), **({'error': row['error']} if row['error'] else {})}

    def get(self, job_id):
        with self.lock:
            row = self.db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row:
                raise FileNotFoundError('Unknown audition ID.')
            return self.public(row)

    def next(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
            if row:
                self.update(row['id'], 'running')
                return json.loads(row['body'])

    def update(self, job_id, status, metrics=None, error=None):
        with self.lock:
            old = self.get(job_id)
            # A late model completion must never undo an acknowledged cancellation.
            if old['status'] == 'cancelled' and status != 'cancelled':
                return
            self.db.execute('UPDATE jobs SET status=?,updated=?,metrics=?,error=? WHERE id=?', (status, time.time(), json.dumps(metrics or old['metrics']), error, job_id))
            self.db.commit()


class Supervisor:
    def __init__(self, root, store, hourly_usd=None):
        self.root, self.store, self.hourly_usd = Path(root), store, hourly_usd
        self.process = None
        self.model = None
        self.active = None
        self.events = queue.Queue()
        self.guard = threading.RLock()
        self.shutdown = threading.Event()
        self.last_activity = time.monotonic()
        self.boot = time.time()

    def prepared(self, model):
        receipt = self.root / 'prepared' / (model + '.json')
        python = self.root / 'envs' / model / 'bin' / 'python'
        if not receipt.exists() or not python.exists():
            return False
        try:
            record = json.loads(receipt.read_text())
            models_exist = all((self.root / 'models' / item['folder'] / ('codec.pth' if item.get('allow') else 'config.json')).is_file()
                               for item in MANIFEST['models'][model])
            return models_exist and record['source'] == MANIFEST['sources'][model] and record['models'] == MANIFEST['models'][model]
        except (OSError, ValueError, KeyError):
            return False

    def health(self):
        models = [{'id': m, 'ready': self.prepared(m), 'resident': self.model == m and self.process is not None and self.process.poll() is None,
                   'checkpoint': MANIFEST['models'][m][0]['repo'], 'revision': MANIFEST['models'][m][0]['revision']} for m in MODES]
        return {'ready': any(m['ready'] for m in models), 'models': models, 'activeJob': self.active, 'activeModel': self.model,
                'hourlyUSD': self.hourly_usd, 'uptimeSeconds': time.time() - self.boot,
                'startupSeconds': None, 'startupBasis': 'Pod allocation, image pull and preparation are measured separately by the owner; loadSeconds records model loading only.',
                'execution': 'one resident model; one audition at a time; no automatic retries'}

    def stop_model(self):
        with self.guard:
            process = self.process
            self.process, self.model = None, None
            if process and process.poll() is None:
                try:
                    if os.name == 'posix':
                        os.killpg(process.pid, signal.SIGTERM)
                    else:
                        process.terminate()
                    process.wait(timeout=15)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    if process.poll() is None:
                        if os.name == 'posix':
                            os.killpg(process.pid, signal.SIGKILL)
                        else:
                            process.kill()
            if process and process.stdin:
                process.stdin.close()

    def cancel(self, job_id):
        with self.guard:
            current = self.store.get(job_id)
            if current['status'] not in FINAL:
                self.store.update(job_id, 'cancelled')
                if self.active == job_id:
                    self.stop_model()
            return self.store.get(job_id)

    def start_model(self, model):
        if self.model == model and self.process and self.process.poll() is None:
            return
        self.stop_model()
        if not self.prepared(model):
            raise RuntimeError('Model preparation is incomplete. Run the pinned preparation command on the voice pod.')
        self.events = queue.Queue()
        environment = os.environ.copy()
        environment.update(HF_HOME=str(self.root / 'cache' / 'huggingface'), HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                           PYTHONUNBUFFERED='1', TOKENIZERS_PARALLELISM='false', QT_LAB_ROOT=str(self.root),
                           CUDA_CACHE_PATH=str(self.root / 'cache' / 'cuda'),
                           TRITON_CACHE_DIR=str(self.root / 'cache' / 'triton'),
                           TORCHINDUCTOR_CACHE_DIR=str(self.root / 'cache' / 'torchinductor'))
        logs = self.root / 'logs'
        logs.mkdir(exist_ok=True)
        with (logs / (model + '.stderr.log')).open('a') as log:
            self.process = subprocess.Popen([str(self.root / 'envs' / model / 'bin' / 'python'), str(HERE / 'adapter.py'), model],
                                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log, text=True,
                                            env=environment, start_new_session=True)
        self.model = model
        output, events = self.process.stdout, self.events
        def read():
            try:
                for line in output:
                    if line.startswith('QT_EVENT '):
                        try:
                            events.put(json.loads(line[9:]))
                        except ValueError:
                            pass
            finally:
                events.put({'event': 'exit'})
                output.close()
        threading.Thread(target=read, daemon=True).start()

    def run_one(self, body):
        job_id = body['id']
        self.active = job_id
        started = time.monotonic()
        directory = self.root / 'jobs' / job_id
        directory.mkdir(parents=True, exist_ok=True)
        os.chmod(directory, 0o700)
        payload = dict(body, directory=str(directory))
        with self.guard:
            if self.store.get(job_id)['status'] == 'cancelled':
                return
            self.start_model(body['model'])
            self.process.stdin.write(json.dumps(payload) + '\n')
            self.process.stdin.flush()
        deadline = time.monotonic() + 1200
        while time.monotonic() < deadline and not self.shutdown.is_set():
            if self.store.get(job_id)['status'] == 'cancelled':
                return
            try:
                event = self.events.get(timeout=.5)
            except queue.Empty:
                continue
            if event.get('id') not in (None, job_id):
                continue
            if event['event'] == 'done':
                output = directory / 'audio.wav'
                with wave.open(str(output), 'rb') as audio:
                    duration = audio.getnframes() / audio.getframerate()
                if not 0 < duration <= 120:
                    raise RuntimeError('The output exceeds the audition limit; it was retained privately for inspection.')
                checkpoint = checkpoint_for(body['model'], body['mode'])
                metrics = event['metrics'] | {'audioSeconds': duration, 'workerWallSeconds': time.monotonic() - started,
                                             'hourlyUSD': self.hourly_usd, 'model': body['model'],
                                             'checkpoint': checkpoint['repo'], 'revision': checkpoint['revision'],
                                             'costBasis': 'Per-job worker wall time excludes GPU allocation, image pull, package/model downloads and idle time. Full cold-start receipt must add them.'}
                if self.hourly_usd is not None:
                    metrics['workerEstimatedUSD'] = metrics['workerWallSeconds'] * self.hourly_usd / 3600
                self.store.update(job_id, 'completed', metrics)
                return
            if event['event'] in {'error', 'exit'}:
                if event.get('code') == 'AUDITION_LIMIT':
                    raise RuntimeError('The model reached its audition output limit or did not confirm completion. Shorten the script; this output was not marked complete.')
                raise RuntimeError('Model inference failed. The private worker log contains diagnostics; this audition was not retried.')
        self.stop_model()
        raise RuntimeError('Audition exceeded its runtime limit and was stopped without retry.')

    def loop(self):
        while not self.shutdown.is_set():
            body = self.store.next()
            if not body:
                # Idle GPU memory is released; this does not stop paid pod compute.
                if self.process and time.monotonic() - self.last_activity > 900:
                    self.stop_model()
                self.shutdown.wait(.25)
                continue
            try:
                self.run_one(body)
            except Exception as error:
                self.stop_model()
                message = str(error) if isinstance(error, RuntimeError) else 'Worker could not complete the audition. Inspect the private log; no automatic retry was made.'
                self.store.update(body['id'], 'failed', error=message)
            finally:
                self.active = None
                self.last_activity = time.monotonic()


def handler(store, supervisor, token):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, data, content_type='application/json'):
            if not isinstance(data, bytes):
                data = json.dumps(data).encode()
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(data)

        def permitted(self):
            if not hmac.compare_digest(self.headers.get('Authorization', '').encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
                self.reply(401, {'error': 'Private worker authentication required.'})
                return False
            return True

        def do_GET(self):
            if not self.permitted():
                return
            path = urlparse(self.path).path
            try:
                if path == '/health':
                    return self.reply(200, supervisor.health())
                match = re.fullmatch(r'/jobs/([A-Za-z0-9_-]{8,80})(/audio)?', path)
                if not match:
                    return self.reply(404, {'error': 'Unknown worker endpoint.'})
                job = store.get(match[1])
                if match[2]:
                    if job['status'] != 'completed':
                        return self.reply(409, {'error': 'Audio is not ready.'})
                    return self.reply(200, (store.root / 'jobs' / match[1] / 'audio.wav').read_bytes(), 'audio/wav')
                self.reply(200, job)
            except FileNotFoundError:
                self.reply(404, {'error': 'Unknown audition ID.'})

        def do_POST(self):
            if not self.permitted():
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 <= length <= MAX_BODY:
                    return self.reply(413, {'error': 'Request exceeds audition upload limit.'})
                path = urlparse(self.path).path
                if path == '/jobs':
                    body = json.loads(self.rfile.read(length))
                    model = body.get('model') if isinstance(body, dict) else None
                    if model in MODES and not supervisor.prepared(model):
                        return self.reply(503, {'error': 'This model has not been prepared on the voice worker.'})
                    return self.reply(200, store.submit(body))
                match = re.fullmatch(r'/jobs/([A-Za-z0-9_-]{8,80})/cancel', path)
                if match:
                    return self.reply(200, supervisor.cancel(match[1]))
                self.reply(404, {'error': 'Unknown worker endpoint.'})
            except Conflict as error:
                self.reply(409, {'error': str(error)})
            except (ValueError, TypeError) as error:
                self.reply(400, {'error': str(error)})
            except FileNotFoundError:
                self.reply(404, {'error': 'Unknown audition ID.'})
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default='/workspace/questiontemplate-voice-lab')
    parser.add_argument('--port', type=int, default=18765)
    parser.add_argument('--token-file', required=True)
    parser.add_argument('--hourly-usd', type=float)
    args = parser.parse_args()
    token = Path(args.token_file).read_text().strip()
    if not re.fullmatch('[A-Za-z0-9_-]{32,256}', token):
        parser.error('Use a private random bearer token of at least 32 characters.')
    if args.hourly_usd is not None and (not math.isfinite(args.hourly_usd) or args.hourly_usd <= 0):
        parser.error('Hourly rate must be the positive quoted rate, or omitted when unknown.')
    root = Path(args.root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    store = Store(root)
    supervisor = Supervisor(root, store, args.hourly_usd)
    threading.Thread(target=supervisor.loop, daemon=True).start()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler(store, supervisor, token))
    try:
        server.serve_forever()
    finally:
        supervisor.shutdown.set()
        supervisor.stop_model()
        server.server_close()


if __name__ == '__main__':
    main()
