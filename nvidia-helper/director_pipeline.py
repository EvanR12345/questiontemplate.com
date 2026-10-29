"""Overlap accepted visual work with later ordered facts, never canonical commits.

The existing director map still owns durable request IDs, API lanes, budget,
stale-input checks and validated response caches. This queue adds one producer
thread and at most two frozen factual-group batches, not more API slots.
"""
from studio_director_limits import validate_source_visual_overlap, fact_beats_mode
import contextvars
import copy
import threading
from concurrent.futures import Future, ThreadPoolExecutor


class AcceptedVisualQueue:
    def __init__(self,service,project,chapter,expected,enabled=False):
        self.service=service;self.project=project;self.chapter=chapter;self.expected=expected
        self.enabled=enabled;self.pending=[];self.results=[];self.failure=None
        self.lock=threading.RLock();self.stopped=threading.Event()
        self.cv=threading.Condition(self.lock);self.groups=[];self.active={}
        overlap=getattr(service,'_overlap',None)
        factory=getattr(overlap,'_director_executor',None)
        capacity=getattr(getattr(overlap,'api_lane',None),'capacity',None)
        parallelism=project.get('settings',{}).get('director',{}).get('parallelism',1)
        # A custom/serial provider has no request-owned executor contract. Keep
        # its original director_calls path instead of sharing mutable adapters.
        self.request_dispatch=(enabled and getattr(overlap,'main',None) is service
            and callable(factory) and type(capacity) is int and capacity>0
            and type(parallelism) is int and 1<parallelism<=8)
        self.factory=factory if self.request_dispatch else None
        self.limit=min(capacity,parallelism) if self.request_dispatch else 1
        self.pool=ThreadPoolExecutor(max_workers=self.limit,thread_name_prefix='studio-accepted-visuals') if enabled else None
        self.dispatcher=None
        if self.request_dispatch:
            self.dispatcher=threading.Thread(target=self._dispatch,name='studio-accepted-visual-admission',daemon=True)
            self.dispatcher.start()

    def __enter__(self):return self

    def check(self):
        with self.lock:error=self.failure
        if error is not None:raise error

    def _fail(self,error):
        # Close every sibling's dispatch/retry gate immediately, before waiting
        # for the admission lock. Paid streams retain their separate receive gate.
        self.stopped.set()
        with self.cv:
            if self.failure is None:self.failure=error
            self.cv.notify_all()

    def _invoke(self,execute,item):
        try:
            if self.stopped.is_set():raise RuntimeError('Accepted visual dispatch stopped before submission.')
            return execute(item,self.stopped)
        except BaseException as error:
            self._fail(error)
            raise

    def _dispatch(self):
        """One bounded map across at most two frozen, ordered fact groups.

        Reuse the production map's request executor, including its distinct
        journal owner, stale checks, reservations, retry gate and receipt drain.
        Unlike two independent maps, every callback sees the SAME stop event.
        """
        try:
            while True:
                with self.cv:
                    # Observe every finished error before admitting another call.
                    for future in [f for f in self.active if f.done()]:
                        group,index=self.active.pop(future)
                        try:group['results'][index]=future.result()
                        except BaseException as error:
                            self.stopped.set()
                            if self.failure is None:self.failure=error
                        group['remaining']-=1
                    if self.stopped.is_set():
                        # Cancel callbacks that never started; collect active paid
                        # work before resolving failed group futures or shutdown.
                        for future in self.active:future.cancel()
                        if not self.active:
                            error=self.failure or RuntimeError('Accepted visual dispatch stopped before submission.')
                            for group in self.groups:
                                if not group['future'].done():group['future'].set_exception(error)
                            self.groups=[]
                            return
                    else:
                        for group in self.groups:
                            if group['remaining']==0 and not group['future'].done():
                                group['future'].set_result(group['results'])
                        self.groups=[g for g in self.groups if not g['future'].done()]
                        # All first-group requests are offered first. Once they
                        # are in flight, its slow tail cannot strand spare slots.
                        for group in self.groups:
                            while group['next']<len(group['calls']) and len(self.active)<self.limit:
                                if self.stopped.is_set():break
                                index=group['next'];group['next']+=1
                                future=self.pool.submit(group['context'].copy().run,
                                    self._invoke,group['execute'],group['calls'][index])
                                self.active[future]=(group,index)
                                future.add_done_callback(self._wake)
                            if self.stopped.is_set() or len(self.active)>=self.limit:break
                    self.cv.wait(.05)
        except BaseException as error:
            self._fail(error)
            # A dispatcher failure must still drain callbacks and unblock callers.
            for future in list(self.active):
                future.cancel()
                if not future.cancelled():
                    try:future.result()
                    except BaseException:pass
            with self.cv:
                for group in self.groups:
                    if not group['future'].done():group['future'].set_exception(error)
                self.groups=[]

    def _wake(self,_future):
        with self.cv:self.cv.notify_all()

    def _execute(self,project,calls):
        if self.stopped.is_set():raise RuntimeError('Accepted visual dispatch stopped before submission.')
        try:
            return self.service.director_calls(project,self.chapter,calls,self.expected)
        except BaseException as error:
            self._fail(error)
            raise

    def add(self,calls):
        if not self.enabled:return
        self.check()
        if len(self.pending)>=2:
            self.results.extend(self.pending.pop(0).result())
        self.check()
        # Nothing in a later fact pass can mutate this accepted group's input.
        project=copy.deepcopy(self.project);frozen=copy.deepcopy(calls)
        if self.request_dispatch:
            try:execute=self.factory(project,self.chapter,self.expected)
            except BaseException as error:
                self._fail(error);raise
            future=Future()
            group={'calls':frozen,'next':0,'remaining':len(frozen),'results':[None]*len(frozen),
                'execute':execute,'future':future,'context':contextvars.copy_context()}
            with self.cv:
                if self.stopped.is_set():
                    raise self.failure or RuntimeError('Accepted visual dispatch stopped before submission.')
                self.groups.append(group);self.pending.append(future);self.cv.notify_all()
        else:
            self.pending.append(self.pool.submit(contextvars.copy_context().run,self._execute,project,frozen))

    def finish(self,calls):
        if not self.enabled:
            return self.service.director_calls(self.project,self.chapter,calls,self.expected)
        for future in self.pending:self.results.extend(future.result())
        self.pending=[];self.check()
        return self.results

    def __exit__(self,*_):
        self.stopped.set()
        with self.cv:self.cv.notify_all()
        # Unsubmitted batches can be cancelled. Active paid requests use their
        # existing receive gate to collect receipts, even on a source failure.
        if self.dispatcher:self.dispatcher.join()
        if self.pool:self.pool.shutdown(wait=True,cancel_futures=True)






def source_visual_overlap_enabled(service,director):
    value=validate_source_visual_overlap(director)
    overlap=getattr(service,'_overlap',None)
    return value and overlap is not None and service is overlap.main and director.get('parallelism',1)>1


def focused_storyboard_review_context(context):
    """Research-only text-fidelity view; never shorten generation or repairs.

    Preserve every source sentence, identity, appearance/object fact, dialogue
    cue, action, pose, lighting and spatial composition. Omit only production
    preferences, absolute audio times and motion/transition instructions, which
    cannot establish whether a shot contradicts the author's story.
    """
    fields=('sentences','people','priorState','acceptedChanges','analysis','speakerHints',
            'speakerHintInstruction','shots','cameras','reviewShotIndices','repairIndices','adjacentNarration')
    focused={key:copy.deepcopy(context[key]) for key in fields if key in context}
    focused['sentences']=[{k:v for k,v in sentence.items() if k not in ('start','end')}
                           for sentence in focused['sentences']]
    focused['shots']=[{k:v for k,v in shot.items() if k not in ('sceneIndex','motion','transition','timingRepair','alternateDirections')}
                       for shot in focused['shots']]
    # Keep framing/angles too: conservative first test, no assumed QC savings.
    return focused
