"""Private, persistent auditions; deliberately separate from production voices.

The helper owns references and Opus previews. A private worker owns inference.
An interrupted submission is reconciled by its original ID, never blindly retried.
"""
import base64
import binascii
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler
import uuid

from studio_audio import probe, executable, OpusWriter, _flags
from cost_control import atomic_replace

MAX_REFERENCE_BYTES = 12 * 1024 * 1024
MAX_OUTPUT_BYTES = 40 * 1024 * 1024
ACTIVE = ('QUEUED', 'SUBMITTING', 'UNKNOWN', 'RUNNING', 'CANCEL_REQUESTED')
IDENTITY = re.compile(r'^[a-f0-9]{32}$')


def _mode(id, label, reference=False, transcript=False, description=False):
    return dict(id=id, label=label, requiresReference=reference,
                requiresTranscript=transcript, requiresDescription=description)


def _parameter(id, label, low, high, step, default):
    return dict(id=id, label=label, type='integer' if step == 1 else 'number', min=low, max=high, step=step, default=default)


MODELS = [
    dict(id='drama3', name='Fish Audio Drama 3', checkpoint='fishaudio/drama-3',
         modes=[_mode('default', 'Unconditioned voice'), _mode('clone', 'Reference voice', True, True),
                _mode('design', 'Describe a voice', description=True)],
         languages=['Auto'], parameters=[_parameter('temperature', 'Temperature', .1, 2, .05, 1),
             _parameter('top_p', 'Top P', .1, 1, .05, .9), _parameter('top_k', 'Top K (0 = all candidates)', 0, 4097, 1, 0)],
         notes='Language follows the script. Controls use bounded audition ranges. Voice reference permissions are required.'),
    dict(id='voxcpm2', name='VoxCPM2', checkpoint='openbmb/VoxCPM2',
         modes=[_mode('default', 'Unconditioned voice'), _mode('design', 'Describe a voice', description=True),
                _mode('clone', 'Reference voice', True) | {'allowsDescription': True}], languages=['Auto'],
         parameters=[_parameter('cfg_value', 'Guidance', .1, 10, .1, 2),
             _parameter('inference_timesteps', 'Inference steps', 1, 50, 1, 10),
             _parameter('seed', 'Seed', 0, 2147483647, 1, 42),
             dict(id='clone_strategy', label='Clone strategy', type='select', default='auto', options=[
                 dict(value='auto', label='Automatic'), dict(value='reference', label='Reference only'),
                 dict(value='prompt_reference', label='Prompt + reference')])],
         notes='Language follows the script. Prompt + reference requires a transcript and uses the recording for both inputs; voice descriptions are unavailable with that strategy. Automatic retry is disabled for predictable audition cost.'),
    dict(id='qwen3', name='Qwen3-TTS 1.7B',
         checkpoint='Qwen/Qwen3-TTS-12Hz-1.7B-Base + Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign + Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice',
         modes=[_mode('design', 'Describe a voice', description=True),
                _mode('clone', 'Reference voice', True, True),
                _mode('preset', 'Built-in Qwen voices') | {'allowsDescription': True}],
         languages=['English', 'Chinese', 'Japanese', 'Korean', 'German', 'French', 'Russian',
                    'Portuguese', 'Spanish', 'Italian'],
         parameters=[_parameter('seed', 'Seed', 0, 2147483647, 1, 42),
             dict(id='speaker', label='Built-in voice', type='select', default='vivian', modes=['preset'], options=[
                 dict(value='vivian', label='Vivian'), dict(value='serena', label='Serena'),
                 dict(value='uncle_fu', label='Uncle Fu'), dict(value='dylan', label='Dylan'),
                 dict(value='eric', label='Eric'), dict(value='ryan', label='Ryan'),
                 dict(value='aiden', label='Aiden'), dict(value='ono_anna', label='Ono Anna'),
                 dict(value='sohee', label='Sohee')]),
             dict(id='do_sample', label='Sample main voice tokens', type='boolean', default=True),
             _parameter('temperature', 'Temperature', .1, 2, .05, .9),
             _parameter('top_p', 'Top P', .1, 1, .05, 1), _parameter('top_k', 'Top K', 1, 100, 1, 50),
             _parameter('repetition_penalty', 'Repetition penalty', .8, 2, .05, 1.05),
             dict(id='subtalker_dosample', label='Sample subtalker tokens', type='boolean', default=True),
             _parameter('subtalker_temperature', 'Subtalker temperature', .1, 2, .05, .9),
             _parameter('subtalker_top_p', 'Subtalker Top P', .1, 1, .05, 1),
             _parameter('subtalker_top_k', 'Subtalker Top K', 1, 100, 1, 50)],
         notes='Base handles cloning; VoiceDesign creates described voices; CustomVoice provides nine built-in voices with optional style instructions. Controls use bounded audition ranges. Chinese is the model language label; Cantonese support is not verified.'),
]


