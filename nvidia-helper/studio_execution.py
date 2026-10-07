"""Bounded execution, durable remote uncertainty, and one project/trace writer."""
import contextvars
import copy
import json
import os
import queue
import sqlite3
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
from dataclasses import dataclass


class UnresolvedExecution(RuntimeError):
    pass


class CommitCoordinator:
    """Workers submit short commits; remote work never occupies this thread."""
    def __init__(self):
        self.pending = queue.Queue()
        self.state_lock=threading.Lock()
        self.closed=False
        self.thread = threading.Thread(target=self._run, daemon=True, name='studio-commits')
        self.thread.start()

    def _run(self):
        while True:
            item = self.pending.get()
            if item is None:
                return
            future, context, callback, args, kwargs = item
            try:
                future.set_result(context.run(callback, *args, **kwargs))
            except BaseException as error:
                future.set_exception(error)

    def call(self, callback, *args, **kwargs):
        if threading.current_thread() is self.thread:
            return callback(*args, **kwargs)
        future = Future()
        with self.state_lock:
            if self.closed:raise RuntimeError('Commit coordinator is closed.')
            self.pending.put((future, contextvars.copy_context(), callback, args, kwargs))
        return future.result()

    def close(self):
        with self.state_lock:
            if self.closed:return
            self.closed=True
            self.pending.put(None)
        self.thread.join()


class CoordinatedStore:
    def __init__(self, store, coordinator):
        self.original, self.coordinator = store, coordinator

    def __getattr__(self, name):
        value = getattr(self.original, name)
        if name in ('load', 'save', 'mutate', 'revision', 'list'):
            return lambda *args, **kwargs: self.coordinator.call(value, *args, **kwargs)
        return value


class CoordinatedTrace:
    def __init__(self, trace, coordinator):
        self.original, self.coordinator = trace, coordinator

    def __getattr__(self, name):
        value = getattr(self.original, name)
        return lambda *args, **kwargs: self.coordinator.call(value, *args, **kwargs)

    @contextmanager
    def span(self, project, stage, details=None, kind='stage'):
        identity = self.begin(project, stage, details, kind)
        token = self.original.parent.set(identity)
        began, status = time.monotonic(), 'COMPLETE'
        try:
            yield identity
        except BaseException as error:
            status = 'CANCELLED' if type(error).__name__ == 'JobCancelled' else 'FAILED'
            raise
        finally:
            self.finish(identity, time.monotonic()-began, status)
            self.original.parent.reset(token)


@dataclass(frozen=True)
class ExecutionContext:
    identity: str
    parent_job: str
    project: str
    chapter: str
    shot: str
    kind: str
    revision: int
    input_hash: str
    settings_json: str
    dependencies: tuple
    cancelled: threading.Event

    @property
    def settings(self):
        return json.loads(self.settings_json)


class ExecutionJournal:
    """A submitted operation is never automatically replayed after restart."""
    def __init__(self, path):
        self.lock = threading.RLock()
        self.owner=open(str(path)+'.owner','a+b')
        if self.owner.seek(0,os.SEEK_END)==0:
            self.owner.write(b'\0');self.owner.flush()
        self.owner.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.owner.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.owner.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.owner.close()
            raise RuntimeError('Another helper owns this execution journal; stop it before starting a second worker.') from None
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.execute('CREATE TABLE IF NOT EXISTS executions '
            '(id TEXT PRIMARY KEY,parent TEXT,project TEXT,chapter TEXT,shot TEXT,kind TEXT,'
            'revision INTEGER,input_hash TEXT,settings TEXT,dependencies TEXT,status TEXT,'
            'operations TEXT,message TEXT,created REAL,finished REAL)')
        self.db.execute('CREATE INDEX IF NOT EXISTS execution_project_status ON executions(project,status)')
        self.db.execute('CREATE INDEX IF NOT EXISTS execution_status_created ON executions(status,created)')
        for row in self.db.execute("SELECT id,operations FROM executions WHERE status='RUNNING'").fetchall():
            # Known billing alone does not prove output publication. A crash
            # after receipt but before cache/project commit must not buy it again.
            pending = bool(json.loads(row['operations']))
            self.db.execute('UPDATE executions SET status=?,message=? WHERE id=?',
                ('UNKNOWN' if pending else 'INTERRUPTED', 'Helper restarted; saved remote operations retained', row['id']))
        self.db.commit()

    def admit(self, parent, project, chapter, shot, kind, revision, input_hash, settings, dependencies=()):
        with self.lock:
            unresolved = self.db.execute("SELECT id FROM executions WHERE project=? AND chapter IS ? "
                "AND shot IS ? AND kind=? AND status='UNKNOWN'", (project,chapter,shot,kind)).fetchone()
            if unresolved:
                raise UnresolvedExecution('Execution '+unresolved['id']+' has unresolved remote work; reconcile before retrying.')
            identity = 'exec-'+uuid.uuid4().hex
            encoded = json.dumps(settings, sort_keys=True)
            context = ExecutionContext(identity,parent,project,chapter,shot,kind,revision,input_hash,
                encoded,tuple(dependencies),threading.Event())
            self.db.execute('INSERT INTO executions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity,parent,project,chapter,shot,kind,revision,input_hash,encoded,
                 json.dumps(list(dependencies)),'RUNNING','{}','Preparing',time.time(),None))
            self.db.commit()
            return context

    def observe(self, identity, event, details):
        with self.lock:
            row = self.db.execute('SELECT operations,status FROM executions WHERE id=?',(identity,)).fetchone()
            if not row or row['status']!='RUNNING':
                raise RuntimeError('Execution no longer owns remote admission.')
            operations = json.loads(row['operations'])
            key = str(details['operationId'])
            if event=='submitting':
                if key in operations:
                    raise RuntimeError('Duplicate remote admission.')
                operations[key]={'state':'SUBMITTED','provider':details.get('provider')}
            elif key not in operations:
                raise RuntimeError('Remote result has no submitted execution owner.')
            elif event=='accepted':
                if operations[key].get('remoteId') not in (None,details['remoteId']):
                    raise RuntimeError('Conflicting remote identity; original submission retained.')
                operations[key]['remoteId']=details['remoteId']
            elif event in ('completed','rejected','failed'):
                operations[key]['state']=event.upper()
            else:
                raise ValueError('Unknown execution event.')
            self.db.execute('UPDATE executions SET operations=? WHERE id=?',(json.dumps(operations),identity))
            self.db.commit()

    def progress(self, identity, message):
        with self.lock:
            self.db.execute('UPDATE executions SET message=? WHERE id=?',(message,identity))
            self.db.commit()

    def finish(self, identity, status, message=''):
        with self.lock:
            row=self.db.execute('SELECT operations,status FROM executions WHERE id=?',(identity,)).fetchone()
            if any(op['state']=='SUBMITTED' for op in json.loads(row['operations']).values()):
                status='UNKNOWN'
            if row['status']!='RUNNING':
                if row['status']==status:return status
                raise RuntimeError('Execution already terminal; original outcome retained.')
            self.db.execute('UPDATE executions SET status=?,message=?,finished=? WHERE id=?',
                (status,message,time.time(),identity))
            self.db.commit()
            return status

    def unresolved(self, parent):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT id,kind,chapter,shot FROM executions "
                "WHERE parent=? AND status='UNKNOWN'",(parent,))]

    def snapshot(self, parent):
        with self.lock:
            rows=[dict(r) for r in self.db.execute('SELECT id,parent,project,chapter,shot,kind,revision,'
                'input_hash,status,operations,message,created,finished FROM executions WHERE parent=? ORDER BY created',(parent,))]
        for row in rows:row['operations']=json.loads(row['operations'])
        return rows

    def recent(self, limit=30):
        with self.lock:
            rows=[dict(row) for row in self.db.execute("SELECT id,parent,project,chapter,shot,kind,status,message "
                "FROM executions WHERE status IN ('RUNNING','UNKNOWN') OR id IN "
                "(SELECT id FROM executions ORDER BY created DESC LIMIT ?) ORDER BY created DESC",(limit,))]
            return rows

    def close(self):
        self.db.close()
        self.owner.close()

    def unresolved_project(self, project):
        with self.lock:
            return [dict(row) for row in self.db.execute("SELECT id,kind,chapter,shot FROM executions "
                "WHERE project=? AND status='UNKNOWN'",(project,))]


