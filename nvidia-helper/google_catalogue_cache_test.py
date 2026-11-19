import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google_storage import GCSObjects
from google_storage_test import Blob, Client
from studio_data import ProjectStore
from studio_storage import R2Archive


class ConditionalFailure(Exception):
    def __init__(self, code):
        self.code = code


class CountingBlob(Blob):
    def download_as_bytes(self, if_generation_match=None, **kwargs):
        client = self.bucket
        with client.lock:
            client.counts['bodyGETs'] += 1
            row = client.data.get(self.name)
            if row is None:
                raise ConditionalFailure(404)
            if row['generation'] != if_generation_match:
                raise ConditionalFailure(412)
            if self.name == client.unreadable:
                raise OSError('private diagnostic')
            client.counts['bodyBytes'] += len(row['body'])
        return super().download_as_bytes(if_generation_match=if_generation_match, **kwargs)


class CountingClient(Client):
    def __init__(self):
        super().__init__()
        self.lock = threading.Lock()
        self.after_list = None
        self.unreadable = None
        self.reset()

    def reset(self):
        self.counts = dict(listPages=0, metadataGETs=0, bodyGETs=0, bodyBytes=0)

    def blob(self, key, **kwargs):
        return CountingBlob(self, key, **kwargs)

    def get_blob(self, key, **kwargs):
        self.counts['metadataGETs'] += 1
        return super().get_blob(key, **kwargs)

    def list_blobs(self, bucket, prefix, **kwargs):
        self.counts['listPages'] += 1
        assert kwargs.get('fields') == 'nextPageToken,items(name,generation)'
        items = [SimpleNamespace(name=k,generation=v['generation'])
                 for k,v in self.data.items() if k.startswith(prefix)]
        if self.after_list:self.after_list()
        return items


