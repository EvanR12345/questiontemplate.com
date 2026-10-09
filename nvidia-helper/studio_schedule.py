"""Separate ordered story planning from bounded cloud image admission.

The clock selects image dispatch; it does not secretly rent or stop a GPU.
Unknown forecasts wait for completed plans instead of inventing a start time.
"""
import copy
import math
import threading
import time
from collections import deque
from studio_data import digest,get_chapter

LABELS={'align':'Timed · align Luna + images · 15s buffer',
    'fastest':'Timed · keep fastest video','legacy':'Existing chapter-by-chapter workflow'}


def strategy(options):
    value=options.get('generationStrategy','legacy')
    if value not in LABELS:raise ValueError('Choose a supported generation strategy.')
    if value!='legacy' and not options.get('overlap'):
        raise ValueError('Timed generation needs cloud overlap, ComfyUI images and Luna.')
    return value


def start_decision(mode,remaining,backlog,finished=False):
    if mode=='fastest':return {'start':True,'reason':'Start the first accepted ready chapter; planning continues independently.'}
    if finished:return {'start':True,'reason':'All plans are ready; dispatch images now.'}
    if any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in (remaining,backlog)):
        return {'start':False,'reason':'Matching timing history is incomplete; keep planning before image dispatch.'}
    return {'start':backlog>=max(0,remaining-15),
        'reason':'Start when measured ready-image work can cover the forecast planning tail, allowing 15 seconds.'}


def chapter_signature(service,p,chid):
    ch=get_chapter(p,chid)
    # References are resolved and fingerprinted at image admission by the
    # existing per-shot guard. Automatic portrait creation in an earlier
    # chapter must not invalidate already accepted later chapter plans.
    fields=('id','characters','camera','location','locationId','action','pose',
        'expression','lighting','narrationSegment','continuity','intentionalAppearanceChanges',
        'prompt','negativePrompt','imageProvider','imageModel','workflow','generationSettings',
        'manual','start','end','referenceImages')
    shots=[s for sc in ch['scenes'] for s in sc['shots']]
    return digest({'source':ch['sourceText'],'narration':ch['cleanNarrationText'],
        'audio':ch.get('audio',{}).get('signature'),'proposal':ch.get('proposedPlan'),
        'style':p['settings']['style'],
        'shots':[{k:s.get(k) for k in fields} for s in shots]})


class ChapterImageScheduler:
    def __init__(self,overlap,mode):
        self.overlap=overlap;self.mode=mode
        self.cv=threading.Condition();self.ready=deque()
        self.finished=False;self.closed=False;self.error=None
        self.started=False;self.forecast=None;self.forecast_at=time.monotonic()
        self.forecast_pause_epoch=self.overlap.root.pause_epoch
        self.thread=threading.Thread(target=self._run,name='studio-image-admission',daemon=True)
        self.thread.start()

    def publish(self,p,chid,index,chapters,forecast):
        self.check()
        # Only IDs/signatures are buffered; project/media copies remain durable.
        ids={person['id'] for sc in get_chapter(p,chid)['scenes'] for shot in sc['shots']
            for person in shot.get('characters',[]) if person['type']=='main'}
        missing_references=any(person['id'] in ids and not person.get('references') for person in p['characters'])
        item=(chid,index,tuple(chapters),chapter_signature(self.overlap.main,p,chid),missing_references)
        with self.cv:
            if self.finished or self.closed:raise RuntimeError('Image admission has already closed.')
            self.ready.append(item);self.forecast=copy.deepcopy(forecast)
            self.forecast_pause_epoch=self.overlap.root.pause_epoch
            self.forecast_at=time.monotonic();self.cv.notify_all()

    def check(self):
        if self.error is not None:raise self.error

    def _run(self):
        try:
            while True:
                with self.cv:
                    if self.closed:return
                    if not self.ready:
                        if self.finished:break
                        self.cv.wait(.1);continue
                    # Generating a missing canonical portrait while Luna is
                    # planning changes its reference inputs. Existing portraits
                    # can overlap; new portraits wait for ordered planning.
                    if self.ready[0][4] and not self.finished:
                        self.cv.wait(.1);continue
                    report=self.forecast or {}
                    remaining=report.get('planningSeconds')
                    if self.overlap.root.pause_epoch!=self.forecast_pause_epoch:remaining=None
                    if remaining is not None:remaining=max(0,remaining-(time.monotonic()-self.forecast_at))
                    decision=start_decision(self.mode,remaining,report.get('readyImageSeconds'),self.finished)
                    if not self.started and not decision['start']:
                        self.cv.wait(.1);continue
                    item=self.ready.popleft();self.started=True
                self.overlap.gate(self.overlap.main_context,'Starting accepted chapter images')
                chid,index,chapters,expected,_missing_references=item
                p=self.overlap.validate_settings()
                if chapter_signature(self.overlap.main,p,chid)!=expected:
                    from studio_overlap import StaleExecution
                    raise StaleExecution('Accepted chapter changed before image admission; current edits and saved work retained.')
                self.overlap.coordinator.call(self.overlap.root.store.mutate,self.overlap.project,
                    lambda q:q.setdefault('production',{}).setdefault('scheduling',{}).update(
                        imageDispatchStarted=True,dispatchReason=decision['reason']))
                context=self.overlap.admit(p,chid,None,'chapter-images',expected,[self.overlap.main_context.identity])
                clone=self.overlap.clone(context)
                status,message='COMPLETE','Accepted chapter image tasks admitted'
                try:clone.generate_production_chapter_images(self.overlap.project,chid,index,list(chapters))
                except BaseException as error:
                    status='CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED'
                    message=str(error)[:1800];raise
                finally:
                    actual=self.overlap.coordinator.call(self.overlap.journal.finish,context.identity,status,message)
                    with self.overlap.root.cv:self.overlap.root.active_executions.pop(context.identity,None)
                    if actual=='UNKNOWN' and status=='COMPLETE':
                        from studio_execution import UnresolvedExecution
                        raise UnresolvedExecution('Image admission has unresolved remote usage; reconcile its saved ID before retrying.')
            self.overlap.pool.drain()
        except BaseException as error:self.error=error

    def finish(self):
        with self.cv:self.finished=True;self.cv.notify_all()
        while self.thread.is_alive():
            self.overlap.gate(self.overlap.main_context,'Waiting for accepted chapter images')
            self.thread.join(.1)
        self.check()

    def close(self):
        with self.cv:self.closed=True;self.cv.notify_all()
        self.thread.join()
