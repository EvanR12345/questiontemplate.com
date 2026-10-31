"""Integrity/concurrency tests; no cloud clients or network operations."""
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from google_storage import GCSObjects
from google_storage_test import Blob, Client
from studio_data import ProjectStore, new_project
from studio_storage import R2Archive, sha, stage_immutable_assets


class Objects:
    provider='Google Cloud Storage'
    upload_workers=2
    def __init__(self):
        self.lock=threading.RLock();self.data={};self.serial=0
        self.active=0;self.maximum=0;self.started=[];self.finished=[]
        self.entered=threading.Event();self.release=None;self.fail_name=None
        self.before_upload=None;self.fail_manifest=False;self.manifest_writes=0
    def read(self,key):
        with self.lock:return self.data.get(key,(None,None))
    def put_json(self,key,value,etag=None,create=False):
        with self.lock:
            if key.startswith('studio/manifests/') and self.fail_manifest:raise ValueError('Concurrent update')
            previous=self.data.get(key,(None,None))
            if create and key in self.data or etag is not None and previous[1]!=etag:raise ValueError('Concurrent update')
            self.serial+=1;self.data[key]=(json.dumps(value).encode(),str(self.serial))
            if key.startswith('studio/manifests/'):self.manifest_writes+=1
    def upload(self,key,path,checksum):
        with self.lock:
            self.active+=1;self.maximum=max(self.maximum,self.active)
            self.started.append(path.name);self.entered.set()
        try:
            if self.before_upload:self.before_upload(key,path,checksum)
            if path.name==self.fail_name:raise RuntimeError('Synthetic transfer failure')
            if self.release and not self.release.wait(4):raise TimeoutError('Test release missing')
            time.sleep(.01)
            body=path.read_bytes()
            if hashlib.sha256(body).hexdigest()!=checksum:raise ValueError('Synthetic immutable mismatch')
            with self.lock:self.data[key]=(body,'immutable')
        finally:
            with self.lock:self.active-=1;self.finished.append(path.name)
    def keys(self,prefix):return [key for key in self.data if key.startswith(prefix)]


class ParallelStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.paths=[]
        for index in range(4):
            path=self.root/('image-'+str(index)+'.png');path.write_bytes(('asset-'+str(index)).encode());self.paths.append(path)
    def tearDown(self):self.tmp.cleanup()
    def stage(self,objects,workers=2,records=None,cancelled=lambda:False,progress=lambda *_:None):
        return stage_immutable_assets(objects,'project',self.root,self.paths,records or {},lambda _:None,progress,workers,cancelled)
    def thread_run(self,call):
        outcome=[]
        def run():
            try:outcome.append(('return',call()))
            except BaseException as error:outcome.append(('error',error))
        thread=threading.Thread(target=run);thread.start();return thread,outcome
    def test_serial_parallel_records_bytes_and_owner_progress_match(self):
        baseline=Objects();expected,expected_bytes=self.stage(baseline,1)
        for workers in (2,4):
            objects=Objects();progress=[];owner=threading.get_ident()
            records,size=self.stage(objects,workers,progress=lambda *v:progress.append((threading.get_ident(),v)))
            self.assertEqual(records,expected);self.assertEqual(size,expected_bytes)
            self.assertEqual(objects.data,baseline.data);self.assertEqual(list(records),sorted(records))
            self.assertLessEqual(objects.maximum,workers);self.assertGreater(objects.maximum,1)
            self.assertTrue(all(tid==owner for tid,_ in progress));self.assertEqual(progress[-1][1],(4,4,size))
    def test_invalid_workers_fail_before_upload_and_unchanged_records_skip_upload(self):
        objects=Objects()
        for value in (True,False,0,5,1.0,'2',None):
            with self.assertRaises(ValueError):self.stage(objects,value)
        self.assertEqual(objects.started,[])
        records,_=self.stage(objects,2);count=len(objects.started)
        again,size=self.stage(objects,2,records)
        self.assertEqual(again,records);self.assertEqual(size,0);self.assertEqual(len(objects.started),count)
    def test_failure_drains_active_transfers_and_never_admits_later_batch(self):
        objects=Objects();objects.release=threading.Event();objects.fail_name='image-1.png'
        first_active=threading.Event();failure_ready=threading.Event()
        def before(_key,path,_hash):
            if path.name=='image-0.png':first_active.set()
            if path.name=='image-1.png':
                self.assertTrue(first_active.wait(2));failure_ready.set()
        objects.before_upload=before
        thread,outcome=self.thread_run(lambda:self.stage(objects,2))
        try:
            self.assertTrue(failure_ready.wait(2));time.sleep(.03)
            self.assertTrue(thread.is_alive(),'Failure must wait for admitted transfer')
            self.assertEqual(set(objects.started),{'image-0.png','image-1.png'})
        finally:objects.release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertIsInstance(outcome[0][1],RuntimeError)
        self.assertEqual(objects.active,0);self.assertEqual(len(objects.finished),2)
    def test_local_file_edit_during_upload_rejects_stage_without_mutating_input_records(self):
        objects=Objects();original={};before=copy.deepcopy(original)
        def mutate(_key,path,_hash):
            if path.name=='image-0.png':path.write_bytes(b'edited-after-hash')
        objects.before_upload=mutate
        with self.assertRaises(ValueError):self.stage(objects,2,original)
        self.assertEqual(original,before);self.assertEqual(objects.active,0)
    def test_cancel_during_active_batch_drains_and_skips_unsent_work(self):
        objects=Objects();objects.release=threading.Event();cancel=threading.Event()
        both_active=threading.Barrier(3)
        objects.before_upload=lambda *_:both_active.wait(timeout=3)
        thread,outcome=self.thread_run(lambda:self.stage(objects,2,cancelled=cancel.is_set))
        try:
            both_active.wait(timeout=3);cancel.set();objects.release.set()
        finally:objects.release.set();thread.join(3)
        self.assertFalse(thread.is_alive());self.assertIsInstance(outcome[0][1],RuntimeError)
        self.assertEqual(len(objects.started),2);self.assertEqual(objects.active,0)
    def test_archive_parallel_stage_has_no_early_manifest_or_network_project_lock(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);project=store.save(new_project('Safe stage'))
            for path in self.paths:(store.folder(project['id'])/path.name).write_bytes(path.read_bytes())
            objects=Objects();objects.release=threading.Event()
            archive=R2Archive(store,Path(folder)/'absent',objects=objects,start_worker=False)
            thread,outcome=self.thread_run(lambda:archive.sync(project['id']))
            try:
                self.assertTrue(objects.entered.wait(2));self.assertEqual(objects.manifest_writes,0)
                self.assertTrue(store.lock.acquire(timeout=.2),'Upload must not hold project lock');store.lock.release()
            finally:objects.release.set();thread.join(3)
            self.assertEqual(outcome,[('return',True)]);self.assertEqual(objects.manifest_writes,1)
            manifest,_=archive.manifest(project['id'],True)
            self.assertEqual(list(manifest['files']),[p.name for p in self.paths]);self.assertEqual(objects.active,0)
            archive.close()
    def test_concurrent_project_edit_prevents_stale_manifest_and_preserves_new_revision(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);project=store.save(new_project('Before'))
            (store.folder(project['id'])/'image.png').write_bytes(b'image')
            objects=Objects();objects.release=threading.Event()
            archive=R2Archive(store,Path(folder)/'absent',objects=objects,start_worker=False)
            thread,outcome=self.thread_run(lambda:archive.sync(project['id']))
            try:
                self.assertTrue(objects.entered.wait(2));store.mutate(project['id'],lambda p:p.update(name='New revision'))
            finally:objects.release.set();thread.join(3)
            self.assertEqual(outcome,[('return',False)]);self.assertEqual(objects.manifest_writes,0)
            self.assertEqual(store.load_local(project['id'])['name'],'New revision')
            self.assertIn(project['id'],archive.pending);archive.close()
    def test_remote_manifest_conflict_keeps_previous_snapshot_and_r2_stays_serial(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);project=store.save(new_project('Before'))
            for path in self.paths:(store.folder(project['id'])/path.name).write_bytes(path.read_bytes())
            objects=Objects();objects.provider='Cloudflare R2';objects.upload_workers=4
            archive=R2Archive(store,Path(folder)/'absent',objects=objects,start_worker=False)
            self.assertTrue(archive.sync(project['id']));self.assertEqual(objects.maximum,1)
            key='studio/manifests/'+project['id']+'.json';previous=objects.data[key]
            store.mutate(project['id'],lambda p:p.update(name='Local edit'));objects.fail_manifest=True
            with self.assertRaises(ValueError):archive.sync(project['id'])
            self.assertEqual(objects.data[key],previous);self.assertEqual(store.load_local(project['id'])['name'],'Local edit')
            archive.close()
    def test_owner_close_during_parallel_upload_prevents_publication(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);project=store.save(new_project('Before'))
            for path in self.paths:(store.folder(project['id'])/path.name).write_bytes(path.read_bytes())
            objects=Objects();objects.release=threading.Event()
            archive=R2Archive(store,Path(folder)/'absent',objects=objects,start_worker=True)
            self.assertTrue(objects.entered.wait(2))
            closer=threading.Thread(target=archive.close);closer.start()
            try:
                until=time.monotonic()+2
                while not archive.stopped and time.monotonic()<until:time.sleep(.005)
                self.assertTrue(archive.stopped)
            finally:objects.release.set();closer.join(3)
            self.assertFalse(closer.is_alive());self.assertFalse(archive.thread.is_alive())
            self.assertEqual(objects.manifest_writes,0);self.assertLessEqual(len(objects.started),2);self.assertEqual(objects.active,0)