class CatalogueCacheTests(unittest.TestCase):
    observations = {}

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = ProjectStore(directory.name)
        self.client = CountingClient()
        self.objects = GCSObjects({'bucket':'private-test'}, client=self.client)
        self.archive = R2Archive(self.store, Path(directory.name)/'absent', self.objects, start_worker=False)
        self.addCleanup(self.archive.close)
        for i in range(28):self.save(i)

    @staticmethod
    def key(i):
        return f'studio/manifests/pr-{i:016x}.json'

    def save(self, i, revision=1):
        key = self.key(i)
        p = {'id':f'pr-{i:016x}', 'name':f'Project {i} revision {revision}', 'revision':revision,
             'updated':revision, 'chapters':[], 'fixtureText':'x'*(8192+i)}
        self.objects.put_json(key, {'project':p})

    def expire(self):
        # Exercise elapsed TTL independently of explicit publication invalidation.
        with self.archive.catalogue_lock:self.archive.catalogue_at -= 31

    def measure(self, name, expected, force_expiry=True):
        if force_expiry:self.expire()
        self.client.reset()
        rows = self.archive.list_projects()
        observed = self.client.counts.copy()
        self.observations[name] = observed
        self.assertEqual(tuple(observed.values()), expected, name)
        return {p['id']:p for p in rows}

    def test_sdk_counts_across_cold_warm_changed_added_and_deleted_manifests(self):
        size = lambda key:len(self.client.data[key]['body'])
        total = sum(size(k) for k in self.client.data)
        cold = self.measure('cold', (1,0,28,total), False)
        self.assertEqual(len(cold),28)
        self.assertEqual(self.measure('withinTTL',(0,0,0,0), False),cold)
        self.assertEqual(self.measure('expiredUnchanged',(1,0,0,0)),cold)
        self.save(7, 2)
        changed = self.measure('oneChanged',(1,0,1,size(self.key(7))))
        self.assertEqual(changed['pr-0000000000000007']['revision'],2)
        self.save(28)
        del self.client.data[self.key(4)]
        revised = self.measure('addedAndDeleted',(1,0,1,size(self.key(28))))
        self.assertIn('pr-000000000000001c',revised)
        self.assertNotIn('pr-0000000000000004',revised)
        self.assertNotIn(self.key(4), self.archive.catalogue_versions)
        self.save(29)
        self.assertEqual(len(self.measure('addedOnly',(1,0,1,size(self.key(29))))),29)
        del self.client.data[self.key(29)]
        self.assertEqual(self.measure('deletedOnly',(1,0,0,0)),revised)
        changed['pr-0000000000000007']['name'] = 'Caller changed the response'
        current = next(p for p in self.archive.list_projects() if p['id']=='pr-0000000000000007')
        self.assertEqual(current['name'], 'Project 7 revision 2')

    def test_generation_change_and_deletion_during_body_read(self):
        self.archive.list_projects()
        self.save(4,2)
        def publish():
            self.client.after_list = None
            self.save(4,3)
        self.client.after_list = publish
        self.client.reset()
        self.expire()
        rows = self.archive.list_projects()
        self.assertEqual(next(r for r in rows if r['id']=='pr-0000000000000004')['revision'],3)
        self.observations['changedDuringRead'] = self.client.counts.copy()
        self.assertEqual(tuple(self.client.counts.values()), (2,0,2,len(self.client.data[self.key(4)]['body'])))
        self.save(12,2)
        def remove():
            self.client.after_list = None
            del self.client.data[self.key(12)]
        self.client.after_list = remove
        rows = self.measure('deletedDuringRead',(2,0,1,0))
        self.assertNotIn('pr-000000000000000c',rows)

    def test_repeated_churn_and_interrupted_list_keep_previous_picker_incomplete(self):
        self.archive.list_projects()
        self.save(4,2)
        self.client.after_list = lambda:self.save(4,3)
        self.expire()
        self.client.reset()
        pending = self.store.project_picker(self.archive)
        self.assertFalse(pending['complete'])
        self.archive.catalogue_thread.join(3)
        failed = self.store.project_picker(self.archive)
        self.assertFalse(failed['complete'])
        self.assertFalse(failed['refreshing'])
        self.assertTrue(failed['error'])
        self.assertEqual(len(failed['projects']),28)
        self.assertEqual(next(r for r in failed['projects'] if r['id']=='pr-0000000000000004')['revision'],1)
        self.observations['repeatedConflict'] = self.client.counts.copy()
        self.assertEqual(tuple(self.client.counts.values()), (2,0,2,0))
        self.client.after_list = None
        self.archive.catalogue_retry_at = 0
        def interrupted(*args,**kwargs):
            yield SimpleNamespace(name=self.key(1),generation=self.client.data[self.key(1)]['generation'])
            raise OSError('private diagnostic')
        with patch.object(self.client,'list_blobs',side_effect=interrupted):
            self.store.project_picker(self.archive)
            self.archive.catalogue_thread.join(3)
            failed = self.store.project_picker(self.archive)
            self.assertFalse(failed['complete'])
            self.assertEqual(len(failed['projects']),28)
            self.assertNotIn('private diagnostic',json.dumps(failed))
        latest = self.measure('recoveredAfterConflict',(1,0,1,len(self.client.data[self.key(4)]['body'])))
        self.assertEqual(latest['pr-0000000000000004']['revision'],3)
        self.assertTrue(self.store.project_picker(self.archive)['complete'])

    def test_transient_unreadable_manifest_is_visible_and_retried_at_same_generation(self):
        self.client.unreadable = self.key(1)
        first = self.archive.list_projects()
        row = next(p for p in first if p['id']=='pr-0000000000000001')
        self.assertIn('loadError',row)
        self.assertNotIn('private diagnostic',json.dumps(first))
        self.client.unreadable = None
        rows = self.measure('recoveredSameGeneration',(1,0,1,len(self.client.data[self.key(1)]['body'])))
        self.assertNotIn('loadError',rows['pr-0000000000000001'])

    def test_generation_cache_is_scoped_to_the_connected_google_archive(self):
        self.archive.list_projects()
        replacement = CountingClient()
        second = GCSObjects({'bucket':'second-fixture'},client=replacement)
        p = {'id':'pr-0000000000000000','name':'Second bucket','revision':8,'updated':8,'chapters':[]}
        second.put_json(self.key(0),{'project':p})
        self.assertEqual(replacement.data[self.key(0)]['generation'],self.client.data[self.key(0)]['generation'])
        self.archive.objects = second
        self.expire()
        self.assertEqual([row['name'] for row in self.archive.list_projects()],['Second bucket'])
        self.assertEqual(replacement.counts['bodyGETs'],1)
