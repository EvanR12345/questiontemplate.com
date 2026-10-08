import copy
import hashlib
import io
import json
import unittest
import threading
from cloud_migration import migrate_to_google
from google_storage import GCSObjects
from google_storage_test import Client
from studio_data import new_project

class Source:
    def __init__(self):self.data={};self.mutate_manifest=False;self.manifest_reads=0
    def keys(self,prefix):return [key for key in self.data if key.startswith(prefix)]
    def read(self,key):
        if self.mutate_manifest and key.startswith('studio/manifests/'):
            self.manifest_reads+=1
            if self.manifest_reads>1:return b'{"changed":true}','new-etag'
        body=self.data.get(key);return (body,hashlib.sha256(body).hexdigest()) if body is not None else (None,None)
    def open_object(self,key):return {'body':io.BytesIO(self.data[key]),'bytes':len(self.data[key])}

class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.source=Source();self.client=Client();self.target=GCSObjects({'bucket':'private-test'},client=self.client)
        self.project=new_project('Saved story');self.pid=self.project['id'];self.body=b'visible saved art'
        self.sha=hashlib.sha256(self.body).hexdigest();self.asset=f'studio/assets/{self.pid}/{self.sha}/art.png'
        self.manifest=f'studio/manifests/{self.pid}.json'
        self.source.data[self.asset]=self.body
        self.value={'version':1,'project':self.project,'files':{'art.png':{'key':self.asset,'sha256':self.sha,'bytes':len(self.body)}},'savedAt':1}
        self.source.data[self.manifest]=json.dumps(self.value).encode()
        self.source.data[f'studio/history/{self.pid}/old.json']=b'{"saved":"history"}'
        self.source.data['publisher/jobs/saved.json']=b'{"status":"UPLOADING","uploaded":123}'
    def test_all_assets_history_project_and_upload_state_copy_without_local_files_or_source_changes(self):
        before=copy.deepcopy(self.source.data);events=[]
        result=migrate_to_google(self.source,self.target,events.append)
        self.assertEqual(result['projects'],1);self.assertEqual(result['localFilesCreated'],0);self.assertEqual(result['sourceObjectsDeleted'],0)
        self.assertEqual(self.source.data,before)
        for key,body in before.items():self.assertEqual(self.target.read(key)[0],body)
        self.assertTrue(all(set(event)<=set(result) for event in events))
    def test_resume_verifies_existing_objects_and_is_idempotent(self):
        migrate_to_google(self.source,self.target);generation=self.client.data[self.asset]['generation']
        migrate_to_google(self.source,self.target);self.assertEqual(self.client.data[self.asset]['generation'],generation)
    def test_bounded_parallel_assets_finish_before_publishing_manifest(self):
        second_body=b'second saved reference';second_sha=hashlib.sha256(second_body).hexdigest()
        second=f'studio/assets/{self.pid}/{second_sha}/reference.png';self.source.data[second]=second_body
        self.value['files']['reference.png']={'key':second,'sha256':second_sha,'bytes':len(second_body)}
        self.source.data[self.manifest]=json.dumps(self.value).encode()
        result=migrate_to_google(self.source,self.target,workers=2)
        self.assertEqual(result['assets'],2);self.assertEqual(result['projects'],1)
        self.assertEqual(self.target.read(second)[0],second_body)
        self.assertEqual(self.target.read(self.manifest)[0],self.source.data[self.manifest])
        for invalid in (0,4,True):
            with self.assertRaisesRegex(ValueError,'bounded'):migrate_to_google(self.source,self.target,workers=invalid)
    def test_wrong_asset_stops_before_publishing_any_manifest(self):
        self.source.data[self.asset]=b'wrong source data'
        with self.assertRaisesRegex(ValueError,'source checksum mismatch'):migrate_to_google(self.source,self.target)
        self.assertNotIn(self.manifest,self.client.data);self.assertIn(self.asset,self.source.data)

    def test_failed_transfer_does_not_drain_remaining_archive_queue(self):
        for n in range(20):
            self.source.data[f'studio/assets/{self.pid}/{self.sha}/extra-{n}.png']=self.body
        calls=[]
        def fail(key):
            calls.append(key)
            raise OSError('Transfer failed')
        self.source.open_object=fail
        with self.assertRaises(OSError):migrate_to_google(self.source,self.target,workers=1)
        self.assertEqual(len(calls),1)
        self.assertNotIn(self.manifest,self.client.data)

    def test_completed_parallel_asset_reports_progress_while_earlier_asset_is_slow(self):
        second=f'studio/assets/{self.pid}/{self.sha}/second.png'
        self.source.data[second]=self.body
        first=sorted((self.asset,second))[0];release=threading.Event();events=[]
        original=self.source.open_object
        def delay(key):
            if key==first and not release.wait(3):raise TimeoutError('Progress was blocked behind a slow asset')
            return original(key)
        def progress(value):
            events.append(value)
            if value['assets']==1:release.set()
        self.source.open_object=delay
        try:result=migrate_to_google(self.source,self.target,progress,workers=2)
        finally:release.set()
        self.assertEqual(result['assets'],2)
        self.assertEqual(events[0]['assets'],1)
        self.assertEqual(result['projects'],1)
    def test_target_project_conflict_preserves_both_archives(self):
        self.target.put_json(self.manifest,{'newer':True})
        original=self.target.read(self.manifest)[0]
        with self.assertRaisesRegex(ValueError,'conflicts'):migrate_to_google(self.source,self.target)
        self.assertEqual(self.target.read(self.manifest)[0],original)
        self.assertIn(self.manifest,self.source.data)
    def test_source_edit_during_migration_cannot_publish_stale_manifest(self):
        self.source.mutate_manifest=True
        with self.assertRaisesRegex(ValueError,'Source project changed'):migrate_to_google(self.source,self.target)
        self.assertNotIn(self.manifest,self.client.data)
    def test_unsafe_manifest_path_and_missing_asset_block_publication(self):
        self.value['files']={'../private':self.value['files']['art.png']}
        self.source.data[self.manifest]=json.dumps(self.value).encode()
        with self.assertRaisesRegex(ValueError,'Unsafe'):migrate_to_google(self.source,self.target)
        self.assertNotIn(self.manifest,self.client.data)

if __name__=='__main__':unittest.main()
