import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from google_storage import GCSObjects
from studio_data import ProjectStore, new_project
from studio_storage import R2Archive, sha


class Blob:
    def __init__(self, bucket, name, chunk_size=None):
        self.bucket = bucket;self.name = name;self.metadata = None;self.generation = None
        self.size = None;self.chunk_size = chunk_size

    def reload(self, **kwargs):
        row = self.bucket.data[self.name]
        self.generation = row['generation'];self.size = len(row['body'])
        self.metadata = copy.deepcopy(row['metadata'])

    def upload_from_string(self, data, if_generation_match=None, **kwargs):
        current = self.bucket.data.get(self.name, {}).get('generation', 0)
        if if_generation_match is not None and current != if_generation_match:
            raise ValueError('Concurrent update')
        self.bucket.serial += 1
        self.bucket.data[self.name] = {'body':data, 'generation':self.bucket.serial,
                                      'metadata':copy.deepcopy(self.metadata)}
        self.reload()

    def upload_from_filename(self, name, **kwargs):
        self.upload_from_string(Path(name).read_bytes(), **kwargs)

    def upload_from_file(self, stream, **kwargs):
        data=bytearray()
        while part:=stream.read(3):data.extend(part)
        self.upload_from_string(bytes(data), **kwargs)

    def open(self, mode, if_generation_match=None, **kwargs):
        return io.BytesIO(self.download_as_bytes(if_generation_match=if_generation_match))

    def download_as_bytes(self, if_generation_match=None, **kwargs):
        row = self.bucket.data[self.name]
        if if_generation_match != row['generation']:
            raise ValueError('Concurrent update')
        return row['body']

    def download_to_filename(self, name, **kwargs):
        Path(name).write_bytes(self.download_as_bytes(**kwargs))

    def generate_signed_url(self, **kwargs):
        self.bucket.signed = kwargs
        return 'https://storage.googleapis.com/private-test/signed'


class Client:
    def __init__(self):self.data = {};self.serial = 0;self.signed = None
    def bucket(self, name):return self
    def blob(self, key, **kwargs):return Blob(self, key, **kwargs)
    def get_blob(self, key, **kwargs):
        if key not in self.data:return None
        blob = self.blob(key);blob.reload();return blob
    def list_blobs(self, bucket, prefix, **kwargs):
        return [SimpleNamespace(name=k) for k in self.data if k.startswith(prefix)]


class GoogleStorageTests(unittest.TestCase):
    def test_streamed_import_verifies_hash_and_reuses_only_matching_content(self):
        import hashlib
        body=b'story asset in cloud';checksum=hashlib.sha256(body).hexdigest()
        source={'body':io.BytesIO(body),'bytes':len(body)}
        generation=self.objects.import_stream('asset',source,checksum)
        self.assertTrue(self.objects.matches_verified('asset',checksum,len(body),generation))
        self.assertEqual(self.objects.verify_object('asset',checksum,len(body)),generation)
        self.client.data['asset']['body']=b'X'*len(body)
        with self.assertRaisesRegex(ValueError,'checksum mismatch'):
            self.objects.import_stream('asset',{'body':io.BytesIO(body),'bytes':len(body)},checksum)
    def test_corrupt_stream_does_not_return_successful_migration_receipt(self):
        with self.assertRaisesRegex(ValueError,'source checksum mismatch'):
            self.objects.import_stream('asset',{'body':io.BytesIO(b'wrong'),'bytes':5},'a'*64)
    def setUp(self):
        self.client = Client()
        self.objects = GCSObjects({'bucket':'private-test'}, client=self.client)

    def test_stale_and_create_only_manifest_writes_preserve_newer_revision(self):
        self.assertEqual(self.objects.read('manifest'), (None, None))
        self.objects.put_json('manifest', {'revision':1}, create=True)
        _, token = self.objects.read('manifest')
        self.objects.put_json('manifest', {'revision':2}, etag=token)
        for kwargs in ({'create':True}, {'etag':token}):
            with self.assertRaisesRegex(ValueError, 'Concurrent update'):
                self.objects.put_json('manifest', {'revision':0}, **kwargs)
        body, _ = self.objects.read('manifest')
        self.assertEqual(json.loads(body)['revision'], 2)

    def test_asset_upload_reuses_verified_object_and_rejects_collision(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'image.png';path.write_bytes(b'story-image')
            digest = sha(path)
            self.objects.upload('asset', path, digest)
            generation = self.client.data['asset']['generation']
            self.objects.upload('asset', path, digest)
            self.assertEqual(self.client.data['asset']['generation'], generation)
            self.client.data['asset']['metadata']['sha256'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'verification failed'):
                self.objects.upload('asset', path, digest)
            self.assertEqual(path.read_bytes(), b'story-image')

    def test_project_restore_and_assets_survive_complete_cache_restart(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            store = ProjectStore(first);p = store.save(new_project('Google story'))
            asset = store.asset(p['id'], 'art.png');asset.write_bytes(b'saved story art')
            archive = R2Archive(store, Path(first)/'absent');archive.close()
            archive.objects = self.objects;archive.enabled = True;archive.sync(p['id'])
            restored = ProjectStore(second)
            cloud = R2Archive(restored, Path(second)/'absent', objects=self.objects);cloud.close()
            self.assertEqual(cloud.status()['provider'], 'Google Cloud Storage')
            self.assertTrue(cloud.status()['publisherCompatible'])
            self.assertEqual(restored.load(p['id'])['name'], 'Google story')
            self.assertEqual(restored.asset(p['id'], 'art.png').read_bytes(), asset.read_bytes())
            self.assertTrue(asset.exists())

    def test_download_condition_and_private_expiring_url(self):
        self.objects.put_json('test', {'ok':True}, create=True)
        blob = self.client.get_blob('test')
        self.objects.put_json('test', {'ok':False})
        with self.assertRaisesRegex(ValueError, 'Concurrent update'):
            blob.download_as_bytes(if_generation_match=blob.generation)
        self.objects.url('test')
        self.assertEqual(self.client.signed['version'], 'v4')
        self.assertEqual(self.client.signed['expiration'].total_seconds(), 3600)
        self.assertEqual(self.client.signed['method'], 'GET')

    def test_invalid_bucket_rejected_before_any_client_access(self):
        with self.assertRaises(ValueError):GCSObjects({'bucket':'../private'}, client=self.client)

    def test_invalid_google_connection_cannot_silently_publish_from_r2(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp)/'private-config.json'
            config.write_text(json.dumps({'provider':'gcs','bucket':'private-test','serviceAccount':{}}))
            archive = R2Archive(ProjectStore(tmp), config);archive.close()
            self.assertFalse(archive.enabled)
            self.assertFalse(archive.status()['publisherCompatible'])
            self.assertTrue(archive.status()['error'])


if __name__ == '__main__':unittest.main()
