"""Cloud-only rendering using existing Studio logic and immutable GCS assets.

The entry point refuses laptop execution. Cloud scratch belongs to the job;
the private bucket retains checkpoints, results and original project revisions.
"""
import json, os, re, tempfile, threading, time
from pathlib import Path
from studio_data import ProjectStore, validate_project
from studio_storage import R2Archive, sha, stable_file
from studio_render import VideoRenderer


class RenderCancelled(Exception):pass


class CloudRenderJob:
    def __init__(self, objects, job_id, scratch, renderer_factory=VideoRenderer):
        if not re.fullmatch(r'[a-f0-9]{32}',job_id):raise ValueError('Invalid cloud render job.')
        self.objects=objects;self.key=f'studio/render-jobs/{job_id}.json'
        self.job_id=job_id;self.scratch=Path(scratch);self.factory=renderer_factory
        self.last_check=0;self.last_update=0;self.files={}
        self.lock=threading.RLock()

    def state(self):
        raw,etag=self.objects.read(self.key)
        if not raw:raise ValueError('Cloud render request is missing.')
        state=json.loads(raw)
        if state.get('id')!=self.job_id:raise ValueError('Cloud render identity mismatch.')
        return state,etag

    def update(self, **values):
        # Conditional writes preserve cancellation and another worker's lease.
        with self.lock:
            state,etag=self.state()
            if state.get('cancelRequested') and values.get('status') not in ('CANCELLED','FAILED'):
                raise RenderCancelled('Cloud render cancelled; saved assets retained.')
            state.update(values,updated=time.time())
            self.objects.put_json(self.key,state,etag=etag)
            return state

    def gate(self, message):
        now=time.monotonic()
        if now-self.last_check>=2:
            state,_=self.state();self.last_check=now
            if state.get('cancelRequested'):raise RenderCancelled('Cloud render cancelled; saved assets retained.')
        if now-self.last_update>=4:
            self.update(stage=message,elapsed=time.time()-self.started,completedAssets=len(self.files))
            self.last_update=now

    def checkpoint(self, path):
        # The final remux replaces temporary complete files. Do not archive
        # those large intermediates as separate public render results.
        if '.partial.' in path.name:return
        relative=path.relative_to(self.store.folder(self.project_id)).as_posix()
        self.store.asset_local(self.project_id,relative)
        stamp=stable_file(path);checksum=sha(path)
        key=f'studio/assets/{self.project_id}/{checksum}/{relative}'
        self.objects.upload(key,path,checksum)
        if stable_file(path)!=stamp:raise ValueError('Render output changed during upload.')
        # Parallel clip workers must not race conditional job-state writes.
        with self.lock:
            self.files[relative]={'key':key,'sha256':checksum,'bytes':stamp[0],'stamp':list(stamp)}
            self.update(files=dict(self.files),completedAssets=len(self.files))

    def run(self):
        state,etag=self.state()
        if state.get('status')=='COMPLETE':return state
        if state.get('status') not in ('QUEUED','SUBMISSION_UNKNOWN','FAILED','CANCELLED'):
            raise ValueError('This cloud render is already running; no duplicate launched.')
        if state.get('cancelRequested'):
            state.update(status='CANCELLED',stage='Cancelled before rendering; saved assets retained')
            self.objects.put_json(self.key,state,etag=etag)
            raise RenderCancelled('Clear cancellation explicitly before retrying.')
        self.started=time.time()
        # Claim the execution before downloading or encoding anything.
        state.update(status='RESTORING',stage='Loading saved cloud project',started=self.started,updated=self.started)
        self.objects.put_json(self.key,state,etag=etag)
        self.project_id=state.get('project','')
        archive=None
        try:
            if not re.fullmatch(r'pr-[a-f0-9]{16}',self.project_id):raise ValueError('Invalid project.')
            destination=state.get('destination','youtube')
            if destination not in ('youtube','patreon'):raise ValueError('Invalid export destination.')
            manifest_key=f'studio/manifests/{self.project_id}.json'
            raw,original_etag=self.objects.read(manifest_key)
            if not raw:raise ValueError('Save this project to Google storage before rendering.')
            original=json.loads(raw);project=validate_project(original['project'])
            if project['id']!=self.project_id:raise ValueError('Saved project identity mismatch.')
            if state.get('revision')!=project['revision']:
                raise ValueError('Project changed before the render started. Submit its current revision.')
            self.store=ProjectStore(self.scratch/'studio')
            archive=R2Archive(self.store,self.scratch/'absent-config.json');archive.close()
            archive.objects=self.objects;archive.enabled=True;self.store.archive=archive
            archive.restore(self.project_id)
            self.files=dict(state.get('files',{}))
            # Reuse only verified immutable checkpoints from this job/snapshot.
            merged=dict(original['files'])
            for name,record in self.files.items():
                if (not re.fullmatch(r'[a-f0-9]{64}',record.get('sha256','')) or
                    record.get('key')!=f'studio/assets/{self.project_id}/{record["sha256"]}/{name}'):
                    raise ValueError('Invalid render checkpoint.')
                self.store.asset_local(self.project_id,name)
                merged[name]=record
            frozen=original | {'files':merged}
            archive.manifest=lambda pid,fresh=False:(frozen,original_etag)
            project['settings'].setdefault('engagement',{})['exportDestination']=destination
            self.store.save(project)
            project=self.store.load(self.project_id)
            renderer=self.factory(self.store,{'ffmpeg':os.environ.get('STUDIO_FFMPEG','/usr/bin/ffmpeg'),
                'renderWorkers':int(os.environ.get('STUDIO_RENDER_WORKERS','1'))})
            renderer.on_output=self.checkpoint
            # Cloud renderer never loads an image model or a paid director.
            # Existing outro narration must be prepared before this job.
            self.update(status='RENDERING',stage='Rendering saved story footage')
            result=renderer.full(project,self.gate)
            def saved(p):
                if p.get('render',{}).get('path') and p['render']['path']!=result['path']:
                    p.setdefault('renderHistory',[]).append(p['render'])
                p.setdefault('exports',{})[destination]=result
                p.update(render=result,renderStale=False)
            self.store.mutate(self.project_id,saved)
            self.gate('Saving completed video to Google storage')
            self.update(status='UPLOADING',stage='Verifying and publishing completed video')
            # Pin the original manifest generation: concurrent website edits
            # cannot be replaced even after many local render-cache mutations.
            # Checkpoints were already uploaded and verified. Include their
            # current local stamps so final publication does not upload every
            # clip again. The original generation still guards concurrent edits.
            publication=original | {'files':original['files'] | self.files}
            archive.manifest=lambda pid,fresh=False:(publication,original_etag)
            archive.sync(self.project_id)
            return self.update(status='COMPLETE',stage='Video ready',result=result,
                elapsed=time.time()-self.started,files=self.files)
        except RenderCancelled:
            self.update(status='CANCELLED',stage='Cancelled; completed assets retained',files=self.files)
            raise
        except Exception as error:
            # No upstream exception text, request headers or credentials in logs.
            self.update(status='FAILED',stage='Render failed; original project retained',
                errorType=type(error).__name__,files=self.files)
            raise
        finally:
            if archive:archive.close()


def main():
    if not os.environ.get('CLOUD_RUN_JOB'):
        raise RuntimeError('Cloud render entry point refuses laptop execution.')
    from google.cloud import storage
    from google_storage import GCSObjects
    bucket=os.environ['STUDIO_BUCKET'];project=os.environ['GOOGLE_CLOUD_PROJECT']
    # Attached, bucket-only service identity. No downloaded private key.
    objects=GCSObjects({'bucket':bucket},client=storage.Client(project=project))
    with tempfile.TemporaryDirectory(prefix='studio-render-',dir='/work') as scratch:
        CloudRenderJob(objects,os.environ['STUDIO_RENDER_JOB_ID'],scratch).run()


if __name__=='__main__':
    try:main()
    except Exception as error:
        print(json.dumps({'cloudRenderFailed':True,'errorType':type(error).__name__}))
        raise SystemExit(1)
