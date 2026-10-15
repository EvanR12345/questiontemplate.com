"""Render-only SSH worker. Object capabilities arrive on stdin, never argv/logs.

No director, speech or diffusion modules are imported. The caller owns the GPU
lease and signs only this job's immutable object uploads. Video bytes travel
from this worker to Google, not through the laptop.
"""
import base64,gzip,hashlib,io,json,math,mimetypes,os,re,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
from urllib.parse import urlparse
from fractions import Fraction

def validate_video(video,file_duration,expected_duration,chapter_count):
    """Account for subframe chapter trims, while rejecting wrong output modes."""
    try:
        average=float(Fraction(video['avg_frame_rate']));frames=int(video['nb_frames'])
    except (ValueError,KeyError,ZeroDivisionError,TypeError):raise ValueError('Cloud video timing metadata is invalid.') from None
    tolerance=max(2,chapter_count+2)
    if (video.get('width'),video.get('height'))!=(1280,720) or not math.isfinite(average):
        raise ValueError('Unexpected cloud video dimensions or frame rate.')
    if abs(average-30)>min(.1,tolerance/max(expected_duration,1)) or abs(frames-expected_duration*30)>tolerance:
        raise ValueError('Cloud video frame cadence differs from the saved 30 fps plan.')
    if not math.isfinite(file_duration) or abs(file_duration-expected_duration)>.1:
        raise ValueError('Unexpected narration/video duration mismatch.')
    return average


def safe_url(value):
    parsed=urlparse(value)
    if (parsed.scheme!='https' or parsed.username or parsed.password or parsed.fragment
            or not (parsed.hostname=='storage.googleapis.com' or
                    (parsed.hostname or '').endswith('.storage.googleapis.com'))):
        raise ValueError('Only private Google object capabilities are accepted.')
    return value


_telemetry=None

def isolate_telemetry():
    """Keep native encoder stdout separate from the JSON control protocol."""
    global _telemetry
    _telemetry=os.fdopen(os.dup(sys.stdout.fileno()),'w',buffering=1,encoding='utf-8')
    os.dup2(sys.stderr.fileno(),sys.stdout.fileno())

def emit(value):
    print(json.dumps(value,separators=(',',':')),file=_telemetry or sys.stdout,flush=True)


def receive():
    line=sys.stdin.readline()
    if not line or len(line)>16*1024*1024:raise RuntimeError('Render controller disconnected or returned invalid control data.')
    value=json.loads(line)
    if not isinstance(value,dict):raise ValueError('Render control must be structured.')
    if value.get('encoding')=='gzip-json':
        packed=base64.b64decode(value['payload'],validate=True)
        with gzip.GzipFile(fileobj=io.BytesIO(packed)) as compressed:raw=compressed.read(64*1024*1024+1)
        if len(raw)>64*1024*1024 or hashlib.sha256(raw).hexdigest()!=value.get('sha256'):
            raise ValueError('Invalid compressed render control identity or size.')
        value=json.loads(raw)
        if not isinstance(value,dict):raise ValueError('Render control must be structured.')
    return value


