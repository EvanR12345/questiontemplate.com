"""Private R2 archives, immutable assets, atomic manifests and recoverable sync.

The helper's folders remain a working cache. No file is deleted by this module.
Secrets never enter project exports, health responses or signed URL reports.
"""
import copy
import hashlib
import json
import mimetypes
import re
import threading
import time
from pathlib import Path
from urllib.parse import quote

MEDIA = {'.png', '.jpg', '.jpeg', '.webp', '.wav', '.m4a', '.mp4', '.json', '.flac'}

def attachment_disposition(name):
    if not isinstance(name,str) or not name or len(name)>240 or any(ord(c)<32 or ord(c)==127 for c in name) or any(c in name for c in '\\/:') or name in ('.','..'):
        raise ValueError('Enter a plain download filename without folders or control characters.')
    fallback=re.sub(r'[^a-zA-Z0-9 ._-]','_',name)
    return 'attachment; filename="'+fallback+'"; filename*=UTF-8\'\''+quote(name,safe='')

def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda: f.read(4*1024*1024), b''): h.update(b)
    return h.hexdigest()

def stable_file(path):
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)

class S3Objects:
    provider = 'Cloudflare R2'
    publisher_compatible = True
    def __init__(self, config):
        import boto3
        from botocore.config import Config
        if not re.fullmatch(r'[a-f0-9]{32}', config.get('accountId', '')):
            raise ValueError('Enter a valid Cloudflare account ID.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', config.get('bucket', '')):
            raise ValueError('Enter a valid private bucket name.')
        self.bucket = config['bucket']
        self.client = boto3.client('s3', endpoint_url=f"https://{config['accountId']}.r2.cloudflarestorage.com",
            aws_access_key_id=config['accessKeyId'], aws_secret_access_key=config['secretAccessKey'],
            region_name='auto', config=Config(signature_version='s3v4', retries={'max_attempts':4,'mode':'standard'},
                connect_timeout=10, read_timeout=60, request_checksum_calculation='when_required',
                response_checksum_validation='when_required'))

    def read(self, key):
        try:
            r = self.client.get_object(Bucket=self.bucket, Key=key)
            try: return r['Body'].read(), r['ETag']
            finally: r['Body'].close()
        except self.client.exceptions.NoSuchKey: return None, None

    def put_json(self, key, value, etag=None, create=False):
        args = {'Bucket':self.bucket,'Key':key,'Body':json.dumps(value,ensure_ascii=False,separators=(',',':')).encode(),
                'ContentType':'application/json'}
        if etag: args['IfMatch'] = etag
        elif create: args['IfNoneMatch'] = '*'
        self.client.put_object(**args)

    def upload(self, key, path, checksum):
        from boto3.s3.transfer import TransferConfig
        self.client.upload_file(str(path), self.bucket, key,
            ExtraArgs={'ContentType':mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
                       'Metadata':{'sha256':checksum}},
            Config=TransferConfig(multipart_threshold=64*1024*1024,multipart_chunksize=64*1024*1024,max_concurrency=3))
        head = self.client.head_object(Bucket=self.bucket,Key=key)
        if head['ContentLength'] != path.stat().st_size or head.get('Metadata',{}).get('sha256') != checksum:
            raise ValueError('Cloud upload verification failed; local file retained.')

    def download(self, key, target):
        self.client.download_file(self.bucket, key, str(target))

    def open_object(self, key):
        """Streaming migration source: never creates a laptop download file."""
        r = self.client.get_object(Bucket=self.bucket, Key=key)
        return {'body':r['Body'], 'bytes':r['ContentLength'], 'etag':r['ETag'],
                'contentType':r.get('ContentType', 'application/octet-stream')}

    def keys(self, prefix):
        for page in self.client.get_paginator('list_objects_v2').paginate(Bucket=self.bucket,Prefix=prefix):
            for x in page.get('Contents',[]): yield x['Key']

    def url(self, key, download_name=None):
        params={'Bucket':self.bucket,'Key':key}
        if download_name is not None:params['ResponseContentDisposition']=attachment_disposition(download_name)
        return self.client.generate_presigned_url('get_object',Params=params,ExpiresIn=3600)