class ImageLane:
    """One image request including retrieval; repairs precede waiting fresh work."""
    def __init__(self, capacity=1, name='image'):
        self.cv=threading.Condition()
        self.waiters=[]
        self.active=0
        self.capacity,self.name=capacity,name
        self.sequence=0

    @contextmanager
    def acquire(self, gate, repair=False):
        with self.cv:
            self.sequence+=1
            ticket=(0 if repair else 1,self.sequence)
            self.waiters.append(ticket)
        acquired=False
        try:
            while True:
                gate('Waiting for '+self.name+' lane')
                with self.cv:
                    if self.active<self.capacity and ticket==min(self.waiters):
                        self.waiters.remove(ticket)
                        self.active+=1
                        acquired=True
                        break
                    self.cv.wait(.05)
            yield
        finally:
            with self.cv:
                if acquired:self.active-=1
                elif ticket in self.waiters:self.waiters.remove(ticket)
                self.cv.notify_all()


class LaneProvider:
    def __init__(self, provider, lane, gate, observer=None, validator=None, retry_priority=True):
        self.original,self.lane,self.gate,self.observer=provider,lane,gate,observer
        self.validator=validator
        self.retry_priority=retry_priority
        self.calls=0

    def __getattr__(self,name):return getattr(self.original,name)

    def generateImage(self, request, checkpoint):
        repair=(self.retry_priority and self.calls>0) or request.get('operation') in ('edit','inpaint')
        self.calls+=1
        with self.lane.acquire(self.gate,repair):
            if self.validator:self.validator()
            request=dict(request)
            if self.observer:request['_executionObserver']=self.observer
            return self.original.generateImage(request,checkpoint)


class BoundedExecutions:
    def __init__(self, limit, gate):
        if isinstance(limit,bool) or not isinstance(limit,int) or not 2<=limit<=3:
            raise ValueError('Choose two or three in-flight cloud shots.')
        self.pool=ThreadPoolExecutor(max_workers=limit,thread_name_prefix='studio-shot')
        self.limit,self.gate=limit,gate
        self.pending=set()
        self.all=[]

    def drain_one(self):
        while True:
            self.gate('Waiting for cloud shot results')
            done,_=wait(self.pending,timeout=.1,return_when=FIRST_COMPLETED)
            if done:
                self.pending-=done
                for future in done:future.result()
                return

    def ready(self):
        while len(self.pending)>=self.limit:self.drain_one()
        # Surface already completed failures before buying more work.
        done={f for f in self.pending if f.done()}
        self.pending-=done
        for future in done:future.result()
        self.gate('Preparing cloud shot')

    def submit(self, callback, *args):
        self.ready()
        future=self.pool.submit(contextvars.copy_context().run,callback,*args)
        self.pending.add(future);self.all.append(future)
        return future

    def drain(self):
        while self.pending:self.drain_one()

    def wait_for(self, futures):
        waiting=set(futures)
        while waiting:
            self.gate('Waiting for earlier chapter results')
            done,waiting=wait(waiting,timeout=.1,return_when=FIRST_COMPLETED)
            self.pending-=done
            for future in done:future.result()

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)
