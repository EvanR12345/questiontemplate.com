"""Opt-in cloud overlap inside one authoritative full-story queue job."""
from studio_director_limits import (shot_task_limit, validate_overlap,
    ADAPTIVE_IMAGE_ADMISSION, adaptive_image_window)
import copy
import hashlib
import threading
import time
import contextvars
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
from openai_director import OpenAIDirector
from studio_data import digest, get_chapter, get_shot, uid
from studio_qc import decision as qc_decision, automatic_repairs, check_level
from studio_execution import (CommitCoordinator, CoordinatedStore, CoordinatedTrace,
    ExecutionJournal, ImageLane, LaneProvider, BoundedExecutions, UnresolvedExecution)


class LaneDirector(OpenAIDirector):
    def __init__(self, config, root, lane):
        super().__init__(config,root)
        self.lane=lane

    def _call(self, role, context, schema, gate, vision=False):
        waiting=time.monotonic()
        with self.lane.acquire(gate):
            self.api_lane_wait_seconds=time.monotonic()-waiting
            return super()._call(role,context,schema,gate,vision)


class NoLocalGeneration:
    def unload(self):pass
    def generateImage(self,*args):
        raise ValueError('Cloud overlap cannot dispatch a local image fallback.')


class StaleExecution(ValueError):
    pass