class GoogleWorkerConfigurationTests(unittest.TestCase):
    def test_upload_workers_are_explicit_bounded_integers_with_serial_default(self):
        self.assertEqual(GCSObjects({'bucket':'private-test'},client=Client()).upload_workers,1)
        for workers in (1,2,3,4):
            self.assertEqual(GCSObjects({'bucket':'private-test','uploadWorkers':workers},client=Client()).upload_workers,workers)
        for invalid in (True,False,0,5,1.0,'2',None):
            with self.assertRaises(ValueError):GCSObjects({'bucket':'private-test','uploadWorkers':invalid},client=Client())
    def test_upload_returns_verified_generation_and_reuse_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'image.png';path.write_bytes(b'image');objects=GCSObjects({'bucket':'private-test'},client=Client())
            first=objects.upload('object',path,sha(path));second=objects.upload('object',path,sha(path))
            self.assertFalse(first['reused']);self.assertTrue(second['reused'])
            self.assertEqual(first['generation'],second['generation']);self.assertEqual(first['bytes'],5)
    def test_native_upload_receipt_remains_original_when_later_generation_changes(self):
        class RaceBlob(Blob):
            def upload_from_file(self,stream,**kwargs):
                super().upload_from_file(stream,**kwargs)
                self.bucket.data[self.name]['generation']+=1
            def reload(self,**kwargs):
                expected=kwargs.get('if_generation_match')
                if expected is not None:raise AssertionError('No later metadata GET may replace native response')
                super().reload(**kwargs)
        client=Client();client.blob=lambda key,**kwargs:RaceBlob(client,key,**kwargs)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'image.png';path.write_bytes(b'image')
            receipt=GCSObjects({'bucket':'private-test'},client=client).upload('object',path,sha(path))
            self.assertEqual(receipt['generation'],'1')
            self.assertEqual(client.data['object']['generation'],2)
            self.assertFalse(receipt['reused'])



if __name__=='__main__':unittest.main()