def run(spec):
    if not re.fullmatch(r'[a-f0-9]{32}',spec.get('id','')):raise ValueError('Invalid render job identity.')
    if os.name=='nt' or not (os.environ.get('RUNPOD_POD_ID') or os.environ.get('STUDIO_GPU_CLOUD')=='1'):
        raise RuntimeError('Render-only worker requires the owned cloud pod.')
    deadline=spec.get('deadline')
    if type(deadline) not in (int,float) or not math.isfinite(deadline) or deadline<=time.time():raise ValueError('Render deadline expired.')
    root=Path('/workspace')/('studio-render-only-'+spec['id'])
    if root.exists():raise ValueError('This render identity already has working files; do not replay it.')
    root.mkdir();started=time.monotonic();last_progress=0.;upload_seconds=0.;uploads=[]
    from studio_data import ProjectStore,validate_project
    from studio_gpu_render import NativeVideoRenderer
    store=ProjectStore(root)
    project=validate_project(spec['project']);pid=project['id']
    records=spec.get('assets',[])
    if not isinstance(records,list) or not records:raise ValueError('Saved source assets required.')
    def gate(message=''):
        nonlocal last_progress
        if time.time()>=deadline:raise RuntimeError('Render spending deadline reached.')
        now=time.monotonic()
        if now-last_progress>=4:
            emit({'event':'progress','stage':message or 'Rendering saved footage','elapsed':now-started})
            last_progress=now
    from studio_cloud_inputs import CloudInputCache
    from studio_cloud_render_plan import timeout_for_bytes
    def capability(path):
        emit({'event':'source','path':path})
        return receive()
    cache=CloudInputCache(records,lambda path:store.asset_local(pid,path),capability,gate,deadline,safe_url)
    ordinary_asset=store.asset
    def source_asset(project_id,path):
        if project_id!=pid:raise ValueError('Cloud input belongs to another project.')
        return cache.ensure(path) if path in cache.records else ordinary_asset(project_id,path)
    store.asset=source_asset
    reserve=int(spec.get('scratchEstimateBytes',256*2**20))
    if shutil.disk_usage(root).free<reserve:
        raise RuntimeError('Insufficient cloud scratch for the planned output; Google originals remain intact.')
    project=store.save(project)
    renderer=NativeVideoRenderer(store,{'ffmpeg':'/usr/bin/ffmpeg','renderWorkers':1})
    operations=[]
    ordinary_run=renderer.run
    def measured(args,*rest,**kwargs):
        began=time.monotonic()
        kind='shot' if '-frames:v' in args else 'effects' if '-filter_complex_script' in args else 'mux'
        try:return ordinary_run(args,*rest,**kwargs)
        finally:operations.append({'kind':kind,'seconds':time.monotonic()-began})
    renderer.run=measured
    def completed(path):
        nonlocal upload_seconds
        if '.partial.' in path.name:return
        relative=path.relative_to(store.folder(pid)).as_posix()
        if path.suffix.lower()!='.mp4':return
        # Original media stays archived. Unreferenced render caches do not need
        # a paid transfer for every shot; completed chapter videos are reusable.
        if path.name.startswith('clip-') or path.name.endswith('-visual.mp4'):return
        digest=hashlib.sha256()
        with path.open('rb') as source:
            for part in iter(lambda:source.read(1024*1024),b''):digest.update(part)
        checksum=digest.hexdigest();size=path.stat().st_size
        emit({'event':'asset','path':relative,'bytes':size,'sha256':checksum})
        capability=receive()
        if capability.get('path')!=relative or capability.get('sha256')!=checksum:raise ValueError('Upload capability identity mismatch.')
        began=time.monotonic()
        if not capability.get('exists'):
            headers=capability.get('headers',{})
            if (set(headers)!={'Content-Type','x-goog-if-generation-match','x-goog-meta-sha256'}
                    or headers['x-goog-if-generation-match']!='0' or headers['x-goog-meta-sha256']!=checksum):
                raise ValueError('Upload must be immutable and checksum-bound.')
            with path.open('rb') as body:
                request=urllib.request.Request(safe_url(capability['url']),data=body,method='PUT',
                    headers=headers|{'Content-Length':str(size)})
                with urllib.request.urlopen(request,timeout=timeout_for_bytes(size,deadline-time.time())) as response:
                    if not 200<=response.status<300:raise RuntimeError('Google rejected render upload.')
        upload_seconds+=time.monotonic()-began
        emit({'event':'uploaded','path':relative,'bytes':size,'sha256':checksum})
        uploads.append({'path':relative,'bytes':size,'sha256':checksum})
    renderer.on_output=completed
    render_started=time.monotonic()
    result=renderer.full(project,gate)
    render_seconds=time.monotonic()-render_started
    final=store.asset(pid,result['path'])
    check_started=time.monotonic()
    metadata=json.loads(subprocess.check_output(['/usr/bin/ffprobe','-v','error','-show_streams','-show_format','-of','json',str(final)],text=True))
    subprocess.run(['/usr/bin/ffmpeg','-v','error','-i',str(final),'-f','null','-'],check=True,capture_output=True,timeout=max(.1,deadline-time.time()))
    video=next(s for s in metadata['streams'] if s['codec_type']=='video')
    average=validate_video(video,float(metadata['format']['duration']),result['duration'],
        sum(bool(c['sourceText'].strip()) for c in project['chapters']))
    checksum=hashlib.sha256()
    with final.open('rb') as source:
        for part in iter(lambda:source.read(1024*1024),b''):checksum.update(part)
    final_checksum=checksum.hexdigest()
    import torch
    emit({'event':'complete','result':result,'sha256':final_checksum,'bytes':final.stat().st_size,
        'sourceTransferSeconds':cache.seconds,'sourceBytesDownloaded':cache.bytes,'sourceFilesDownloaded':cache.files,'renderAndUploadsSeconds':render_seconds,'uploadSeconds':upload_seconds,
        'decodeVerificationSeconds':time.monotonic()-check_started,'operations':operations,'uploadedAssetsCount':len(uploads),
        'width':video['width'],'height':video['height'],'fps':30,'measuredAverageFPS':average,'frames':video.get('nb_frames'),
        'fileDuration':float(metadata['format']['duration']),'gpu':torch.cuda.get_device_name(0),
        'totalWorkerSeconds':time.monotonic()-started,
        'peakRetainedScratchBytes':sum(p.stat().st_size for p in root.rglob('*') if p.is_file()),
        'modelGenerationCalls':0,'directorCalls':0,'voiceGenerationCalls':0})


if __name__=='__main__':
    isolate_telemetry()
    try:run(receive())
    except BaseException as error:
        # Capabilities and private URLs must never leak via urllib tracebacks.
        detail=re.sub(r'https?://\S+','[private object URL]',str(error))[-400:]
        emit({'event':'failed','errorType':type(error).__name__,'detail':detail})
        raise SystemExit(1)
