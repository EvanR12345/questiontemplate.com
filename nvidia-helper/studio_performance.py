"""Durable, compatible production observations, not another billing ledger.

Keep failed/reused/pending observations for diagnostics. Only fresh successful
matching work informs speed. Parent intervals and nested work are never added.
"""
import hashlib
import json
import math
import sqlite3
import statistics
import threading
import time

VERSION = 1
DIRECTOR_PASSES = {'Casting supervisor', 'Story analyst', 'Chapter director',
    'Scene director', 'Cinematographer', 'Workflow planner', 'Continuity supervisor',
    'Image prompt engineer','Source fact analyst','Source continuity reviewer','Source fact correction','Visual storyboard director','Storyboard continuity supervisor','Targeted storyboard repair'}


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def signature(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def interval_union(intervals):
    total=0;end=None
    for start,finish in sorted(intervals):
        if finish<=start:continue
        total+=max(0,finish-max(start,end if end is not None else start))
        end=max(finish,end if end is not None else finish)
    return total


def profile_for(stage, project, config, details=None):
    details=details or {};settings=project['settings']
    image=settings['image'];director=settings['director'];video=settings['video']
    profile={'version':VERSION,'stage':stage}
    if stage=='AI directing' or stage in DIRECTOR_PASSES:
        profile.update(provider=director.get('provider'),model=details.get('model') or
            (config.get('openaiModel','gpt-6-luna') if director.get('provider')=='openai-luna' else director.get('model')),
            reasoning=director.get('reasoning','Balanced'),parallelism=details.get('directorTasks',director.get('parallelism',1)),
            concise=director.get('concisePrompts',False),focused=settings.get('focusedPrompts',False),
            executionMode=director.get('executionMode','classic'),reasoningProfile=director.get('reasoningProfile','selected'),
            factGroupSentences=director.get('factGroupSentences',48),
            cadencePerMinute=director.get('cadencePerMinute'),
            compactWire=config.get('openaiCompactWire',False) or director.get('executionMode','classic').startswith('staged'),
            tier=director.get('processingTier',config.get('openaiServiceTier','default')),layout=settings.get('layoutMode','AUTO'),
            mode=settings.get('generationMode','BALANCED'),style=settings.get('style'),
            layoutSettings=settings.get('customLayout',{}),maxOutputTokens=director.get('maxOutputTokens',12000),
            checkLevel=settings.get('qcCheckLevel','off'))
        if director.get('executionMode','classic').startswith('staged'):
            profile['planVersion']=2
            profile['visualGroupLimit']={'sentences':24,'characters':6000,'targetShots':8}
    elif stage in ('Image pipeline','Image + quality checks','Image generation','Visual QC','Character reference','Intro images'):
        if details.get('shot'):
            image=details.get('_shotSpec') or next((s for c in project['chapters'] for sc in c['scenes'] for s in sc['shots']
                if s['id']==details['shot']),{}) | {}
            image={**settings['image'],**image.get('generationSettings',{}),
                'provider':image.get('imageProvider',settings['image']['provider']),
                'model':image.get('imageModel',settings['image']['model']),
                'workflow':image.get('workflow',settings['image']['workflow'])}
        # A changed cloud host cannot inherit the previous host's timing.
        backend=signature({k:config.get(k) for k in ('comfyEndpoint','comfySshHost','comfyPort','comfyWorkflow','performanceGPU')})
        profile.update(provider=details.get('provider') or image.get('provider'),model=details.get('model') or image.get('model'),
            workflow=image.get('workflow'),width=image.get('width'),height=image.get('height'),
            steps=image.get('steps'),sampler=image.get('sampler'),scheduler=image.get('scheduler'),
            conditioning=signature({k:image.get(k) for k in ('guidance','denoisingStrength','loras','controlnets','ipAdapter')}),
            references=image.get('workflow')=='reference-edit',checkLevel=settings.get('qcCheckLevel','off'),
            sampleEvery=settings.get('qcSampleEvery',5),backend=backend,execution='single-image-lane',
            automaticRepair=settings.get('automaticRepair',False),maxRetries=settings.get('maxImageRetries',0),
            style=settings.get('style'))
        if stage=='Image pipeline':
            profile.update(imageTasks=details.get('imageTasks',3),director=director.get('provider'),
                directorParallelism=director.get('parallelism',1),reasoning=director.get('reasoning','Balanced'))
    elif stage in ('Render chapter','Assemble full video'):
        profile.update(width=video.get('width'),height=video.get('height'),fps=video.get('fps'),
            renderer=video.get('renderer','auto'),crf=video.get('crf'),fit=video.get('imageFit'),
            motion=video.get('motionMode'),engagement=signature(settings.get('engagement',{})),
            machine=signature({'gpu':config.get('performanceGPU','local'),
                'renderer':config.get('nativeRendererExecutable'), 'ffmpeg':config.get('ffmpeg')}))
    elif stage=='Narration':
        profile.update(voice=settings.get('voice'),speed=settings.get('speakingSpeed',settings.get('speed',1)),
            delivery=settings.get('narrationDelivery','standard'),effectMode=settings.get('soundEffects','subtle'),
            emphasis=signature(settings.get('emphasisPhrases',[])))
    else:return None
    return profile


def observation_units(stage, project, details):
    chapter=next((c for c in project['chapters'] if c['number']==details.get('chapter')),None)
    if stage in DIRECTOR_PASSES:return {'kind':'calls','value':1}
    if stage=='AI directing' or stage=='Narration':
        if not chapter:return None
        return {'kind':'characters','value':len(chapter.get('cleanNarrationText') or chapter['sourceText'])}
    if stage=='Image pipeline':return {'kind':'images','value':details.get('images',0)}
    if stage in ('Image + quality checks','Image generation','Visual QC','Character reference','Intro images'):
        return {'kind':'images','value':1}
    if stage=='Render chapter':
        duration=chapter.get('audio',{}).get('duration') if chapter else None
        return {'kind':'videoSeconds','value':duration} if positive(duration) else None
    if stage=='Assemble full video':
        duration=sum(c.get('audio',{}).get('duration',0) for c in project['chapters'] if c['sourceText'].strip())
        return {'kind':'videoSeconds','value':duration} if positive(duration) else None
    return None


class PerformanceStore:
    def __init__(self,path):
        self.lock=threading.RLock()
        self.db=sqlite3.connect(path,check_same_thread=False)
        self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS observations '
            '(id TEXT PRIMARY KEY,run_id TEXT,profile_hash TEXT,profile TEXT,stage TEXT,units_kind TEXT,'
            'units REAL,seconds REAL,cost REAL,status TEXT,reused INTEGER,pending INTEGER,paused INTEGER,'
            'finished REAL,source TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS observations_profile ON observations(profile_hash,finished)')
        self.db.commit()

    def record(self,identity,run_id,profile,units,seconds,details,finished=None,source='live'):
        if not profile or not units or not positive(units['value']) or not positive(seconds):return False
        cost=details.get('estimatedUSD')
        if type(cost) not in (int,float) or not math.isfinite(cost) or cost<0:cost=None
        with self.lock:
            changed=self.db.execute('INSERT OR IGNORE INTO observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,str(run_id),signature(profile),json.dumps(profile,sort_keys=True),profile['stage'],
                 units['kind'],units['value'],seconds,cost,details.get('status','COMPLETE'),
                 bool(details.get('reused')),bool(details.get('usagePending')),bool(details.get('paused')),
                 finished or time.time(),source)).rowcount
            self.db.commit()
        return bool(changed)

    def estimate(self,profile,units,now=None):
        now=now or time.time()
        if not profile or not units or not positive(units['value']):return {'status':'UNKNOWN','seconds':None,'costUSD':None,'samples':0,'runs':0}
        with self.lock:
            identity=signature(profile)
            retained=self.db.execute('SELECT COUNT(*) FROM observations WHERE profile_hash=? AND units_kind=?',
                (identity,units['kind'])).fetchone()[0]
            usable=[dict(r) for r in self.db.execute('SELECT * FROM observations WHERE profile_hash=? AND units_kind=? '
                "AND status='COMPLETE' AND reused=0 AND pending=0 AND paused=0 AND finished BETWEEN ? AND ? "
                'ORDER BY finished DESC LIMIT 30',(identity,units['kind'],now-90*86400,now))]
        if not usable:return {'status':'UNKNOWN','seconds':None,'costUSD':None,'samples':0,'runs':0,'retained':retained}
        # One long run cannot masquerade as thirty independent experiments.
        rates={};costs={}
        for row in usable:
            rates.setdefault(row['run_id'],[]).append(row['seconds']/row['units'])
            if row['cost'] is not None:costs.setdefault(row['run_id'],[]).append(row['cost']/row['units'])
        run_rates=[statistics.median(v) for v in rates.values()]
        scale=units['value'];seconds=statistics.median(run_rates)*scale
        return {'status':'LIMITED_HISTORY' if len(run_rates)<3 else 'OBSERVED',
            'seconds':seconds,'rangeSeconds':[min(run_rates)*scale,max(run_rates)*scale],
            'costUSD':statistics.median([statistics.median(v) for v in costs.values()])*scale if costs else None,
            'samples':len(usable),'runs':len(run_rates),'retained':retained,
            'latest':max(r['finished'] for r in usable),'profile':profile,
            'note':'Recent matching successful fresh work; observed range is not a confidence interval or a guaranteed finish.'}

    def summary(self):
        with self.lock:
            return dict(self.db.execute('SELECT COUNT(*) AS observations,COUNT(DISTINCT run_id) AS runs,'
                'MAX(finished) AS latest FROM observations').fetchone())

    def known(self,identities):
        result=set()
        with self.lock:
            for start in range(0,len(identities),400):
                group=identities[start:start+400]
                result.update(r[0] for r in self.db.execute('SELECT id FROM observations WHERE id IN ('+
                    ','.join('?' for _ in group)+')',group))
        return result

    def close(self):
        with self.lock:self.db.close()
