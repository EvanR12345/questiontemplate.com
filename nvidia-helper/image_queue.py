"""Durable, single-GPU image queue. No model imports or additional environment."""
import hashlib
import json
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import traceback
from pathlib import Path


class Canceled(Exception):
    pass


class YieldAudio(Exception):
    pass


def action_text(data):
    return str(data.get('action_prompt') or data.get('prompt', '')).split('.', 1)[0].strip(' *"\u201c\u201d')


def sound_only(text):
    return bool(re.fullmatch(r'(?:bang|boom|crash|pow|thud|(?:a+|o+|e+)c*k*h*|screams?|gasps?)[.!?\s*-]*', re.sub(r'["\u201c\u201d\x27]', '', text.strip()), re.I))


def human_subjects(text):
    plurals = {'men':'man', 'women':'woman', 'boys':'boy', 'girls':'girl', 'people':'person', 'children':'child'}
    return {plurals.get(word, word) for word in re.findall(r'\b(?:man|men|woman|women|boy|boys|girl|girls|person|people|crowd|child|children)\b', text.lower())}


def validate_job(data):
    if not isinstance(data, dict) or not str(data.get('prompt', '')).strip():
        raise ValueError('Every image needs a prompt.')
    if len(str(data['prompt'])) > 12000:
        raise ValueError('Image prompt exceeds 12,000 characters.')
    if data.get('kind') == 'panel' and sound_only(action_text(data)):
        raise ValueError('This panel contains only a sound effect. In Layout, describe the visible action (who does what) and put Bang/screams in SFX. No image was queued.')
    for key, low, high in [('width', 384, 768), ('height', 384, 768), ('steps', 8, 50), ('guidance', 1, 14), ('reference_strength', 0, 1), ('denoising_strength', .1, 1)]:
        if key in data:
            n = data[key]
            if isinstance(n, bool) or not isinstance(n, (float, int)) or not math.isfinite(n) or not low <= n <= high:
                raise ValueError(f'{key} must be between {low} and {high}.')
    if data.get('operation', 'generate') not in ('generate', 'inpaint'):
        raise ValueError('Unsupported image operation.')
    if data.get('model') not in (None, 'sd15', 'dreamshaper8'):
        raise ValueError('Choose a supported local image model.')
    if 'cast_ids' in data and (not isinstance(data['cast_ids'], list) or len(data['cast_ids']) > 20 or any(not isinstance(value, str) or len(value) > 100 for value in data['cast_ids'])):
        raise ValueError('Invalid panel cast selection.')
    if 'continuity_target' in data and (not isinstance(data['continuity_target'], str) or len(data['continuity_target']) > 100):
        raise ValueError('Invalid scene reference target.')
    if data.get('operation') == 'inpaint' and not (data.get('image') and data.get('mask')):
        raise ValueError('Inpainting needs an image and mask.')
    if not isinstance(data.get('reference_images', []), list) or len(data.get('reference_images', [])) > 3:
        raise ValueError('Use at most three reference images.')
    if 'seed' in data and (isinstance(data['seed'], bool) or not isinstance(data['seed'], int) or not 0 <= data['seed'] < 2**32):
        raise ValueError('Seed must be an integer from 0 to 4294967295.')
    return dict(data)


