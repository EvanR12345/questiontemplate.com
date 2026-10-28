"""Bounded durable QC descriptors; image pixels and project snapshots stay on disk."""
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DESCRIPTOR_LIMIT=8192
DESCRIPTOR_MAX_BYTES=4096
DESCRIPTOR_BYTES=DESCRIPTOR_LIMIT*DESCRIPTOR_MAX_BYTES
DISK_HEADROOM=256*2**20


def capacity_preflight(project,shots,folder,max_repairs):
    count=len(shots)*(1+max_repairs)
    if count>DESCRIPTOR_LIMIT:raise ValueError('Dense image/QC descriptor capacity is too small for this complete burst and its existing repair ceiling.')
    # Uncompressed RGBA plus PNG/sidecar overhead for every possible new asset.
    needed=sum(int(s['generationSettings']['width'])*int(s['generationSettings']['height'])*4+131072
        for s in shots)*(1+max_repairs)+DESCRIPTOR_BYTES+DISK_HEADROOM
    free=shutil.disk_usage(folder).free
    if free<needed:raise ValueError('Dense images require enough free disk for the whole burst, bounded repairs and safety headroom before GPU startup.')
    return {'plannedImages':len(shots),'maximumDescriptors':count,'descriptorLimit':DESCRIPTOR_LIMIT,
        'requiredFreeBytes':needed,'availableFreeBytes':free,'headroomBytes':DISK_HEADROOM}


class DeferredQC:
    def __init__(self,folder,parent,recover=None):
        self.lock=threading.Lock()
        name=hashlib.sha256(parent.encode()).hexdigest()[:24]
        self.path=Path(folder)/('dense-qc-'+name+'.sqlite')
        self.db=sqlite3.connect(self.path,check_same_thread=False)
        self.db.execute('PRAGMA synchronous=FULL');self.db.execute('PRAGMA cache_size=-256')
        self.db.execute('CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, payload TEXT NOT NULL, bytes INTEGER NOT NULL, status TEXT NOT NULL, execution TEXT, updated REAL NOT NULL)')
        self.db.commit()
        unresolved=self.db.execute("SELECT id,payload,execution FROM tasks WHERE status IN ('CLAIMED','UNKNOWN')").fetchall()
        for identity,payload,execution in unresolved:
            # Recovery never authorizes another purchase. Only a proven saved
            # review and a reconciled complete execution can close this row.
            if recover is not None and recover(json.loads(payload),execution):
                self.db.execute("UPDATE tasks SET status='COMPLETE',updated=? WHERE id=?",(time.time(),identity))
        self.db.commit()
        if self.db.execute("SELECT 1 FROM tasks WHERE status IN ('CLAIMED','UNKNOWN') LIMIT 1").fetchone():
            self.db.close();raise RuntimeError('Interrupted dense QC has unresolved execution ownership; reconcile saved IDs before retrying.')

    def enqueue(self,descriptor):
        encoded=json.dumps(descriptor,sort_keys=True,separators=(',',':'))
        size=len(encoded.encode())
        if size>DESCRIPTOR_MAX_BYTES:raise ValueError('Dense QC descriptor exceeds its bounded metadata allowance.')
        identity=hashlib.sha256(json.dumps({k:v for k,v in descriptor.items() if k not in ('revision','parent')},sort_keys=True).encode()).hexdigest()
        with self.lock:
            if self.db.execute('SELECT 1 FROM tasks WHERE id=?',(identity,)).fetchone():return identity
            count,total=self.db.execute('SELECT COUNT(*),COALESCE(SUM(bytes),0) FROM tasks').fetchone()
            if count>=DESCRIPTOR_LIMIT or total+size>DESCRIPTOR_BYTES:
                raise ValueError('Dense QC descriptor capacity exhausted; no additional work admitted.')
            self.db.execute("INSERT INTO tasks VALUES (?,?,?,'PENDING',NULL,?)",(identity,encoded,size,time.time()))
            self.db.commit()
        return identity

    def pending(self):
        with self.lock:
            row=self.db.execute("SELECT id,payload FROM tasks WHERE status='PENDING' ORDER BY rowid LIMIT 1").fetchone()
        return (row[0],json.loads(row[1])) if row else None

    def pending_tasks(self):
        with self.lock:return [json.loads(row[0]) for row in self.db.execute("SELECT payload FROM tasks WHERE status='PENDING' ORDER BY rowid")]

    def chapter_ready(self,chapter,confirmed):
        with self.lock:
            for payload,status,execution in self.db.execute("SELECT payload,status,execution FROM tasks WHERE status!='COMPLETE'"):
                if json.loads(payload)['chapter']==chapter and not (status=='CLAIMED' and execution in confirmed):return False
        return True

    def claim(self,identity,execution):
        with self.lock:
            changed=self.db.execute("UPDATE tasks SET status='CLAIMED',execution=?,updated=? WHERE id=? AND status='PENDING'",(execution,time.time(),identity)).rowcount
            self.db.commit()
        return bool(changed)

    def finish(self,identity,status):
        if status not in ('COMPLETE','STALE','FAILED','CANCELLED','UNKNOWN'):raise ValueError('Invalid dense QC terminal state')
        with self.lock:
            self.db.execute('UPDATE tasks SET status=?,updated=? WHERE id=?',(status,time.time(),identity));self.db.commit()

    def snapshot(self):
        with self.lock:
            counts=dict(self.db.execute('SELECT status,COUNT(*) FROM tasks GROUP BY status').fetchall())
            size=self.db.execute('SELECT COALESCE(SUM(bytes),0) FROM tasks').fetchone()[0]
        return {'counts':counts,'descriptorBytes':size,'descriptorLimit':DESCRIPTOR_LIMIT,'descriptorByteLimit':DESCRIPTOR_BYTES}

    def close(self):
        with self.lock:self.db.close()


