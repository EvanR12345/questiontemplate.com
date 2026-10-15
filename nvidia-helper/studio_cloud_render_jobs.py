"""Render-only queue integration. A durable nonce never repeats a paid allocation.

The marketplace path remains gated by a completed real renderer verification.
Long exports keep a separate measured-duration gate; resource planning is free.
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
        limits={p:max(MAX_PREVIEW_SECONDS,int(self.studio.config.get('gpuRenderVerifiedDurationSeconds',{}).get(p,MAX_PREVIEW_SECONDS))) for p in configured}
        archive=self.studio.storage
        ready=bool(configured and archive.enabled and archive.provider=='Google Cloud Storage')
        return {'backend':'marketplace','ready':ready,'providers':configured,'resolution':'720p30',
            'maximumDurationSeconds':max(limits.values(),default=MAX_PREVIEW_SECONDS),'providerDurationLimits':limits,'voiceLocation':'selected project voice engine',
            'videoLocation':'private Google storage','paidRequestsSubmitted':0,
            'longExportPrepared':True,'longExportVerified':any(v>MAX_PREVIEW_SECONDS for v in limits.values()),
            'reason':'Ready for verified short render-only previews; long exports need a capped validation run.' if ready else
                'Cloud GPU rendering needs a successful video test and private Google storage. Existing local rendering remains available.'}

    def plan(self,project):
        from studio_cloud_render_plan import render_plan
        from studio_cloud_pod_render import render_assets
        sizes=[];missing=[]
        for path in render_assets(project):
            record=self.studio.storage.asset_record(project['id'],path)
            if record:sizes.append(record['bytes'])
            else:missing.append(path)
        plan=render_plan(project,sum(sizes),self.studio.config)
        limit=self.capabilities()['maximumDurationSeconds']
        plan.update(currentVerifiedLimitSeconds=limit,withinVerifiedDuration=plan['durationSeconds']<=limit,
            missingCloudInputs=len(missing),configuredDiskGB=self.studio.config.get('gpuWorkingDiskGB',25),
            paidRequestsSubmitted=0)
        plan['workingDiskSufficient']=plan['configuredDiskGB']>=plan['recommendedDiskGB']
        return plan

    def publish_saved_files(self,pid,files):
        if not files:return
        manifest,etag=self.studio.storage.manifest(pid,True)
        if not manifest:raise ValueError('Cloud project manifest is missing; completed objects remain saved.')
        publication=copy.deepcopy(manifest)
        for alias,record in files.items():
            publication.setdefault('files',{})[record['path']]={k:v for k,v in record.items() if k!='path'}
            publication.setdefault('renderCache',{})[alias]=record
        self.studio.storage.objects.put_json('studio/manifests/'+pid+'.json',publication,etag=etag)
        self.studio.storage.manifests.pop(pid,None)
        with self.studio.storage.catalogue_lock:self.studio.storage.catalogue_at=0

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
        from studio_cloud_render_plan import render_plan
        version=copy.deepcopy(project);version['settings'].setdefault('engagement',{})['exportDestination']=options['destination']
        duration=render_plan(version,config=self.studio.config)['durationSeconds']
        limit=self.capabilities()['maximumDurationSeconds']
        if duration>limit:raise ValueError(f'Cloud exports are verified up to {limit:g} seconds on the configured providers. A longer capped validation is required before production.')
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
                'completedOutputs':list(record.get('savedFiles',{}).values()),
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
            elif kind=='assetSaved':
                item=dict(value);alias=item.pop('originalPath')
                record.setdefault('savedFiles',{})[alias]=item
                record['completedAssets']=len(record['savedFiles'])
                self.publish_saved_files(project['id'],{alias:item})
            self.save(record)
        from studio_cloud_pod_render import CloudPodRenderer
        from studio_gpu_broker import RenderGPUReservation
        capabilities=self.capabilities();verified=capabilities.get('providers',[])
        from studio_cloud_render_plan import render_plan
        seconds=render_plan(render_project,config=self.studio.config)['durationSeconds']
        limits=capabilities.get('providerDurationLimits',{})
        verified=[p for p in verified if limits.get(p,MAX_PREVIEW_SECONDS)>=seconds]
        config=copy.deepcopy(self.studio.config)
        config['gpuProviderOrder']=[p for p in config.get('gpuProviderOrder',['runpod','vast','lium']) if p in verified]
        renderer=(self.renderer_factory or CloudPodRenderer)(self.studio.store,self.studio.storage,config,gate,
            self.studio.store.root/'.managed-rental.json',progress,lease_factory=RenderGPUReservation)
        try:
            gate('Checking saved cloud inputs');prepared=renderer.capabilities(project)
            report=renderer.run(render_project,identity,compute_cap,prepared_sources=prepared)
            # Results are added to the archive. The selected original render,
            # source text and manual edits are not overwritten by this preview.
            manifest,etag=self.studio.storage.manifest(project['id'],True)
            if digest(self.studio.store.load(project['id']))!=digest(project) or digest(manifest['project'])!=digest(project):
                raise ValueError('The source changed during rendering. Completed immutable videos are retained; originals were not replaced.')
            for path,source in getattr(renderer,'source_records',{}).items():
                if source.get('optionalCache'):continue
                current=manifest.get('files',{}).get(path,{})
                if any(current.get(k)!=source.get(k) for k in ('bytes','sha256')):
                    raise ValueError('Saved input bytes changed during rendering. Completed earlier outputs remain in the archive.')
            publication=copy.deepcopy(manifest)
            for alias,output in report['files'].items():
                publication['files'][output['path']]={k:v for k,v in output.items() if k!='path'}
                publication.setdefault('renderCache',{})[alias]=output
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