class R2Archive:
    def __init__(self, store, secret_path, objects=None):
        self.store = store; self.secret_path = Path(secret_path)
        self.state_path = store.root / 'cloud-sync.json'
        self.cv = threading.Condition(threading.RLock()); self.stopped = False
        self.pending = set(); self.states = {}; self.objects = objects
        self.configuration_error='';self.journal_error=''
        if self.state_path.exists():
            try:self.states = json.loads(self.state_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):self.journal_error='Cloud status journal could not be read. Local project data is unchanged; retry cloud save.'
        requested_provider = 'r2'
        if self.objects is None and self.secret_path.exists():
            try:
                config = json.loads(self.secret_path.read_text(encoding='utf-8'))
                requested_provider = config.get('provider', 'r2')
                if config.get('provider', 'r2') == 'gcs':
                    from google_storage import GCSObjects
                    self.objects = GCSObjects(config)
                elif config.get('provider', 'r2') == 'r2':self.objects = S3Objects(config)
                else:raise ValueError('Unsupported storage provider.')
            except Exception:self.configuration_error='Cloud storage configuration is unavailable. Local audio and projects remain usable; check the private storage credentials and SDK.'
        self.provider = getattr(self.objects, 'provider', 'Google Cloud Storage' if requested_provider == 'gcs' else 'Cloudflare R2')
        self.publisher_compatible = getattr(self.objects, 'publisher_compatible', requested_provider == 'r2')
        if self.provider == 'Google Cloud Storage':
            # Never display old R2 synchronization results as Google uploads.
            self.state_path = store.root / 'cloud-sync-gcs.json'
            self.states = {};self.journal_error = ''
            if self.state_path.exists():
                try:self.states = json.loads(self.state_path.read_text(encoding='utf-8'))
                except (OSError,ValueError):self.journal_error='Cloud status journal could not be read. Retry cloud save.'
        self.enabled = self.objects is not None
        self.manifests = {}
        self.asset_locks = [threading.RLock() for _ in range(32)]
        self.catalogue_lock = threading.RLock()
        self.catalogue = None; self.catalogue_at = 0
        if self.enabled:
            store.archive = self
            for p in store.list_local(): self.enqueue(p['id'])
        self.thread = threading.Thread(target=self.worker,daemon=True,name='studio-cloud-save')
        self.thread.start()

    def persist(self):
        temp = self.state_path.with_suffix('.tmp')
        try:
            temp.write_text(json.dumps(self.states,separators=(',',':')),encoding='utf-8'); temp.replace(self.state_path)
            self.journal_error=''
        except OSError:self.journal_error='Cloud status could not be saved locally. Project data is retained; check free space and retry cloud save.'

    def enqueue(self, pid):
        if not self.enabled: return
        with self.cv:
            self.pending.add(pid)
            self.states.setdefault(pid,{}).update(status='PENDING')
            self.persist(); self.cv.notify_all()

    def status(self, pid=None):
        with self.cv:
            return {'enabled':self.enabled,'provider':self.provider if self.enabled else 'Local helper',
                    'publisherCompatible':self.publisher_compatible,
                    'workingCache':True,'projects':copy.deepcopy(self.states if pid is None else {pid:self.states.get(pid,{})}),
                    'pending':len(self.pending),'error':self.configuration_error or self.journal_error}

    def manifest(self, pid, refresh=False):
        self.store.folder(pid)  # Validate stable identity before forming an object key.
        if refresh or pid not in self.manifests:
            data, etag = self.objects.read(f'studio/manifests/{pid}.json')
            self.manifests[pid] = (json.loads(data) if data else None, etag)
        return self.manifests[pid]

    def sync(self, pid):
        folder = self.store.folder(pid)
        with self.store.lock:
            project = self.store.load_local(pid)
            snapshot = (folder/'project.json').read_bytes()
        old, etag = self.manifest(pid, True)
        if old and old['project']['revision'] > project['revision']:
            raise ValueError('Cloud project is newer. Reload it before overwriting; both copies retained.')
        if old and old['project']['revision'] == project['revision'] and old['project'] != project:
            raise ValueError('Cloud and helper revisions conflict. Both copies retained for review.')
        records = dict(old.get('files',{})) if old else {}
        uploaded = 0
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in MEDIA: continue
            relative = path.relative_to(folder).as_posix()
            if any(x.startswith('.') or x=='working' for x in path.relative_to(folder).parts) or '.partial.' in relative or '.writing.' in relative: continue
            if path.name in ('project.json','revision.json','project.previous.json'): continue
            self.store.asset(pid, relative)  # Reject traversal and unsupported asset paths.
            before = stable_file(path); previous = records.get(relative,{})
            if previous.get('stamp') == list(before): continue
            checksum = sha(path)
            if stable_file(path) != before: raise ValueError('An asset changed during upload preparation. Retry safely.')
            key = f'studio/assets/{pid}/{checksum}/{relative}'
            if previous.get('sha256') != checksum:
                self.objects.upload(key, path, checksum); uploaded += before[0]
            if stable_file(path) != before: raise ValueError('An asset changed during upload. Previous cloud snapshot preserved.')
            records[relative] = {'key':key,'sha256':checksum,'bytes':before[0],'stamp':list(before)}
        manifest = {'version':1,'project':project,'files':records,'savedAt':time.time()}
        # All immutable assets finish before publishing a new loadable project.
        with self.store.lock:
            if (folder/'project.json').read_bytes() != snapshot:
                self.enqueue(pid); return False
        # Snapshot + immutable assets are already complete. Network requests
        # must not hold the project lock and stall edits/generation commits.
        # The cloud's condition still rejects concurrent remote revisions.
        if old and old['project']==project and old.get('files')==records:
            manifest['savedAt']=old['savedAt']
        else:
            from studio_data import digest
            history=digest({'project':project,'files':records})[:16]
            self.objects.put_json(f'studio/history/{pid}/{project["revision"]}-{history}.json',manifest)
            self.objects.put_json(f'studio/manifests/{pid}.json',manifest,etag,create=old is None)
        self.manifests.pop(pid,None)
        self.catalogue_at = 0
        with self.store.lock:
            changed = (folder/'project.json').read_bytes() != snapshot
            with self.cv:
                self.states[pid] = {'status':'PENDING' if changed else 'SYNCED','revision':project['revision'],'savedAt':manifest['savedAt'],
                    'files':len(records),'bytes':sum(x['bytes'] for x in records.values()),'lastUploadBytes':uploaded,'error':''}
                self.persist()
            if changed:self.enqueue(pid)
        return not changed

    def restore(self, pid):
        manifest,_ = self.manifest(pid,True)
        if not manifest:
            from studio_data import ProjectNotFound
            raise ProjectNotFound('Project not found in cloud storage.')
        from studio_data import validate_project
        project = validate_project(manifest['project'])
        if project['id'] != pid: raise ValueError('Cloud project identity does not match.')
        folder = self.store.folder(pid); folder.mkdir(exist_ok=True)
        with self.store.lock:
            local=folder/'project.json'
            if local.exists():
                current=self.store.load_local(pid)
                if current['revision']>project['revision'] or (current['revision']==project['revision'] and current!=project):
                    raise ValueError('Unsaved or conflicting helper changes exist. Both copies retained; cloud reload was cancelled.')
            temp=folder/'project.cloud.tmp'; temp.write_text(json.dumps(project,ensure_ascii=False),encoding='utf-8')
            temp.replace(folder/'project.json')
            (folder/'revision.json').write_text(json.dumps({'revision':project['revision'],'updated':project['updated']}),encoding='utf-8')
        return project

    def asset_record(self, pid, name):
        """Verified immutable identity from the published manifest; no media download.

        This is metadata availability, not a new visual inspection. Actual reads
        still verify the downloaded bytes against this identity before use.
        """
        self.store.asset_local(pid,name) # Path and media-extension validation.
        if not self.enabled:return None
        manifest,_=self.manifest(pid)
        record=(manifest or {}).get('files',{}).get(name.replace('\\','/'))
        if record is None:return None
        if (not isinstance(record,dict) or not isinstance(record.get('sha256'),str) or not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256',''))
                or record.get('key') != f'studio/assets/{pid}/{record["sha256"]}/{name.replace(chr(92),"/")}'
                or isinstance(record.get('bytes'),bool) or not isinstance(record.get('bytes'),int)
                or record['bytes']<0):
            raise ValueError('Invalid cloud asset identity.')
        return dict(record)

    def fetch_asset(self, pid, name):
        target = self.store.asset_local(pid,name)
        # Simultaneous preview/render requests must share one restore rather
        # than overwrite each other's temporary file or an existing local edit.
        with self.asset_locks[hash((pid,name.replace('\\','/'))) % len(self.asset_locks)]:
            if target.exists(): return target
            return self._fetch_asset(pid,name,target)

    def _fetch_asset(self, pid, name, target):
        manifest,_ = self.manifest(pid)
        record = (manifest or {}).get('files',{}).get(name.replace('\\','/'))
        if not record: return target
        if not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256','')) or record.get('key') != f'studio/assets/{pid}/{record["sha256"]}/{name.replace(chr(92),"/")}':
            raise ValueError('Invalid cloud asset identity.')
        target.parent.mkdir(parents=True,exist_ok=True); tmp=target.with_suffix(target.suffix+'.cloud-partial')
        self.objects.download(record['key'],tmp)
        if tmp.stat().st_size != record['bytes'] or sha(tmp)!=record['sha256']:
            tmp.unlink(missing_ok=True); raise ValueError('Cloud asset checksum failed. Existing assets preserved.')
        tmp.replace(target); return target

    def media_url(self, pid, name, download_name=None):
        manifest,_=self.manifest(pid)
        record=(manifest or {}).get('files',{}).get(name.replace('\\','/'))
        local=self.store.asset_local(pid,name)
        if not record or (local.exists() and record.get('stamp')!=list(stable_file(local))): return None
        if not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256','')) or record.get('key') != f'studio/assets/{pid}/{record["sha256"]}/{name.replace(chr(92),"/")}':
            raise ValueError('Invalid cloud asset identity.')
        return self.objects.url(record['key'],download_name=download_name) if download_name is not None else self.objects.url(record['key'])

    def files(self, pid):
        """Credential-free dashboard data; URLs are created only on request."""
        if not self.enabled:
            folder=self.store.folder(pid); rows=[]
            for path in folder.rglob('*'):
                if not path.is_file() or path.is_symlink() or path.suffix.lower() not in MEDIA:continue
                name=path.relative_to(folder).as_posix()
                if any(x.startswith('.') or x=='working' for x in path.relative_to(folder).parts) or '.writing.' in name or '.partial.' in name:continue
                self.store.asset_local(pid,name)
                rows.append({'path':name,'bytes':path.stat().st_size,'cloud':False,
                    'category':{'mp4':'Video','wav':'Audio','m4a':'Audio','flac':'Audio','json':'Project data'}.get(path.suffix[1:].lower(),'Images')})
            return {'files':rows, 'cloud':False}
        manifest,_=self.manifest(pid)
        rows={}
        for name,r in (manifest or {}).get('files',{}).items():
            self.store.asset_local(pid,name)
            rows[name]={'path':name,'bytes':r['bytes'],'sha256':r['sha256'],'cloud':True,
                        'category': {'mp4':'Video','wav':'Audio','m4a':'Audio','flac':'Audio','json':'Project data'}.get(Path(name).suffix[1:].lower(),'Images')}
        # A cloud connection must not hide newly generated or edited files while
        # the immutable snapshot is still uploading. Show their local status.
        folder=self.store.folder(pid)
        for path in folder.rglob('*'):
            if not path.is_file() or path.is_symlink() or path.suffix.lower() not in MEDIA:continue
            name=path.relative_to(folder).as_posix()
            if any(x.startswith('.') or x=='working' for x in path.relative_to(folder).parts) or '.writing.' in name or '.partial.' in name:continue
            if path.name in ('project.json','revision.json','project.previous.json'):continue
            self.store.asset_local(pid,name)
            record=(manifest or {}).get('files',{}).get(name,{})
            if record.get('stamp')==list(stable_file(path)):continue
            rows[name]={'path':name,'bytes':path.stat().st_size,'cloud':False,
                        'category':{'mp4':'Video','wav':'Audio','m4a':'Audio','flac':'Audio','json':'Project data'}.get(path.suffix[1:].lower(),'Images')}
        return {'files':[rows[name] for name in sorted(rows)],'cloud':True,'revision':(manifest or {}).get('project',{}).get('revision')}

    def list_projects(self):
        with self.catalogue_lock:
            if self.catalogue is not None and time.monotonic()-self.catalogue_at < 30:
                return copy.deepcopy(self.catalogue)
            self.catalogue = self._list_projects()
            self.catalogue_at = time.monotonic()
            return copy.deepcopy(self.catalogue)

    def _list_projects(self):
        result=[]
        for key in self.objects.keys('studio/manifests/'):
            pid=key.rsplit('/',1)[-1].removesuffix('.json')
            manifest,_=self.manifest(pid,True)
            if manifest:
                p=manifest['project']; result.append({k:p[k] for k in ('id','name','revision','updated')} | {'chapters':len(p['chapters'])})
        return result

    def worker(self):
        while True:
            with self.cv:
                if self.stopped: return
                if not self.pending: self.cv.wait(3); continue
                pid=self.pending.pop(); self.states.setdefault(pid,{})['status']='UPLOADING'; self.persist()
            try: self.sync(pid)
            except Exception:
                # SDK errors may contain credential-adjacent URLs. Never return their raw text.
                with self.cv:
                    self.states[pid].update(status='FAILED',error='Cloud save failed. Local work is retained. Check access, connection and revision conflicts; retry cloud save.')
                    self.persist()

    def close(self):
        with self.cv: self.stopped=True; self.cv.notify_all()
        self.thread.join(5)