class WorkerUnavailable(ValueError):
    pass


class WorkerNotFound(WorkerUnavailable):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class WorkerClient:
    """Credentials are sent only to an explicitly configured loopback tunnel."""
    def __init__(self, config):
        self.config = config

    def request(self, path, body=None, binary=False):
        value = self.config.get('voiceLabWorkerURL', '')
        parsed = urlparse(value)
        if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in ('', '/')):
            raise WorkerUnavailable('Voice Lab worker is not configured. Start its private RunPod tunnel first.')
        try:
            token = Path(self.config.get('voiceLabWorkerTokenFile', '')).read_text(encoding='utf-8-sig').strip()
            if not 24 <= len(token) <= 512 or any(c.isspace() for c in token):
                raise ValueError()
        except (OSError, ValueError):
            raise WorkerUnavailable('Voice Lab private worker credentials are unavailable.') from None
        data = None if body is None else json.dumps(body).encode()
        request = Request(value.rstrip('/') + path, data=data,
                          headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            # No proxy environment or redirects may forward the worker credential.
            with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=4) as response:
                limit = MAX_OUTPUT_BYTES if binary else 1024 * 1024
                raw = response.read(limit + 1)
                if len(raw) > limit:
                    raise WorkerUnavailable('Voice worker returned an oversized response.')
                return raw if binary else json.loads(raw)
        except HTTPError as error:
            if error.code == 404:
                raise WorkerNotFound('This audition is not present on the voice worker.') from None
            raise WorkerUnavailable('Voice worker rejected the request. Check its private connection and model status.') from None
        except (URLError, OSError, TimeoutError, ValueError):
            raise WorkerUnavailable('Voice worker is offline or still starting. Reconnect the private RunPod tunnel.') from None


def _number(value, low=0, high=86400):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high else None