def qc_snapshot(project,chapter,shot):
    """Fresh request-local projection; preserve every fact used by this check."""
    from studio_data import get_chapter,get_shot
    view=dict(project)
    view['production']={k:v for k,v in project.get('production',{}).items() if k!='timings'}
    intro=dict(project['intro']);view['intro']=intro
    if chapter is None:
        intro['shots']=[next(s for s in intro.get('shots',[]) if s['id']==shot)]
        view['chapters']=[]
    else:
        current=get_chapter(project,chapter);selected=get_shot(project,chapter,shot)
        scene=next(sc for sc in current['scenes'] if any(s['id']==shot for s in sc['shots']))
        view['chapters']=[{**current,'scenes':[{**scene,'shots':[selected]}]}]
        intro['shots']=[]
    return view


class DenseReviews:
    """Independent bounded QC consumers never occupy image-generation permits."""
    def __init__(self,overlap):
        self.owner=overlap
        self.queue=DeferredQC(overlap.root.store.folder(overlap.project),overlap.parent,self._recover)
        try:
            counts=self.queue.snapshot()['counts']
            if any(counts.get(name) for name in ('FAILED','STALE','CANCELLED')):
                raise RuntimeError('This dense QC job has failed or superseded work. Reconcile ownership and explicitly start a new job; no rental started.')
            pending=self.queue.pending_tasks()
            if pending:
                current=overlap.validate_settings()
                for task in pending:self._bound(task,current)
        except BaseException:
            self.queue.close();raise
        self.cv=threading.Condition();self.closed=False;self.error=None;self.active={};self.confirmed=set()
        self.workers=min(4,overlap.api_lane.capacity)
        self.pool=ThreadPoolExecutor(max_workers=self.workers,thread_name_prefix='studio-dense-qc')
        self.thread=threading.Thread(target=self._run,name='studio-dense-qc-admission',daemon=True)
        self.thread.start()

    def _recover(self,task,execution):
        try:
            records=self.owner.coordinator.call(self.owner.journal.snapshot,self.owner.parent)
            row=next((r for r in records if r['id']==execution),None)
            if (not row or row['status']!='COMPLETE' or row['input_hash']!=task['spec']
                    or any(op['state']=='SUBMITTED' for op in row['operations'].values())):return False
            current=self._bound(task)
            from studio_data import get_shot
            shot=next(s for s in current['intro']['shots'] if s['id']==task['shot']) if task['chapter'] is None else get_shot(current,task['chapter'],task['shot'])
            qc=shot.get('qc',{})
            return bool(type(qc.get('pass')) is bool and qc.get('status') not in (None,'PENDING','UNREVIEWED')
                and qc.get('checkedImagePath')==task['path'] and qc.get('checkedImageSHA256')==task['imageSHA256']
                and qc.get('shotSpecSignature')==task['spec'] and qc.get('reviewSettingsSignature')==task['policy'])
        except (KeyError,ValueError,RuntimeError,StopIteration):return False

    def check(self):
        if self.error is not None:raise self.error

    def chapter_ready(self,chapter):
        self.check()
        with self.cv:confirmed=set(self.confirmed)
        return self.queue.chapter_ready(chapter,confirmed)

    def enqueue(self,project,chapter,shot,parent):
        from studio_qc import should_check,review_settings_signature
        if not should_check(project,shot):return
        path=shot.get('imagePath')
        if not path:raise ValueError('Dense QC needs a durable selected image')
        task={'project':project['id'],'chapter':chapter,'shot':shot['id'],'path':path,
            'imageSHA256':self.owner.root.store.asset_digest(project['id'],path),
            'spec':self.owner.main.visual_spec_signature(project,shot),
            'policy':review_settings_signature(project['settings']),
            'revision':project['revision'],'parent':parent}
        self.queue.enqueue(task)
        with self.cv:self.cv.notify_all()

    def _bound(self,task,current=None):
        from studio_data import get_shot
        from studio_qc import review_settings_signature
        from studio_overlap import StaleExecution
        if current is None:current=self.owner.validate_settings()
        shot=(next(s for s in current['intro'].get('shots',[]) if s['id']==task['shot'])
            if task['chapter'] is None else get_shot(current,task['chapter'],task['shot']))
        if (shot.get('imagePath')!=task['path'] or
                self.owner.root.store.asset_digest(task['project'],task['path'])!=task['imageSHA256'] or
                self.owner.main.visual_spec_signature(current,shot)!=task['spec'] or
                review_settings_signature(current['settings'])!=task['policy']):
            raise StaleExecution('Dense QC image, source, reference bytes, specification or policy changed; current edits retained.')
        return current

    def _review(self,identity,task,context):
        from studio_execution import UnresolvedExecution
        from studio_overlap import StaleExecution
        status,message='COMPLETE','Deferred image QC completed'
        descriptor_status='COMPLETE'
        try:
            self.check();self.owner.gate(context,'Checking saved dense image')
            current=self._bound(task)
            snapshot=qc_snapshot(current,task['chapter'],task['shot']);del current
            clone=self.owner.clone(context)
            if task['chapter'] is None:clone.inspect_intro(snapshot,sampled_only=True,shot_id=task['shot'])
            else:
                result=clone.inspect_shot(snapshot,task['chapter'],task['shot'])
                if not result['selected']:raise StaleExecution('Dense QC result was superseded; current selection retained.')
            self._bound(task)
        except BaseException as error:
            status='SUPERSEDED' if isinstance(error,StaleExecution) else 'CANCELLED' if type(error).__name__=='JobCancelled' else 'FAILED'
            descriptor_status='STALE' if status=='SUPERSEDED' else status
            message=str(error)[:1800]
            with self.cv:
                if self.error is None:self.error=error
                self.cv.notify_all()
            raise
        finally:
            actual=self.owner.coordinator.call(self.owner.journal.finish,context.identity,status,message)
            with self.owner.root.cv:self.owner.root.active_executions.pop(context.identity,None)
            if status=='COMPLETE' and actual=='COMPLETE':
                with self.cv:self.confirmed.add(context.identity)
            if status=='COMPLETE' and actual=='COMPLETE' and task['chapter'] is not None:
                try:
                    owner=self.owner
                    owner.schedule_render(task['chapter'],owner.chapters.index(task['chapter']),owner.chapters)
                except BaseException as error:
                    # The QC receipt is complete; a local render-admission error
                    # remains a separate fail-closed pipeline outcome.
                    self.queue.finish(identity,'FAILED')
                    with self.cv:
                        if self.error is None:self.error=error
                        self.cv.notify_all()
                    raise
            unresolved=actual=='UNKNOWN' or status=='COMPLETE' and actual!='COMPLETE'
            self.queue.finish(identity,'UNKNOWN' if unresolved else descriptor_status)
            if unresolved and status=='COMPLETE':
                error=UnresolvedExecution('Dense QC receipt remains UNKNOWN; no new work admitted.')
                with self.cv:
                    if self.error is None:self.error=error
                raise error

    def _run(self):
        try:
            while True:
                with self.cv:
                    if self.closed:return
                self.check()
                for future in [f for f in self.active if f.done()]:
                    self.active.pop(future);future.result()
                if len(self.active)<self.workers:
                    pending=self.queue.pending()
                    if pending:
                        identity,task=pending
                        self.owner.gate(self.owner.main_context,'Admitting saved dense image QC')
                        context=self.owner.admit({'revision':task['revision']},task['chapter'],task['shot'],
                            'deferred-qc',task['spec'],[task['parent']])
                        if not self.queue.claim(identity,context.identity):
                            self.owner.coordinator.call(self.owner.journal.finish,context.identity,'CANCELLED','Descriptor already claimed')
                            with self.owner.root.cv:self.owner.root.active_executions.pop(context.identity,None)
                            continue
                        self.active[self.pool.submit(self._review,identity,task,context)]=identity
                        continue
                with self.cv:self.cv.wait(.05)
        except BaseException as error:
            with self.cv:
                if self.error is None:self.error=error
                self.cv.notify_all()

    def drain(self):
        while True:
            self.check();state=self.queue.snapshot()['counts']
            if any(state.get(name) for name in ('UNKNOWN','STALE','FAILED','CANCELLED')):
                raise RuntimeError('Dense QC has unresolved or superseded work; no rendering or replacement admitted.')
            if not state.get('PENDING') and not state.get('CLAIMED'):break
            self.owner.gate(self.owner.main_context,'Waiting for saved image quality checks')
            with self.cv:self.cv.wait(.05)
        self.check()

    def stop_admission(self):
        with self.cv:self.closed=True;self.cv.notify_all()
        self.thread.join()

    def close(self):
        self.stop_admission()
        self.pool.shutdown(wait=True,cancel_futures=False)
        # Collect every worker exception even if a different sibling failed first.
        for future in self.active:
            try:future.result()
            except BaseException:pass
        self.queue.close()