class CloudStoryOverlap:
    def __init__(self, service, project, options):
        validate_overlap(project,options)
        from studio_schedule import strategy
        self.strategy=strategy(options)
        self.scheduler=None
        self.root=service
        self.project=project['id']
        self.settings=copy.deepcopy(project['settings'])
        self.sources=self.source_inputs(project)
        self.parent=service.current or uid('job-')
        self.coordinator=CommitCoordinator()
        self.journal=service.execution_journal
        from cost_control import SpendLedger
        ledger=SpendLedger(service.store.folder(self.project)/'api-cost-ledger.json',
            self.settings['budget']['openaiUSD'],allow_multiple=True)
        state=ledger.load()
        if state['requests'] or state.get('halted'):
            self.coordinator.close()
            raise UnresolvedExecution('Existing API ledger has unresolved requests or a halt; reconcile before enabling overlap.')
        if self.journal.unresolved_project(self.project):
            self.coordinator.close()
            raise UnresolvedExecution('This run has unresolved remote executions; reconcile saved IDs before retrying.')
        self.contexts=[]
        self.progress_keys={}
        self.image_lane=ImageLane(getattr(service.providers['comfyui'],'client_capacity',1))
        self.local_lane=ImageLane(1,'local narration/render')
        self.api_lane=ImageLane(self.settings['director'].get('parallelism',1),'director')
        self.main_context=self.admit(project,None,None,'story',digest({'sources':[c['sourceText'] for c in project['chapters']],
            'settings':self.settings}))
        self.main=self.clone(self.main_context,main=True)
        from studio_image_workers import ComfyWorkerPool
        provider=service.providers['comfyui']
        self.base_image_tasks=shot_task_limit(project, options)
        self.adaptive_image_admission=('overlapShots' not in options
            and isinstance(provider,ComfyWorkerPool)
            and adaptive_image_window(self.settings,provider.model_copies,provider.client_capacity))
        self.pool=BoundedExecutions(self.base_image_tasks,self.main.gate,
            max_workers=8 if self.adaptive_image_admission else self.base_image_tasks)
        self.chapter_dependencies=[]
        self.chapters=[chapter['id'] for chapter in project['chapters'] if chapter['sourceText'].strip()]
        self.chapter_futures={}
        self.image_tasks=self.pool.limit
        self.admitted_image_windows=set()
        self.pipeline_observations=[]
        self.pipeline_recorded=False
        self.audio_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio-audio-lookahead')
        self.render_pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio-chapter-render')
        self.audio_futures={}
        self.render_futures={}
        if self.strategy!='legacy':
            from studio_schedule import ChapterImageScheduler
            self.scheduler=ChapterImageScheduler(self,self.strategy)

    def admit(self, project, chapter, shot, kind, fingerprint, dependencies=()):
        context=self.coordinator.call(self.journal.admit,self.parent,self.project,chapter,shot,kind,
            project['revision'],fingerprint,self.settings,dependencies)
        self.contexts.append(context)
        with self.root.cv:
            self.root.active_executions[context.identity]=context
        return context

    def observer(self, context):
        return lambda event,details:self.coordinator.call(self.journal.observe,context.identity,event,details)

    @staticmethod
    def source_inputs(project):
        return [{'id':chapter['id'],'name':chapter['name'],'source':chapter['sourceText'],
            'narrationMode':chapter.get('narrationMode'),'includeChapterLabel':chapter.get('includeChapterLabel'),
            'manualNarration':chapter.get('cleanNarrationText') if chapter.get('narrationMode')=='manual' else None}
            for chapter in project['chapters']]

    def gate(self, context, message='', step=0,total=0,wait_paused=True):
        # No mutable current-job lookup: every callback owns this immutable ID.
        from studio_service import JobCancelled, AudioYield
        if wait_paused and self.scheduler:self.scheduler.check()
        with self.root.cv:
            while True:
                if self.root.closed or self.root.cancel or context.cancelled.is_set():raise JobCancelled()
                if self.root.yield_requested:raise AudioYield()
                if not self.root.paused or not wait_paused:break
                self.root.cv.wait(.1)
        if message and self.progress_keys.get(context.identity)!=message:
            self.coordinator.call(self.journal.progress,context.identity,message)
            self.progress_keys[context.identity]=message
            if context.kind!='image-qc':
                with self.root.cv:
                    self.root.db.execute('UPDATE jobs SET message=? WHERE id=?',(message,self.parent))
                    self.root.db.commit()

    def validate_settings(self):
        for future in [*getattr(self,'audio_futures',{}).values(),*getattr(self,'render_futures',{}).values()]:
            if future.done() and not future.cancelled() and future.exception() is not None:
                raise future.exception()
        current=self.root.store.load(self.project)
        if current['settings']!=self.settings:
            raise ValueError('Project settings changed during this run; saved work is retained. Start again with the current settings.')
        if self.source_inputs(current)!=self.sources:
            raise ValueError('Chapter source changed during this run; current text and saved assets retained.')
        return current

    def prefetch_audio(self, chapter):
        if self.root.config.get('studioAudioLookahead',True) is False or chapter in self.audio_futures:return
        snapshot=self.validate_settings()
        context=self.admit(snapshot,chapter,None,'narration-lookahead',
            self.main.narration_signature(snapshot,get_chapter(snapshot,chapter)),[self.main_context.identity])
        clone=self.clone(context)
        def execute():
            status,message='COMPLETE','Next chapter narration saved'
            try:
                from studio_voice_provider import location as voice_location
                remote=voice_location(snapshot['settings'])=='kaggle'
                with (nullcontext() if remote else self.local_lane.acquire(clone.gate,repair=True)):
                    current=self.validate_settings();ch=get_chapter(current,chapter)
                    if not remote:clone.unload_models();clone.before_audio()
                    # The lane is already owned; avoid reacquiring its only slot.
                    return clone.measured_execution_stage(self.project,'Narration',clone.narration,current,ch,chapter=ch['number'])
            except BaseException as error:
                status='CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED';message=str(error)[:1800];raise
            finally:
                self.coordinator.call(self.journal.finish,context.identity,status,message)
                with self.root.cv:self.root.active_executions.pop(context.identity,None)
        self.audio_futures[chapter]=self.audio_pool.submit(contextvars.copy_context().run,execute)

    def await_audio(self, chapter):
        future=self.audio_futures.get(chapter)
        if future:
            while not future.done():self.main.gate('Waiting for prefetched chapter narration');time.sleep(.05)
            future.result()

    def schedule_render(self, chapter, index, chapters):
        if self.root.config.get('studioRenderOverlap',True) is False or chapter in self.render_futures:return
        current=self.validate_settings()
        if current['intro'].get('enabled') and current['intro'].get('placement')=='every_chapter':return
        context=self.admit(current,chapter,None,'early-chapter-render',
            digest({'chapter':chapter,'revision':current['revision']}),[self.main_context.identity])
        clone=self.clone(context)
        futures=tuple(self.chapter_futures.get(chapter,[]))
        def execute():
            status,message='COMPLETE','Chapter render completed or deferred for repairs'
            try:
                for future in futures:
                    while not future.done():clone.gate('Waiting for chapter images and checks');time.sleep(.05)
                    future.result()
                p=self.validate_settings();ch=get_chapter(p,chapter)
                if any(qc_decision(p,shot,clone.store)['blocking'] for scene in ch['scenes'] for shot in scene['shots']):
                    return {'deferred':True,'reason':'Chapter needs bounded repairs before rendering.'}
                # Rendering owns the same lane as local voice; cloud image/API
                # work remains independent. Final assembly revalidates outputs.
                return clone.render_production_chapter(self.project,chapter,index,chapters)
            except BaseException as error:
                status='CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED';message=str(error)[:1800];raise
            finally:
                self.coordinator.call(self.journal.finish,context.identity,status,message)
                with self.root.cv:self.root.active_executions.pop(context.identity,None)
        self.render_futures[chapter]=self.render_pool.submit(contextvars.copy_context().run,execute)

    def finish_local_work(self):
        for future in [*self.audio_futures.values(),*self.render_futures.values()]:
            while not future.done():self.main.gate('Waiting for local narration/render work');time.sleep(.05)
            future.result()

    def receive_gate(self, context, message=''):
        """Collect receipts already bought, even when a sibling stops admission.

        This never authorizes a new dispatch or a project commit. Those retain
        their ordinary cancellation, immutable-input and selection guards.
        """
        from studio_service import JobCancelled
        with self.root.cv:
            if (self.root.closed or self.root.cancel or
                    context.cancelled.is_set() and not getattr(self,'_collecting_after_failure',False)):
                raise JobCancelled()

    def clone(self, context, main=False, snapshot=None):
        clone=copy.copy(self.root)
        clone.config=copy.deepcopy(self.root.config)
        clone.store=CoordinatedStore(self.root.store,self.coordinator)
        clone.trace=CoordinatedTrace(self.root.trace,self.coordinator)
        clone._overlap=self
        clone._timing_pause_epoch=self.root.pause_epoch
        clone.execution_context=context
        clone.gate=lambda *args,**kwargs:self.gate(context,*args,**kwargs)
        clone.checkpoint=lambda step,total,message:clone.gate(message,step,total,wait_paused=False)
        clone.director=LaneDirector(clone.config,clone.store.root,self.api_lane)
        clone.director.execution_observer=self.observer(context)
        clone.director.receive_gate=lambda message:self.receive_gate(context,message)
        clone.director.wait_for_budget=True
        clone.providers=dict(self.root.providers) if main else {
            'existing':NoLocalGeneration(),'native-flux':NoLocalGeneration()}
        initial_shot=get_shot(snapshot,context.chapter,context.shot) if snapshot is not None else None
        initial_path=initial_shot.get('imagePath') if initial_shot else None
        initial_asset=self.root.store.asset(self.project,initial_path) if initial_path else None
        initial_digest=hashlib.sha256(initial_asset.read_bytes()).hexdigest() if initial_asset and initial_asset.is_file() else None
        def validate():
            current=self.validate_settings()
            clone.check_cloud_budget(current,self.root.providers['comfyui'])
            if snapshot is not None:
                shot=get_shot(current,context.chapter,context.shot)
                if clone.visual_spec_signature(current,shot)!=context.input_hash:
                    raise StaleExecution('Shot inputs changed before dispatch; current edits retained.')
                if (shot.get('imagePath','')!=get_shot(snapshot,context.chapter,context.shot).get('imagePath','')
                        and shot.get('imageMetadata',{}).get('executionId')!=context.identity):
                    raise StaleExecution('Selected image changed before dispatch; current selection retained.')
                path=self.root.store.asset(self.project,shot['imagePath']) if shot.get('imagePath') else None
                actual=hashlib.sha256(path.read_bytes()).hexdigest() if path and path.is_file() else None
                expected=(shot['imageMetadata'].get('imageSHA256') if
                    shot.get('imageMetadata',{}).get('executionId')==context.identity else initial_digest)
                if actual!=expected:raise StaleExecution('Selected image bytes changed before dispatch; current asset retained.')
        clone.validate_execution=validate
        clone.providers['comfyui']=LaneProvider(self.root.providers['comfyui'],self.image_lane,clone.gate,
            self.observer(context),validate,retry_priority=not main)
        return clone

    def before_chapter(self, chapter):
        if self.scheduler:
            self.scheduler.check()
            return
        index=self.chapters.index(chapter)
        for earlier in self.chapters[:max(0,index-1)]:
            self.pool.wait_for(self.chapter_futures.get(earlier,[]))

    def publish_chapter(self,project,chapter,index,chapters):
        self.accepted_chapters=getattr(self,'accepted_chapters',set()) | {chapter}
        forecast=self.main.performance_forecast(project,accepted=self.accepted_chapters)
        self.scheduler.publish(project,chapter,index,chapters,forecast)
        def saved(p):
            run=p.setdefault('production',{})
            run.setdefault('scheduling',{}).update({'strategy':self.strategy,'planningReady':len(self.accepted_chapters),
                'planningChapters':len(chapters),'imageDispatchStarted':self.scheduler.started,
                'bufferSeconds':15 if self.strategy=='align' else 0,'forecast':forecast,'rentalControl':run.get('rentalControl','external')})
        self.coordinator.call(self.root.store.mutate,self.project,saved)

    def director_calls(self, project, chapter, calls, expected=None):
        """Only independent finishing/prompt passes, with separate durable owners."""
        from director_tasks import bounded_director_map,validate_director_result
        from studio_service import JobCancelled
        allowed={'planLayout','selectImageWorkflow','checkContinuity','writeImagePrompt','planStoryboard','checkStoryboard','repairStoryboard'}
        if any(method not in allowed for method, _ in calls):
            raise ValueError('This director pass requires ordered story state and cannot run here.')
        snapshot=copy.deepcopy(project)
        expected=expected or self.main.analysis_input_signature(self.root.store.load(self.project),chapter)
        parent=self.main.execution_context
        def execute(item, stopped):
            method, payload=item
            current=self.validate_settings()
            if self.main.analysis_input_signature(current,chapter)!=expected:
                raise StaleExecution('Chapter inputs changed before director dispatch; current edits retained.')
            context=self.admit(snapshot,chapter,None,'director-pass',
                digest({'method':method,'input':payload,'analysis':expected}),[parent.identity])
            clone=self.clone(context)
            ordinary_gate=clone.gate
            def dispatch_gate(*args,**kwargs):
                if stopped.is_set():raise JobCancelled()
                return ordinary_gate(*args,**kwargs)
            clone.gate=dispatch_gate
            status,message='COMPLETE','Director pass saved in validated cache'
            try:
                clone.select_director(snapshot)
                # A sibling failure stops new dispatch/retries, but must not
                # discard a paid response's receipt or completed cached result.
                clone.director.receive_gate=lambda message:self.receive_gate(context,message)
                clone.director.timing_callback=lambda stage,seconds,details:clone.record_timing(
                    self.project,stage,seconds,details | {'chapter':get_chapter(snapshot,chapter)['number'],
                        'detail':True,'parallelDirector':True,
                        'paused':self.root.pause_epoch!=clone._timing_pause_epoch})
                result=getattr(clone.director,method)(copy.deepcopy(payload),dispatch_gate)
                validate_director_result(method,payload,result)
                latest=self.validate_settings()
                if self.main.analysis_input_signature(latest,chapter)!=expected:
                    raise StaleExecution('Chapter changed during direction; earlier result cached and current edits retained.')
                return result
            except BaseException as error:
                status='SUPERSEDED' if isinstance(error,StaleExecution) else (
                    'CANCELLED' if isinstance(error,JobCancelled) else 'FAILED')
                message=str(error)[:1800]
                raise
            finally:
                actual=self.coordinator.call(self.journal.finish,context.identity,status,message)
                with self.root.cv:self.root.active_executions.pop(context.identity,None)
                if actual=='UNKNOWN' and status=='COMPLETE':
                    raise UnresolvedExecution('Director pass has unresolved remote usage; reconcile its saved ID before retrying.')
        return bounded_director_map(calls,execute,self.main.gate,
            self.settings['director'].get('parallelism',1))

    @contextmanager
    def stage(self, stage, args, details):
        snapshot=next((arg for arg in args if isinstance(arg,dict) and arg.get('id')==self.project
            and 'chapters' in arg),None) or self.main.store.load(self.project)
        number=details.get('chapter')
        chapter=next((ch['id'] for ch in snapshot['chapters'] if ch['number']==number),None)
        context=self.admit(snapshot,chapter,details.get('shot'),stage,
            digest({'stage':stage,'snapshot':snapshot,'details':details}),self.chapter_dependencies)
        previous=self.main.execution_context
        self.main.execution_context=context
        self.main.gate=lambda *args,**kwargs:self.gate(context,*args,**kwargs)
        self.main.director.execution_observer=self.observer(context)
        provider=self.main.providers['comfyui']
        previous_validator=provider.validator
        provider.observer=self.observer(context)
        provider.gate=self.main.gate
        if stage=='Character reference':
            character_options=next(arg for arg in args if isinstance(arg,dict) and 'characterId' in arg)
            character_id=character_options['characterId']
            expected=self.main.reference_input_signature(snapshot,character_id)
            def validate_reference():
                previous_validator()
                current=self.main.store.load(self.project)
                if self.main.reference_input_signature(current,character_id)!=expected:
                    raise StaleExecution('Character reference inputs changed before dispatch; current edits retained.')
            provider.validator=validate_reference
        status,message='COMPLETE','Stage saved'
        try:yield
        except BaseException as error:
            status='CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED'
            message=str(error)[:1800]
            raise
        finally:
            actual=self.coordinator.call(self.journal.finish,context.identity,status,message)
            with self.root.cv:self.root.active_executions.pop(context.identity,None)
            self.main.execution_context=previous
            self.main.gate=lambda *args,**kwargs:self.gate(previous,*args,**kwargs)
            self.main.director.execution_observer=self.observer(previous)
            provider.observer=self.observer(previous)
            provider.gate=self.main.gate
            provider.validator=previous_validator
            if actual=='UNKNOWN' and status=='COMPLETE':
                raise UnresolvedExecution('Stage '+context.identity+' has unresolved remote work; saved results retained.')

    def image_admission_details(self, aggregate=False):
        if not self.adaptive_image_admission:return {}
        windows=self.admitted_image_windows if aggregate and self.admitted_image_windows else {self.pool.limit}
        return {'imageAdmissionPolicy':ADAPTIVE_IMAGE_ADMISSION,
                'imageTasks':max(windows),'imageWindowMin':min(windows),'imageWindowMax':max(windows)}

    def update_image_window(self, project, review_only=False):
        """Runtime proof is checked after GPU startup, before durable admission.

        The provider and API lanes remain four slots. More lifecycle permits
        allow existing image work to continue while other shots await QC.
        """
        allowed=False
        if self.adaptive_image_admission and not review_only:
            from studio_image_workers import ComfyWorkerPool
            from studio_snapshot_budget import adaptive_snapshot_room
            from studio_render import VideoRenderer
            provider=self.root.providers['comfyui']
            proof=getattr(provider,'confirmation',None)
            rows=proof.get('workers',[]) if isinstance(proof,dict) else []
            verified=(isinstance(provider,ComfyWorkerPool) and not getattr(provider,'quarantined',True)
                and adaptive_image_window(self.settings,getattr(provider,'model_copies',None),
                                          getattr(provider,'client_capacity',None))
                and self.image_lane.capacity==4 and self.api_lane.capacity==4
                and isinstance(proof,dict) and proof.get('gpu')=='NVIDIA GeForce RTX 5090'
                and isinstance(rows,list) and len(rows)==2
                and all(isinstance(row,dict) and type(row.get('pid')) is int and row['pid']>0
                        and row.get('port')==8188+i for i,row in enumerate(rows))
                and rows[0]['pid']!=rows[1]['pid'])
            if verified:
                allowed=adaptive_snapshot_room(project,VideoRenderer.available_memory_bytes())
        self.pool.set_limit(8 if allowed else self.base_image_tasks)
        self.image_tasks=self.pool.limit

    def submit_shot(self, project, chapter, shot, review_only=False):
        # Backpressure happens before durable admission, bounding the journal too.
        # Recheck after a blocking wait: a worker can be quarantined, or the
        # host can lose memory while previously bought work is still finishing.
        while True:
            self.pool.ready()
            self.update_image_window(project,review_only)
            if len(self.pool.pending)<self.pool.limit:break
        self.validate_settings()
        admitted_window=self.pool.limit
        admission_details={'imageTasks':admitted_window,**self.image_admission_details()}
        snapshot=copy.deepcopy(project)
        signature=self.main.visual_spec_signature(snapshot,shot)
        context=self.admit(snapshot,chapter,shot['id'],'image-qc',signature,self.chapter_dependencies)
        clone=self.clone(context,snapshot=snapshot)
        def execute():
            status,message='COMPLETE','Image saved with the selected check policy'
            try:
                clone.validate_execution()
                if review_only:
                    result=clone.inspect_shot(snapshot,chapter,shot['id'])
                    if not result['selected']:raise StaleExecution('Shot changed during QC; current selection retained.')
                    qc=result['qc']
                    current=clone.store.load(self.project)
                    if qc_decision(current,get_shot(current,chapter,shot['id']),clone.store)['blocking']:
                        if snapshot['settings'].get('unattendedProduction'):
                            return {'status':'REVIEW_REQUIRED','qc':qc}
                        if clone.collect_production_reviews(current) and not automatic_repairs(self.settings,qc):
                            return {'status':'REVIEW_REQUIRED','qc':qc}
                        if not automatic_repairs(self.settings,qc):
                            raise RuntimeError('Saved image needs your review. No repair was purchased; accept it to continue.')
                        current=clone.store.load(self.project)
                        from studio_automation import repair_operation
                        operation=repair_operation(current,get_shot(current,chapter,shot['id']),clone.provider('comfyui'))
                        result=clone.generate(current,chapter,shot['id'],shot['generationSettings']['seed'],
                            {'operation':operation,'budgetedQCRepair':True,'_repairPrompt':qc.get('repairPrompt') or shot['prompt']})
                else:
                    result=clone.measured_stage(self.project,'Image + quality checks',clone.generate,
                        snapshot,chapter,shot['id'],shot['generationSettings']['seed'],
                        {'_collectReview':clone.collect_production_reviews(snapshot),
                         'deferAutomaticRepair':snapshot['settings'].get('unattendedProduction',False)},
                        chapter=get_chapter(snapshot,chapter)['number'],shot=shot['id'],executionId=context.identity,
                        **admission_details)
                if isinstance(result,dict) and result.get('status')=='SUPERSEDED':
                    raise StaleExecution('Shot changed during generation or QC; current edits retained before rendering.')
                return result
            except BaseException as error:
                status='SUPERSEDED' if isinstance(error,StaleExecution) else (
                    'CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED')
                message=str(error)[:1800]
                if (type(error).__name__=='JobCancelled' and context.cancelled.is_set()
                        and not self.root.cancel and not self.root.closed):
                    return {'status':'CANCELLED','executionId':context.identity}
                raise
            finally:
                actual=self.coordinator.call(self.journal.finish,context.identity,status,message)
                with self.root.cv:self.root.active_executions.pop(context.identity,None)
                if actual=='UNKNOWN' and status=='COMPLETE':
                    raise UnresolvedExecution('Execution '+context.identity+' completed without confirmed remote usage; retained for reconciliation.')
        try:
            future=self.pool.submit(execute)
            self.admitted_image_windows.add(admitted_window)
            self.chapter_futures.setdefault(chapter,[]).append(future)
            return future
        except BaseException:
            self.coordinator.call(self.journal.finish,context.identity,'CANCELLED','Cancelled before worker admission')
            with self.root.cv:self.root.active_executions.pop(context.identity,None)
            raise

    def drain(self):
        if self.scheduler:self.scheduler.finish()
        self.pool.drain()
        if any(context.cancelled.is_set() for context in self.contexts):
            raise ValueError('A shot task was cancelled. Other submitted tasks finished; saved assets and unresolved receipts are retained.')
        if not self.pipeline_recorded:
            self.pipeline_recorded=True
            rows=self.pipeline_observations
            # Per-shot latency includes queue waits and concurrent QC. Use the
            # union of completed intervals once, never sum those latencies.
            if rows and all(r['eligible'] for r in rows) and len({r['profile'] for r in rows})==1:
                from studio_performance import interval_union
                seconds=interval_union([(r['start'],r['end']) for r in rows])
                details={'status':'COMPLETE','images':len(rows),'shot':rows[0]['shot'],'detail':True,
                        'imageTasks':max(self.admitted_image_windows or {self.image_tasks}),
                        'timingSource':'busy-interval-union',**self.image_admission_details(aggregate=True),
                        'note':'Delivered-image throughput includes selected reviews and waiting inside active tasks; nested shot latencies are not added.'}
                self.main.record_timing(self.project,'Image pipeline',seconds,details)

    def close(self, status, message=''):
        self._collecting_after_failure=status not in ('COMPLETE','CANCELLED') and not self.root.cancel
        if status!='COMPLETE':
            for context in self.contexts:context.cancelled.set()
            with self.root.cv:self.root.cv.notify_all()
        if self.scheduler:self.scheduler.close()
        self.pool.close()
        self.audio_pool.shutdown(wait=True,cancel_futures=True)
        self.render_pool.shutdown(wait=True,cancel_futures=True)
        self.coordinator.call(self.journal.finish,self.main_context.identity,status,message)
        with self.root.cv:
            for context in self.contexts:self.root.active_executions.pop(context.identity,None)
        self.coordinator.close()