class VoiceLab:
    def __init__(self, root, config, client=None):
        self.root = (Path(root) / '.voice-lab').resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.config = config
        self.client = client or WorkerClient(config)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.stop = threading.Event()
        self.thread = None
        self.db = sqlite3.connect(self.root / 'auditions.sqlite', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY, status TEXT NOT NULL,
                payload TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                metrics TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '');
            CREATE TABLE IF NOT EXISTS refs(id TEXT PRIMARY KEY, name TEXT NOT NULL,
                transcript TEXT NOT NULL, duration REAL NOT NULL, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        ''')
        # Add provenance without rebuilding or replacing existing reference rows.
        columns = {r['name'] for r in self.db.execute('PRAGMA table_info(refs)')}
        for name, definition in (
                ('source_kind', "TEXT NOT NULL DEFAULT 'upload'"),
                ('source_job', 'TEXT'), ('source_model', 'TEXT'), ('source_mode', 'TEXT'),
                ('source_sha256', 'TEXT')):
            if name not in columns:
                self.db.execute('ALTER TABLE refs ADD COLUMN ' + name + ' ' + definition)
        self.db.execute('CREATE UNIQUE INDEX IF NOT EXISTS refs_source_job ON refs(source_job) WHERE source_job IS NOT NULL')
        self.db.execute("UPDATE jobs SET status='UNKNOWN',error=? WHERE status='SUBMITTING'",
                        ('Submission was interrupted. Checking the original worker job; it will not be resubmitted.',))
        self.db.commit()
        if self.db.execute("SELECT 1 FROM jobs WHERE status IN ('QUEUED','UNKNOWN','RUNNING','CANCEL_REQUESTED') LIMIT 1").fetchone():
            self._start()

    def _start(self):
        with self.lock:
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._run, name='voice-lab', daemon=True)
                self.thread.start()
            self.wake.set()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread:
            self.thread.join(6)
        if not self.thread or not self.thread.is_alive():
            self.db.close()

    def _id(self, value):
        if not isinstance(value, str) or not IDENTITY.fullmatch(value):
            raise ValueError('Choose a valid Voice Lab item.')
        return value

    def _get(self, id):
        row = self.db.execute('SELECT * FROM jobs WHERE id=?', (self._id(id),)).fetchone()
        if row is None:
            raise ValueError('Voice Lab audition was not found.')
        return row

    def _view(self, row):
        payload = json.loads(row['payload'])
        return dict(id=row['id'], status=row['status'], model=payload['model'], mode=payload['mode'],
                    text=payload['text'], language=payload['language'], description=payload['description'],
                    character=payload.get('character'),
                    referenceId=payload.get('referenceId'), parameters=payload['parameters'],
                    createdAt=row['created'], updatedAt=row['updated'], metrics=json.loads(row['metrics']),
                    error=row['error'], audioReady=row['status'] == 'COMPLETED' and (self.root / (row['id'] + '.opus')).is_file())

    def health(self):
        try:
            value = self.client.request('/health')
            if not isinstance(value, dict):
                raise WorkerUnavailable('Invalid worker health response.')
            ready = value.get('ready') is True
            prepared = {m['id']: m.get('ready') is True for m in value.get('models', [])
                        if isinstance(m, dict) and m.get('id') in [model['id'] for model in MODELS]}
            return dict(ready=ready, reason='' if ready else 'Voice worker is starting or its models need setup.',
                        model=value.get('activeModel') if value.get('activeModel') in [m['id'] for m in MODELS] else None,
                        startupSeconds=_number(value.get('startupSeconds')), models=prepared,
                        hourlyUSD=_number(self.config.get('voiceLabHourlyUSD'), 0, 100))
        except WorkerUnavailable as error:
            return dict(ready=False, reason=str(error), model=None, startupSeconds=None, models={},
                        hourlyUSD=_number(self.config.get('voiceLabHourlyUSD'), 0, 100))

    def snapshot(self):
        worker = self.health()
        with self.lock:
            jobs = [self._view(r) for r in self.db.execute('SELECT * FROM jobs ORDER BY created DESC LIMIT 200')]
            references = [self._reference_view(r)
                          for r in self.db.execute('SELECT * FROM refs ORDER BY created DESC LIMIT 100')]
            selected = self.db.execute("SELECT value FROM settings WHERE key='selected'").fetchone()
        models = [copy.deepcopy(m) | {'ready': worker['ready'] and worker['models'].get(m['id']) is True} for m in MODELS]
        return dict(models=models, worker=worker, jobs=jobs, references=references,
                    selected=json.loads(selected['value']) if selected else None,
                    limits=dict(maxTextCharacters=2500, maxReferenceSeconds=60, maxQueuedJobs=6),
                    codec=dict(name='Opus', bitrateKbps=128))

    def job(self, id):
        with self.lock:
            return self._view(self._get(id))

    def _reference_view(self, row):
        source = dict(kind=row['source_kind'])
        for column, key in (('source_job', 'jobId'), ('source_model', 'model'),
                            ('source_mode', 'mode'), ('source_sha256', 'sha256')):
            if row[column] is not None:
                source[key] = row[column]
        return dict(id=row['id'], name=row['name'], transcript=row['transcript'],
                    durationSeconds=row['duration'], createdAt=row['created'], source=source,
                    preferredDuration=25 <= row['duration'] <= 30,
                    audioReady=(self.root / (row['id'] + '.opus')).is_file())

    def _reference_row(self, id):
        row = self.db.execute('SELECT * FROM refs WHERE id=?', (self._id(id),)).fetchone()
        if row is None:
            raise ValueError('Voice Lab reference was not found.')
        return row

    def reference_audio(self, id):
        with self.lock:
            row = self._reference_row(id)
            path = self.root / (row['id'] + '.opus')
            if not path.is_file():
                raise ValueError('This saved reference audio is unavailable.')
            return path

    def reference_from_job(self, body):
        if body.get('rightsConfirmed') is not True:
            raise ValueError('Confirm permission to save and reuse this voice as a reference.')
        name = body.get('name', 'Saved audition voice')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError('Reference name must contain 1 to 160 characters.')
        if 'transcript' in body:
            raise ValueError('A saved audition uses its original script as the reference transcript.')
        with self.lock:
            row = self._get(body.get('jobId'))
            payload = json.loads(row['payload'])
            if row['status'] != 'COMPLETED' or payload['model'] not in [m['id'] for m in MODELS]:
                raise ValueError('Choose a completed audition from a Voice Lab model.')
            path = self.root / (row['id'] + '.opus')
            if not path.is_file() or not 0 < path.stat().st_size <= MAX_REFERENCE_BYTES:
                raise ValueError('The completed audition audio is unavailable or too large for a reference.')
            info = probe(path, self.config.get('ffmpeg'))
            if not 1 <= info['duration'] <= 60:
                raise ValueError('The complete audition must be 1 to 60 seconds to save as a reference. It will not be trimmed.')
            existing = self.db.execute('SELECT * FROM refs WHERE source_job=?', (row['id'],)).fetchone()
            if existing:
                return self._reference_view(existing)
            if self.db.execute('SELECT count(*) FROM refs').fetchone()[0] >= 100:
                raise ValueError('The audition library has reached its 100-reference limit.')
            raw = path.read_bytes()
            id = uuid.uuid4().hex
            temporary, target = self.root / (id + '.writing'), self.root / (id + '.opus')
            try:
                # Preserve the entire generated recording and its exact script.
                # It is not cropped, stretched, transcoded, or regenerated.
                temporary.write_bytes(raw)
                atomic_replace(temporary, target)
                self.db.execute('''INSERT INTO refs(id,name,transcript,duration,created,
                    source_kind,source_job,source_model,source_mode,source_sha256)
                    VALUES(?,?,?,?,?,?,?,?,?,?)''', (id, name.strip(), payload['text'], info['duration'],
                    time.time(), 'audition', row['id'], payload['model'], payload['mode'], hashlib.sha256(raw).hexdigest()))
                self.db.commit()
            except Exception:
                self.db.rollback()
                target.unlink(missing_ok=True)
                raise
            finally:
                temporary.unlink(missing_ok=True)
            return self._reference_view(self._reference_row(id))

    def _decode(self, body):
        encoded = body.get('audioBase64')
        if not isinstance(encoded, str) or not encoded or len(encoded) > ((MAX_REFERENCE_BYTES + 2) // 3) * 4:
            raise ValueError('Choose a reference audio file up to 12 MB.')
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise ValueError('Reference audio encoding is invalid.') from None
        if not data or len(data) > MAX_REFERENCE_BYTES:
            raise ValueError('Choose a reference audio file up to 12 MB.')
        return data

    def reference(self, body):
        if body.get('rightsConfirmed') is not True:
            raise ValueError('Confirm permission to use the reference voice before uploading.')
        name = body.get('name', 'Voice reference')
        transcript = body.get('transcript', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 160:
            raise ValueError('Reference name must contain 1 to 160 characters.')
        if not isinstance(transcript, str) or len(transcript) > 4000:
            raise ValueError('Reference transcript must contain at most 4000 characters.')
        with self.lock:
            if self.db.execute('SELECT count(*) FROM refs').fetchone()[0] >= 100:
                raise ValueError('The audition library has reached its 100-reference limit.')
        data = self._decode(body)
        if not (data[:4] in (b'OggS', b'fLaC') or data[:3] == b'ID3'
                or data[:4] == b'RIFF' and data[8:12] == b'WAVE'
                or data[4:8] == b'ftyp' or len(data) > 1 and data[0] == 255 and data[1] & 224 == 224):
            raise ValueError('Choose WAV, MP3, Opus, FLAC, or M4A reference audio.')
        id = uuid.uuid4().hex
        temporary = self.root / (id + '.upload')
        target = self.root / (id + '.opus')
        try:
            temporary.write_bytes(data)
            # Bound decoded output as well as compressed upload size. This works
            # with the helper's FFmpeg-only install and distrusts container duration.
            result = subprocess.run([executable(self.config.get('ffmpeg')), '-v', 'error', '-nostdin',
                '-protocol_whitelist', 'file,pipe', '-i', str(temporary), '-vn', '-t', '61',
                '-ac', '1', '-ar', '24000', '-f', 's16le', 'pipe:1'], stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, timeout=60, **_flags())
            if result.returncode:
                raise ValueError('Reference audio could not be safely decoded.')
            seconds = len(result.stdout) / 2 / 24000
            if not 1 <= seconds <= 60:
                raise ValueError('Decoded reference must contain 1 to 60 seconds of audio.')
            with OpusWriter(target, self.config.get('ffmpeg')) as audio:
                audio.setparams((1, 2, 24000))
                audio.writeframes(result.stdout)
            info = probe(target, self.config.get('ffmpeg'))
            if not 1 <= info['duration'] <= 60:
                raise ValueError('Decoded reference must contain 1 to 60 seconds of audio.')
            with self.lock:
                if self.db.execute('SELECT count(*) FROM refs').fetchone()[0] >= 100:
                    raise ValueError('The audition library has reached its 100-reference limit.')
                self.db.execute('''INSERT INTO refs(id,name,transcript,duration,created,source_sha256)
                    VALUES(?,?,?,?,?,?)''', (id, name.strip(), transcript.strip(), info['duration'],
                    time.time(), hashlib.sha256(target.read_bytes()).hexdigest()))
                self.db.commit()
                return self._reference_view(self._reference_row(id))
        except (OSError, subprocess.SubprocessError):
            target.unlink(missing_ok=True)
            raise ValueError('Reference audio could not be safely decoded. Check FFmpeg and the audio file.') from None
        except Exception:
            target.unlink(missing_ok=True)
            raise
        finally:
            temporary.unlink(missing_ok=True)

    def enqueue(self, body):
        model = next((m for m in MODELS if m['id'] == body.get('model')), None)
        if not model:
            raise ValueError('Choose Drama 3, VoxCPM2, or Qwen3-TTS 1.7B.')
        mode = next((m for m in model['modes'] if m['id'] == body.get('mode')), None)
        if not mode:
            raise ValueError('Choose a supported mode for this model.')
        text, description = body.get('text'), body.get('description', '')
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2500:
            raise ValueError('Audition script must contain 1 to 2500 characters.')
        if not isinstance(description, str) or len(description) > 1000 or mode['requiresDescription'] and not description.strip():
            raise ValueError('Describe the voice in 1 to 1000 characters for voice design.')
        language = body.get('language', model['languages'][0])
        if language not in model['languages']:
            raise ValueError('Choose a supported model language.')
        supplied = body.get('parameters', {})
        if not isinstance(supplied, dict) or set(supplied) - {p['id'] for p in model['parameters']}:
            raise ValueError('Unsupported model control.')
        parameters = {}
        for p in model['parameters']:
            value = supplied.get(p['id'], p['default'])
            if p['type'] == 'boolean':
                if type(value) is not bool:
                    raise ValueError(p['label'] + ' must be enabled or disabled.')
            elif p['type'] == 'select':
                if not isinstance(value, str) or value not in [o['value'] for o in p['options']]:
                    raise ValueError('Choose a supported ' + p['label'].lower() + '.')
            elif _number(value, p['min'], p['max']) is None or p['step'] == 1 and int(value) != value:
                raise ValueError(p['label'] + ' is outside the audition limit.')
            parameters[p['id']] = value
        payload = dict(model=model['id'], mode=mode['id'], text=text.strip(), language=language,
                       description=description.strip(), parameters=parameters)
        if model['id'] == 'drama3' and mode['id'] == 'design':
            value = body.get('character', {})
            if not isinstance(value, dict) or set(value) - {'name', 'scene'}:
                raise ValueError('Character must contain only a name and optional scene.')
            name, scene = value.get('name', 'Narrator'), value.get('scene')
            if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 80 or ':' in name
                    or any(mark in name for mark in ('\n', '\r', '<|', '@@drama3-space@@'))):
                raise ValueError('Character name must be one line, 1 to 80 characters, without structural markers or a colon.')
            if scene is not None and (not isinstance(scene, str) or len(scene) > 500
                    or any(mark in scene for mark in ('\n', '\r', '<|', '@@drama3-space@@'))):
                raise ValueError('Scene must be one line of at most 500 characters without structural markers.')
            payload['character'] = dict(name=name.strip(), scene=scene.strip() or None if scene is not None else None)
        elif body.get('character') is not None:
            raise ValueError('Character name and scene apply only to Drama 3 voice design.')
        if mode['requiresReference']:
            ref_id = self._id(body.get('referenceId'))
            with self.lock:
                ref = self.db.execute('SELECT * FROM refs WHERE id=?', (ref_id,)).fetchone()
            if ref is None or not (self.root / (ref_id + '.opus')).is_file():
                raise ValueError('Upload a permitted reference voice first.')
            if mode['requiresTranscript'] and not ref['transcript'].strip():
                raise ValueError('This cloning mode requires the reference transcript.')
            if model['id'] == 'voxcpm2' and parameters['clone_strategy'] == 'prompt_reference':
                if not ref['transcript'].strip():
                    raise ValueError('Prompt + reference cloning requires the reference transcript.')
                if description.strip():
                    raise ValueError('Prompt + reference cloning cannot use a voice description. Choose Reference only for style control.')
            payload['referenceId'] = ref_id
        health = self.health()
        if not health['ready']:
            raise WorkerUnavailable(health['reason'])
        if health['models'].get(model['id']) is not True:
            raise WorkerUnavailable('This model is not prepared on the voice worker yet. Complete its setup before auditioning.')
        executable(self.config.get('ffmpeg'))
        id, now = uuid.uuid4().hex, time.time()
        with self.lock:
            count = self.db.execute("SELECT count(*) FROM jobs WHERE status IN ('QUEUED','SUBMITTING','UNKNOWN','RUNNING','CANCEL_REQUESTED')").fetchone()[0]
            if count >= 6:
                raise ValueError('Wait for or cancel an audition before adding more. Six auditions can be pending.')
            self.db.execute('INSERT INTO jobs(id,status,payload,created,updated) VALUES(?,?,?,?,?)',
                            (id, 'QUEUED', json.dumps(payload), now, now))
            self.db.commit()
        self._start()
        return self.job(id)

    def cancel(self, id):
        with self.lock:
            row = self._get(id)
            if row['status'] in ACTIVE:
                state = 'CANCELLED' if row['status'] == 'QUEUED' else 'CANCEL_REQUESTED'
                self._update(id, state, error='')
        self._start()
        return self.job(id)

    def select(self, body):
        with self.lock:
            row = self._get(body.get('jobId'))
            payload = json.loads(row['payload'])
            if row['status'] != 'COMPLETED' or payload['model'] != body.get('model'):
                raise ValueError('Choose a completed audition for this model.')
            selected = dict(model=payload['model'], jobId=row['id'], selectedAt=time.time())
            self.db.execute('INSERT OR REPLACE INTO settings VALUES(?,?)', ('selected', json.dumps(selected)))
            self.db.commit()
        return selected

    def audio(self, id):
        with self.lock:
            row = self._get(id)
            path = self.root / (row['id'] + '.opus')
            if row['status'] != 'COMPLETED' or not path.is_file():
                raise ValueError('This audition has no completed audio yet.')
            return path

    def _update(self, id, status, metrics=None, error=''):
        with self.lock:
            if metrics is None:
                self.db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?', (status, error, time.time(), id))
            else:
                self.db.execute('UPDATE jobs SET status=?,metrics=?,error=?,updated=? WHERE id=?',
                                (status, json.dumps(metrics), error, time.time(), id))
            self.db.commit()

    def _metrics(self, source, audio_seconds):
        if not isinstance(source, dict):
            source = {}
        metrics = {name: _number(source.get(name)) for name in ('generationSeconds', 'loadSeconds', 'startupSeconds')}
        metrics['audioSeconds'] = audio_seconds
        rate = _number(self.config.get('voiceLabHourlyUSD'), 0, 100)
        metrics.update(hourlyUSD=rate, warm30SecondsComputeUSD=None, modelLoad30SecondsComputeUSD=None, cold30SecondsComputeUSD=None,
                       costBasis='Compute estimate from measured clip time and configured hourly rate. Excludes downloads/setup, idle time, storage and transfer. Cold includes recorded worker startup and model load only.')
        gen, load, startup = metrics['generationSeconds'], metrics['loadSeconds'], metrics['startupSeconds']
        if rate is not None and gen is not None and audio_seconds > 0:
            metrics['warm30SecondsComputeUSD'] = rate * gen / audio_seconds * 30 / 3600
            if load is not None:
                metrics['modelLoad30SecondsComputeUSD'] = metrics['warm30SecondsComputeUSD'] + rate * load / 3600
            if load is not None and startup is not None:
                metrics['cold30SecondsComputeUSD'] = metrics['warm30SecondsComputeUSD'] + rate * (load + startup) / 3600
        return metrics

    def _finish(self, id, response):
        raw = self.client.request('/jobs/' + id + '/audio', binary=True)
        if not isinstance(raw, bytes) or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
            raise ValueError('Voice worker did not return WAV audio.')
        target = self.root / (id + '.opus')
        temporary = target.with_name(id + '.partial.opus')
        try:
            # Decode over stdin. No generated WAV master is saved to disk.
            result = subprocess.run([executable(self.config.get('ffmpeg')), '-v', 'error', '-nostdin', '-y',
                '-protocol_whitelist', 'pipe', '-i', 'pipe:0', '-vn', '-t', '121', '-ac', '1', '-c:a', 'libopus', '-b:a', '128k',
                '-vbr', 'on', '-f', 'opus', str(temporary)], input=raw, stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE, timeout=60, **_flags())
            if result.returncode:
                raise ValueError('Voice worker audio could not be decoded.')
            duration = probe(temporary, self.config.get('ffmpeg'))['duration']
            if not 0 < duration <= 120.1:
                raise ValueError('Voice worker audio exceeded the audition duration limit.')
            with self.lock:
                if self._get(id)['status'] == 'CANCEL_REQUESTED':
                    self._update(id, 'CANCELLED')
                    return
                temporary.replace(target)
                self._update(id, 'COMPLETED', self._metrics(response.get('metrics'), duration))
        finally:
            temporary.unlink(missing_ok=True)

    def tick(self):
        with self.lock:
            row = self.db.execute("SELECT * FROM jobs WHERE status IN ('QUEUED','SUBMITTING','UNKNOWN','RUNNING','CANCEL_REQUESTED') ORDER BY created LIMIT 1").fetchone()
        if row is None:
            return
        id, status = row['id'], row['status']
        try:
            if status == 'QUEUED':
                payload = json.loads(row['payload'])
                if payload.get('referenceId'):
                    with self.lock:
                        ref = self.db.execute('SELECT * FROM refs WHERE id=?', (payload['referenceId'],)).fetchone()
                    raw = (self.root / (ref['id'] + '.opus')).read_bytes()
                    payload['reference'] = dict(audioBase64=base64.b64encode(raw).decode(), mime='audio/ogg', transcript=ref['transcript'])
                payload['id'] = id
                with self.lock:
                    if self._get(id)['status'] != 'QUEUED':
                        return
                    self._update(id, 'SUBMITTING')
                try:
                    response = self.client.request('/jobs', payload)
                except WorkerUnavailable:
                    with self.lock:
                        if self._get(id)['status'] != 'CANCEL_REQUESTED':
                            self._update(id, 'UNKNOWN', error='Submission outcome is unknown. Checking its original ID without resubmitting.')
                    return
            else:
                response = self.client.request('/jobs/' + id)
            if not isinstance(response, dict) or response.get('id') != id:
                raise WorkerUnavailable('Voice worker job identity could not be verified.')
            with self.lock:
                cancelled = self._get(id)['status'] == 'CANCEL_REQUESTED'
            if cancelled:
                if response.get('status', '').lower() in ('completed', 'failed', 'cancelled'):
                    self._update(id, 'CANCELLED')
                else:
                    self.client.request('/jobs/' + id + '/cancel', {})
                return
            remote = response.get('status', '').lower()
            if remote == 'completed':
                self._finish(id, response)
            elif remote in ('failed', 'cancelled'):
                with self.lock:
                    if self._get(id)['status'] == 'CANCEL_REQUESTED':
                        self._update(id, 'CANCELLED')
                    else:
                        self._update(id, remote.upper(), error='Voice worker could not complete this audition. Check its private diagnostics.' if remote == 'failed' else '')
            elif remote in ('queued', 'running'):
                with self.lock:
                    if self._get(id)['status'] != 'CANCEL_REQUESTED':
                        self._update(id, 'RUNNING')
            else:
                raise WorkerUnavailable('Voice worker returned an unrecognized job state.')
        except WorkerNotFound:
            with self.lock:
                if self._get(id)['status'] == 'CANCEL_REQUESTED':
                    # No inference remains for this exact ID on this worker.
                    self._update(id, 'CANCELLED')
                else:
                    self._update(id, 'UNKNOWN', error='Original audition is absent on this worker. It will not be resubmitted automatically; verify worker recovery before creating a replacement.')
        except WorkerUnavailable:
            # Preserve running/unknown identity while the tunnel reconnects.
            return
        except (ValueError, OSError, subprocess.SubprocessError):
            with self.lock:
                if self._get(id)['status'] == 'CANCEL_REQUESTED':
                    self._update(id, 'CANCELLED')
                else:
                    self._update(id, 'FAILED', error='Audition output could not be validated or saved. Check FFmpeg and private worker diagnostics.')

    def _run(self):
        while not self.stop.is_set():
            self.wake.wait(2)
            self.wake.clear()
            if self.stop.is_set():
                break
            try:
                self.tick()
            except Exception:
                # No raw worker response, script, token, or private path enters logs.
                pass
