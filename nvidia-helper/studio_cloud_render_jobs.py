"""Render-only queue integration. A durable nonce never repeats a paid allocation.

The marketplace path remains gated by a completed real renderer verification.
Until longer exports are measured, its website control admits short previews only.
"""
import copy,json,math,re,threading,time
from pathlib import Path
from cost_control import atomic_replace
from studio_data import digest

MAX_PREVIEW_SECONDS=120
TERMINAL={'COMPLETE','FAILED','CANCELLED','INTERRUPTED'}


def render_options(value):
    if not isinstance(value,dict):raise ValueError('Cloud render options must be structured.')
    identity=value.get('id')
    if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('Invalid cloud render identity.')
    revision=value.get('revision');cap=value.get('capUSD');destination=value.get('destination')
    if type(revision) is not int or revision<1:raise ValueError('Save the project before rendering.')
    if type(cap) not in (int,float) or not math.isfinite(cap) or not .10<=cap<=100:
        raise ValueError('Set an explicit total cloud-render cap between US$0.10 and US$100.')
    if destination not in ('youtube','patreon'):raise ValueError('Choose YouTube or Patreon output.')
    return {'id':identity,'revision':revision,'destination':destination,'capUSD':cap}


class CloudRenderJobs:
    def __init__(self,studio,renderer_factory=None):
        self.studio=studio;self.folder=studio.store.root/'.cloud-render-jobs';self.folder.mkdir(exist_ok=True)
        self.renderer_factory=renderer_factory;self.lock=threading.RLock()

    def path(self,identity):
        if not isinstance(identity,str) or not re.fullmatch(r'[a-f0-9]{32}',identity):raise ValueError('Invalid cloud render identity.')
        return self.folder/(identity+'.json')

    def load(self,identity):
        with self.lock:
            path=self.path(identity)
            return json.loads(path.read_text(encoding='utf-8')) if path.exists() else None

    def save(self,record):
        with self.lock:
            old=self.load(record['id'])
            if old and old.get('cancelRequested'):record['cancelRequested']=True
            path=self.path(record['id']);temporary=path.with_suffix('.writing')
            temporary.write_text(json.dumps(record,separators=(',',':')),encoding='utf-8');atomic_replace(temporary,path)

    def capabilities(self):
        from studio_gpu_providers import provider_status
        status=provider_status(self.studio.config)
        order=status['providerOrder'];configured=[r['provider'] for r in status['providers']
            if r['credentialSaved'] and r['renderVerified'] and r['provider'] in order]
        archive=self.studio.storage
        ready=bool(configured and archive.enabled and archive.provider=='Google Cloud Storage')
        return {'backend':'marketplace','ready':ready,'providers':configured,'resolution':'720p30',
            'maximumDurationSeconds':MAX_PREVIEW_SECONDS,'voiceLocation':'local',
            'videoLocation':'private Google storage','paidRequestsSubmitted':0,
            'reason':'Ready for verified short render-only previews.' if ready else
                'Cloud GPU rendering needs a successful video test and private Google storage. Existing local rendering remains available.'}

    def validate(self,project,options):
        if not self.capabilities()['ready']:raise ValueError(self.capabilities()['reason'])
        if project['revision']!=options['revision']:raise ValueError('Project changed. Save and review the current version first.')
        video=project['settings']['video']
        if (video['width'],video['height'],video['fps'])!=(1280,720,30):raise ValueError('The verified cloud preview format is 720p30.')
        duration=0.;chapters=[c for c in project['chapters'] if c.get('sourceText','').strip()]
        if not chapters:raise ValueError('A saved chapter is required.')
        from studio_qc import decision
        for chapter in chapters:
            audio=chapter.get('audio',{});seconds=audio.get('duration')
            if not audio.get('path') or type(seconds) not in (int,float) or not math.isfinite(seconds) or seconds<=0:
                raise ValueError('Generate and save continuous narration before rendering.')
            duration+=seconds
            shots=[s for scene in chapter.get('scenes',[]) for s in scene['shots']]
            if not shots or any(not s.get('imagePath') for s in shots):raise ValueError('Save every selected shot image before rendering.')
            if any(decision(project,s,self.studio.store)['blocking'] for s in shots):
                raise ValueError('Resolve blocking image findings before rendering; advisory findings stay visible.')
        intro=project['intro']
        if intro.get('enabled'):duration+=intro['duration']*(len(chapters) if intro.get('placement')=='every_chapter' else 1)
        engagement=project['settings'].get('engagement',{})
        if engagement.get('outroEnabled'):duration+=engagement.get('outroDuration',15)
        if duration>MAX_PREVIEW_SECONDS:raise ValueError('Long cloud exports are not verified yet. This control is limited to 120-second previews.')
        rental=self.studio.store.root/'.managed-rental.json'
        if rental.exists() and json.loads(rental.read_text()).get('status')!='STOPPED':
            raise ValueError('An earlier GPU allocation remains unresolved; no second rental will start.')
        with self.studio.storage.cv:
            if self.studio.storage.active_project is not None or self.studio.storage.pending:
                raise ValueError('Wait for cloud saving to finish before rendering.')

    def status(self,pid,identity=None):
        self.studio.store.folder(pid)
        with self.studio.cv:
            query="SELECT * FROM jobs WHERE project=? AND kind='render-cloud'"
            values=[pid]
            if identity:query+=' AND id=?';values.append('cloud-'+identity)
            row=self.studio.db.execute(query+' ORDER BY created DESC LIMIT 1',values).fetchone()
            if not row:return {'project':pid,'status':'NOT_STARTED','backend':'marketplace'}
            row=dict(row);options=json.loads(row['payload']);record=self.load(options['id']) or {}
            state=row['status']
            if state=='COMPLETE' and not record.get('result'):state='INTERRUPTED'
            return {'project':pid,'id':options['id'],'backend':'marketplace','status':state,
                'stage':record.get('stage') or row['message'],'elapsed':max(0,time.time()-row['started']) if row['started'] and state=='RUNNING' else row['seconds'],
                'cancelRequested':bool(record.get('cancelRequested')),'capUSD':options['capUSD'],
                'completedAssets':record.get('completedAssets',0),'result':record.get('result'),
                'cost':record.get('cost'),'error':record.get('error'),
                'originalProjectPreserved':True}

    def start(self,pid,value):
        options=render_options(value);identity='cloud-'+options['id']
        with self.studio.cv:
            old=self.studio.db.execute('SELECT project,payload FROM jobs WHERE id=?',(identity,)).fetchone()
            if old:
                if old['project']!=pid or json.loads(old['payload'])!=options:
                    raise ValueError('This render identity already belongs to another request.')
                return self.status(pid,options['id'])
            project=self.studio.store.load(pid);self.validate(project,options)
            active=self.studio.db.execute("SELECT id FROM jobs WHERE status IN ('QUEUED','RUNNING')").fetchone()
            if active:raise ValueError('Finish or cancel the active queue before a render-only cloud preview.')
            self.studio.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,pid,None,None,'render-cloud',json.dumps(options),'QUEUED','Waiting for cloud render',0,time.time(),None,0,0,None))
            self.studio.db.commit();self.studio.cv.notify_all()
        return self.status(pid,options['id'])

    def cancel(self,pid,identity):
        self.path(identity)
        with self.studio.cv:
            row=self.studio.db.execute("SELECT status FROM jobs WHERE id=? AND project=? AND kind='render-cloud'",('cloud-'+identity,pid)).fetchone()
            if not row:raise ValueError('This render does not belong to the selected project.')
            if row['status'] in TERMINAL:return self.status(pid,identity)
            record=self.load(identity) or {'id':identity};record['cancelRequested']=True;self.save(record)
            if row['status']=='RUNNING' and self.studio.current=='cloud-'+identity:self.studio.cancel=True
            else:
                self.studio.db.execute("UPDATE jobs SET status='CANCELLED',message='Cancelled before rental' WHERE id=?",('cloud-'+identity,));self.studio.db.commit()
            self.studio.cv.notify_all()
        return self.status(pid,identity)

    def run(self,project,options):
        options=render_options(options);identity=options['id']
        if self.load(identity):raise ValueError('A cloud attempt already exists. Its result is retained; paid work is never replayed automatically.')
        self.validate(project,options)
        render_project=copy.deepcopy(project);render_project['settings'].setdefault('engagement',{})['exportDestination']=options['destination']
        # A separate reserve covers operations and transfer; this preview does
        # not borrow the project's image/AI budget or silently increase a cap.
        reserve=min(.05,options['capUSD']/5);compute_cap=options['capUSD']-reserve
        record={'id':identity,'sourceRevision':project['revision'],'started':time.time(),'capUSD':options['capUSD'],
            'operationsReserveUSD':reserve,'status':'RUNNING','stage':'Checking saved cloud inputs'}
        self.save(record)
        def gate(message=''):
            # Pausing a rental must stop billing rather than leave a GPU waiting.
            from studio_service import JobCancelled
            with self.studio.cv:
                if self.studio.paused or self.studio.yield_requested:raise JobCancelled()
            self.studio.gate(message,wait_paused=False)
        def progress(kind,value):
            if kind=='progress':record.update(stage=value.get('stage','Rendering'),completedAssets=value.get('completedAssets',record.get('completedAssets',0)))
            elif kind=='rental':record.update(stage=value.get('status','Starting worker'),rentalStatus=value.get('status'))
            self.save(record)
        from studio_cloud_pod_render import CloudPodRenderer
        from studio_cloud_gpu import CloudGPULease
        renderer=(self.renderer_factory or CloudPodRenderer)(self.studio.store,self.studio.storage,self.studio.config,gate,
            self.studio.store.root/'.managed-rental.json',progress,lease_factory=CloudGPULease)
        try:
            gate('Checking saved cloud inputs');prepared=renderer.capabilities(project)
            report=renderer.run(render_project,identity,compute_cap,prepared_sources=prepared)
            # Results are added to the archive. The selected original render,
            # source text and manual edits are not overwritten by this preview.
            manifest,etag=self.studio.storage.manifest(project['id'],True)
            if digest(self.studio.store.load(project['id']))!=digest(project) or digest(manifest['project'])!=digest(project):
                raise ValueError('The source changed during rendering. Completed immutable videos are retained; originals were not replaced.')
            publication=copy.deepcopy(manifest)
            for output in report['files'].values():publication['files'][output['path']]={k:v for k,v in output.items() if k!='path'}
            self.studio.storage.objects.put_json('studio/manifests/'+project['id']+'.json',publication,etag=etag)
            self.studio.storage.manifests.pop(project['id'],None)
            with self.studio.storage.catalogue_lock:self.studio.storage.catalogue_at=0
            record.update(status='COMPLETE',stage='Video saved in private Google storage',result=report['result'],
                completedAssets=len(report['files']),cost={'computeWindowEstimateUSD':report['rental'].get('estimatedUSD'),
                    'operationsReserveUSD':reserve,'totalCapUSD':options['capUSD'],'billingIsEstimate':True})
            self.save(record)
        except BaseException as error:
            record.update(status='FAILED',error=re.sub(r'https?://\S+','[private URL]',str(error))[:700],stage='Render stopped; originals preserved')
            if renderer.report.get('result'):record['retainedResult']=renderer.report['result']
            self.save(record);raise

    def finish(self,job,state,message):
        options=json.loads(job['payload']);record=self.load(options['id']) or {'id':options['id']}
        record.update(status=state,stage=message if state!='COMPLETE' else record.get('stage',message),finished=time.time())
        self.save(record)