class ImageQueue:
    def __init__(self, root, generate, gpu_lock, before_image=lambda: None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / 'assets').mkdir(exist_ok=True)
        self.db = sqlite3.connect(self.root / 'queue.sqlite3', check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, batch TEXT, project TEXT, target TEXT, kind TEXT, payload TEXT, state TEXT, step INTEGER DEFAULT 0, steps INTEGER DEFAULT 0, message TEXT DEFAULT \'\', seconds REAL DEFAULT 0, seed INTEGER, created REAL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
        recovered = self.db.execute("UPDATE jobs SET state='queued', step=0, message='Helper restarted; this unfinished image will restart with its saved seed.' WHERE state='running'").rowcount
        row = self.db.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        self.paused = recovered > 0 or (row is not None and row[0] == '1')
        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('paused',?)", ('1' if self.paused else '0',))
        self.db.commit()
        self.cv = threading.Condition(threading.RLock())
        self.generate, self.gpu_lock, self.before_image = generate, gpu_lock, before_image
        self.current = None
        self.cancel_requested = False
        self.pause_seconds = 0
        self.failure_streak = 0
        self.yield_audio = False
        self.closed = False
        self.thread = threading.Thread(target=self._worker, daemon=True, name='image-queue')
        self.thread.start()

    def _asset(self, value):
        if not isinstance(value, str) or not value.startswith('data:image/'):
            raise ValueError('References, source images and masks must be image data URLs.')
        name = hashlib.sha256(value.encode()).hexdigest() + '.txt'
        path = self.root / 'assets' / name
        if not path.exists():
            path.write_text(value, encoding='utf-8')
        return {'asset': name}

    def _expand(self, data, project=''):
        def get(value):
            return (self.root / 'assets' / value['asset']).read_text(encoding='utf-8') if isinstance(value, dict) else value
        data['reference_images'] = [get(x) for x in data.get('reference_images', [])]
        for key in ('image', 'mask'):
            if key in data:
                data[key] = get(data[key])
        target = data.get('continuity_target')
        if target and data.get('operation') != 'inpaint':
            with self.cv:
                anchor = self.db.execute("SELECT id,payload FROM jobs WHERE project=? AND target=? AND kind='panel' AND state='completed' ORDER BY rowid DESC LIMIT 1", (project, target)).fetchone()
            if anchor:
                anchor_data = json.loads(anchor['payload'])
                if sound_only(action_text(anchor_data)):
                    data['continuity_skipped'] = 'Scene anchor contains only a sound effect.'
                    return data
                current_cast, anchor_cast = set(data.get('cast_ids', [])), set(anchor_data.get('cast_ids', []))
                incompatible = current_cast != anchor_cast if current_cast or anchor_cast else human_subjects(action_text(anchor_data)) != human_subjects(action_text(data))
                if incompatible:
                    data['continuity_skipped'] = 'Scene anchor has different foreground subjects.'
                    return data
                import base64
                reference = 'data:image/png;base64,' + base64.b64encode(self.result_path(anchor['id']).read_bytes()).decode()
                if len(data['reference_images']) < 3:
                    data['scene_reference_only'] = not data['reference_images']
                    data['reference_images'].append(reference)
                    data['continuity_used'] = True
                    if data['scene_reference_only']:
                        data['reference_strength'] = min(float(data.get('reference_strength', .25)), .25)
        return data

    def enqueue(self, body):
        items = body.get('jobs')
        if not isinstance(items, list) or not 1 <= len(items) <= 1000:
            raise ValueError('Submit between 1 and 1000 images.')
        # Validate the whole submission before accepting any jobs.
        items = [validate_job(item) for item in items]
        batch = secrets.token_hex(12)
        with self.cv:
            pending = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
            if pending + len(items) > 1000:
                raise ValueError('The queue already has work. Maximum outstanding images: 1000.')
            rows = []
            for data in items:
                data.setdefault('seed', secrets.randbelow(2**31 - 1))
                data['reference_images'] = [self._asset(x) for x in data.get('reference_images', [])]
                for key in ('image', 'mask'):
                    if key in data:
                        data[key] = self._asset(data[key])
                rows.append((secrets.token_hex(16), batch, str(body.get('project', 'default'))[:128], str(data.get('target', ''))[:128], str(data.get('kind', 'panel'))[:32], json.dumps(data), 'queued', data['seed'], time.time()))
            with self.db:
                self.db.executemany('INSERT INTO jobs (id,batch,project,target,kind,payload,state,seed,created) VALUES (?,?,?,?,?,?,?,?,?)', rows)
            self.cv.notify_all()
        return {'batch': batch, 'accepted': len(rows)}

    def control(self, action):
        with self.cv:
            if action == 'pause':
                self.paused = True
            elif action == 'resume':
                self.paused = False
                self.yield_audio = False
            elif action == 'yield-audio':
                self.paused = True
                self.yield_audio = bool(self.current)
            elif action in ('cancel-current', 'cancel-all'):
                if self.current:
                    self.cancel_requested = True
                if action == 'cancel-all':
                    self.db.execute("UPDATE jobs SET state='canceled', message='Canceled by user.' WHERE state='queued'")
                    self.paused = False
            elif action == 'retry-failed':
                pending = self.db.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running','failed')").fetchone()[0]
                if pending > 1000:
                    raise ValueError('Retry would exceed 1000 pending images.')
                self.db.execute("UPDATE jobs SET state='queued',step=0,message='' WHERE state='failed'")
            else:
                raise ValueError('Unknown queue control.')
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('paused',?)", ('1' if self.paused else '0',))
            self.db.commit()
            self.cv.notify_all()
        return self.snapshot()

    def checkpoint(self, step=0, steps=0, message=''):
        with self.cv:
            self.db.execute('UPDATE jobs SET step=?,steps=?,message=? WHERE id=?', (step, steps, message, self.current))
            self.db.commit()
            wait_start = time.perf_counter()
            while self.paused and not self.cancel_requested and not self.yield_audio and not self.closed:
                self.cv.wait(timeout=1)
            self.pause_seconds += time.perf_counter() - wait_start
            if self.cancel_requested:
                raise Canceled('Image canceled; completed images are saved.')
            if self.yield_audio or self.closed:
                raise YieldAudio('Paused for Audio; resume restarts this image with its saved seed.')

    def snapshot(self):
        with self.cv:
            rows = self.db.execute('SELECT id,batch,project,target,kind,state,step,steps,message,seconds,seed FROM jobs ORDER BY created,rowid').fetchall()
            counts = {state: sum(row['state'] == state for row in rows) for state in ('queued', 'running', 'completed', 'failed', 'canceled')}
            recent = self.db.execute("SELECT seconds FROM jobs WHERE state='completed' AND seconds>0 ORDER BY rowid DESC LIMIT 20").fetchall()
            average = sum(row[0] for row in recent) / len(recent) if recent else None
            jobs = [dict(row) for row in rows]
            return {'paused': self.paused, 'canceling': self.cancel_requested, 'current': self.current, 'counts': counts, 'etaSeconds': None if average is None else average * (counts['queued'] + counts['running']), 'jobs': jobs, 'outputDirectory': str(self.root)}

    def result_path(self, job_id):
        if len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
            raise ValueError('Invalid image ID.')
        with self.cv:
            row = self.db.execute('SELECT state FROM jobs WHERE id=?', (job_id,)).fetchone()
            if not row or row[0] != 'completed':
                raise ValueError('This image is not completed.')
        return self.root / (job_id + '.png')

    def _worker(self):
        while True:
            with self.cv:
                if self.closed:
                    return
                row = None if self.paused else self.db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY rowid LIMIT 1").fetchone()
                if row is None:
                    self.cv.wait(timeout=1)
                    continue
                # Do not reserve a job until the audio pipeline releases the GPU.
                if not self.gpu_lock.acquire(blocking=False):
                    self.cv.wait(timeout=.2)
                    continue
                self.current, self.cancel_requested = row['id'], False
                self.yield_audio = False
                self.pause_seconds = 0
                self.db.execute("UPDATE jobs SET state='running',message='Preparing model' WHERE id=?", (row['id'],))
                self.db.commit()
            temporary = self.root / (row['id'] + '.png.tmp')
            try:
                self.checkpoint(message='Loading model (first use can take longer)')
                self.before_image()
                data = self._expand(json.loads(row['payload']), row['project'])
                result = self.generate(data, self.checkpoint)
                result['seconds'] = max(.001, result['seconds'] - self.pause_seconds)
                self.checkpoint(message='Saving PNG')
                result['pil'].save(temporary, format='PNG', compress_level=2)
                with self.cv:
                    if self.cancel_requested:
                        raise Canceled('Canceled before saving.')
                    if self.closed or self.yield_audio:
                        raise YieldAudio('Interrupted before saving; resume restarts this image.')
                    os.replace(temporary, self.root / (row['id'] + '.png'))
                    metadata = {k: v for k, v in result.items() if k != 'pil'}
                    metadata.update({'job':row['id'], 'target':row['target'], 'kind':row['kind'], 'request':data})
                    # References already exist in the asset store; avoid duplicate large metadata.
                    metadata['request'] = json.loads(row['payload'])
                    (self.root / (row['id'] + '.json')).write_text(json.dumps(metadata, indent=2), encoding='utf-8')
                    self.db.execute("UPDATE jobs SET state='completed',seconds=?,seed=?,message='' WHERE id=?", (result['seconds'], result['seed'], row['id']))
                    self.db.commit()
                    self.failure_streak = 0
            except YieldAudio as error:
                with self.cv:
                    self.db.execute("UPDATE jobs SET state='queued',step=0,message=? WHERE id=?", (str(error), row['id']))
                    self.db.commit()
            except Canceled as error:
                with self.cv:
                    self.db.execute("UPDATE jobs SET state='canceled',message=? WHERE id=?", (str(error), row['id']))
                    self.db.commit()
            except Exception as error:
                traceback.print_exc()
                message = str(error) or type(error).__name__
                if 'out of memory' in message.lower():
                    message = 'GPU memory exhausted. Use the Volume preset or 512×512, close GPU apps, and retry failed images.'
                with self.cv:
                    self.db.execute("UPDATE jobs SET state='failed',message=? WHERE id=?", (message[:1000], row['id']))
                    # Systemic failures must not burn through a large batch unattended.
                    self.failure_streak += 1
                    if self.failure_streak >= 3:
                        self.paused = True
                        self.db.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
                    self.db.commit()
            finally:
                temporary.unlink(missing_ok=True)
                self.gpu_lock.release()
                with self.cv:
                    self.current, self.cancel_requested = None, False
                    self.yield_audio = False
                    self.cv.notify_all()

    def close(self):
        with self.cv:
            if self.closed:
                return
            self.closed = True
            self.db.execute("INSERT OR REPLACE INTO settings VALUES ('paused','1')")
            self.db.commit()
            self.cv.notify_all()
        self.thread.join(timeout=5)
        if not self.thread.is_alive():
            self.db.close()
