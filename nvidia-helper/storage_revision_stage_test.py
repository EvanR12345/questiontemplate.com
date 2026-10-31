"""Verify metadata reuse after local edits without publishing stale snapshots."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from storage_parallel_stage_test import Objects
from studio_data import ProjectStore, new_project
from studio_storage import R2Archive, sha
from google_storage import GCSObjects
from google_storage_test import Client


class RevisionStageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.store=ProjectStore(self.tmp.name)
        self.project=self.store.save(new_project('Original'))
        self.pid=self.project['id'];self.folder=self.store.folder(self.pid)
        for index in range(4):(self.folder/f'image-{index}.png').write_bytes(f'asset-{index}'.encode())
        self.objects=Objects()
        self.archive=R2Archive(self.store,Path(self.tmp.name)/'absent',objects=self.objects,start_worker=False)
    def tearDown(self):self.archive.close();self.tmp.cleanup()
    def supersede_during_upload(self):
        once=threading.Lock();changed=threading.Event()
        def edit(*_):
            with once:
                if not changed.is_set():
                    self.store.mutate(self.pid,lambda p:p.update(name='Current edit'))
                    changed.set()
        self.objects.before_upload=edit
        self.assertFalse(self.archive.sync(self.pid))
        self.objects.before_upload=None
        self.assertEqual(self.objects.manifest_writes,0)
        self.assertEqual(len(self.objects.started),4)
    def manifest(self):
        raw,_=self.objects.read('studio/manifests/'+self.pid+'.json')
        return json.loads(raw)
    def test_current_revision_reuses_verified_assets_without_rehash_or_upload(self):
        self.supersede_during_upload()
        with patch('studio_storage.sha',wraps=sha) as hashing:
            self.assertTrue(self.archive.sync(self.pid));hashing.assert_not_called()
        self.assertEqual(len(self.objects.started),4)
        self.assertEqual(self.manifest()['project']['name'],'Current edit')
        self.assertNotIn(self.pid,self.archive.staged_assets)
    def test_changed_file_is_rehashed_and_uploaded_before_current_publication(self):
        self.supersede_during_upload()
        (self.folder/'image-0.png').write_bytes(b'new current pixels')
        with patch('studio_storage.sha',wraps=sha) as hashing:
            self.assertTrue(self.archive.sync(self.pid));self.assertEqual(hashing.call_count,1)
        self.assertEqual(len(self.objects.started),5)
        record=self.manifest()['files']['image-0.png']
        self.assertEqual(record['sha256'],sha(self.folder/'image-0.png'))
    def test_deleted_unpublished_asset_is_not_added_to_current_manifest(self):
        self.supersede_during_upload();(self.folder/'image-0.png').unlink()
        self.assertTrue(self.archive.sync(self.pid))
        self.assertNotIn('image-0.png',self.manifest()['files'])
    def test_failed_transfer_does_not_cache_or_publish_partial_stage(self):
        self.objects.fail_name='image-0.png'
        with self.assertRaisesRegex(RuntimeError,'transfer failure'):self.archive.sync(self.pid)
        self.assertNotIn(self.pid,self.archive.staged_assets)
        self.assertEqual(self.objects.manifest_writes,0)
    def test_conflicting_remote_manifest_retains_local_edits_and_verified_stage(self):
        self.supersede_during_upload();self.objects.fail_manifest=True
        with self.assertRaisesRegex(ValueError,'Concurrent update'):self.archive.sync(self.pid)
        self.assertEqual(self.store.load_local(self.pid)['name'],'Current edit')
        self.assertIn(self.pid,self.archive.staged_assets)
        self.assertEqual(self.objects.manifest_writes,0)


class PublicationReceiptTests(unittest.TestCase):
    def test_json_receipt_uses_upload_generation_without_a_later_read(self):
        client=Client();objects=GCSObjects({'bucket':'private-test'},client=client)
        with patch.object(client,'get_blob',side_effect=AssertionError('Receipt must come from upload response')):
            receipt=objects.put_json('owned-manifest',{'value':'original'},create=True)
        self.assertEqual(receipt['generation'],'1')
        self.assertEqual(receipt['bytes'],len(b'{"value":"original"}'))
        next_receipt=objects.put_json('owned-manifest',{'value':'next'},etag=receipt['generation'])
        self.assertEqual(next_receipt['generation'],'2')
        with self.assertRaisesRegex(ValueError,'Concurrent update'):
            objects.put_json('owned-manifest',{'value':'stale'},etag=receipt['generation'])


if __name__=='__main__':unittest.main()
