"""Overlap accepted visual work with later ordered facts, never canonical commits.

The existing director map still owns durable request IDs, API lanes, budget,
stale-input checks and validated response caches. This queue adds one producer
thread and at most two frozen factual-group batches, not more API slots.
"""
import contextvars
import copy
import threading
from concurrent.futures import ThreadPoolExecutor


class AcceptedVisualQueue:
    def __init__(self,service,project,chapter,expected,enabled=False):
        self.service=service;self.project=project;self.chapter=chapter;self.expected=expected
        self.enabled=enabled;self.pending=[];self.results=[];self.failure=None
        self.lock=threading.Lock();self.stopped=threading.Event()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='studio-accepted-visuals') if enabled else None

    def __enter__(self):return self

    def check(self):
        with self.lock:error=self.failure
        if error is not None:raise error

    def _execute(self,project,calls):
        if self.stopped.is_set():raise RuntimeError('Accepted visual dispatch stopped before submission.')
        try:
            return self.service.director_calls(project,self.chapter,calls,self.expected)
        except BaseException as error:
            with self.lock:
                if self.failure is None:self.failure=error
            self.stopped.set()
            raise

    def add(self,calls):
        if not self.enabled:return
        self.check()
        if len(self.pending)>=2:
            self.results.extend(self.pending.pop(0).result())
        self.check()
        # Nothing in a later fact pass can mutate this accepted group's input.
        project=copy.deepcopy(self.project);frozen=copy.deepcopy(calls)
        self.pending.append(self.pool.submit(contextvars.copy_context().run,self._execute,project,frozen))

    def finish(self,calls):
        if not self.enabled:
            return self.service.director_calls(self.project,self.chapter,calls,self.expected)
        for future in self.pending:self.results.extend(future.result())
        self.pending=[];self.check()
        return self.results

    def __exit__(self,*_):
        self.stopped.set()
        # Unsubmitted batches can be cancelled. Active paid requests use their
        # existing receive gate to collect receipts, even on a source failure.
        if self.pool:self.pool.shutdown(wait=True,cancel_futures=True)


def validate_source_visual_overlap(director):
    value=director.get('sourceVisualOverlap',False)
    if type(value) is not bool:raise ValueError('Accepted source/visual overlap must be enabled or disabled.')
    if value and director.get('executionMode')!='staged-lean':
        raise ValueError('Accepted source/visual overlap requires Lean Luna.')
    return value


def fact_beats_mode(director):
    value=director.get('factBeatsMode','analyst')
    if value not in ('analyst','storyboard'):raise ValueError('Choose analyst or storyboard emotional-beat ownership.')
    if value=='storyboard' and director.get('executionMode')!='staged-lean':
        raise ValueError('Storyboard-owned beats require Lean Luna.')
    return value


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
