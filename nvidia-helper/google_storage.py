"""Private Google Cloud Storage adapter for the existing immutable archive.

No ambient credentials, public ACLs, bucket creation or deletion. Generation
preconditions preserve concurrent project edits; the archive verifies SHA-256
again when restoring an asset. Loading this module does not load a model.
"""
import datetime
import json
import mimetypes
import re


class GCSObjects:
    provider = 'Google Cloud Storage'
    publisher_compatible = False  # The current publisher binds the R2 bucket.

    def __init__(self, config, client=None):
        name = config.get('bucket', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', name):
            raise ValueError('Enter a valid private bucket name.')
        if client is None:
            from google.cloud import storage
            info = config.get('serviceAccount', {})
            if info.get('type') != 'service_account' or not info.get('private_key') or not info.get('client_email'):
                raise ValueError('A private service-account credential is required.')
            client = storage.Client.from_service_account_info(info)
        self.client = client
        self.bucket = client.bucket(name)

    def read(self, key):
        blob = self.bucket.get_blob(key, timeout=60)
        if blob is None:
            return None, None
        generation = blob.generation
        # Pin the body to the same version as the concurrency token.
        return blob.download_as_bytes(if_generation_match=generation, timeout=60), str(generation)

    def put_json(self, key, value, etag=None, create=False):
        blob = self.bucket.blob(key)
        condition = int(etag) if etag is not None else (0 if create else None)
        blob.upload_from_string(
            json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode(),
            content_type='application/json', if_generation_match=condition, timeout=60,
        )

    @staticmethod
    def _verify(blob, path, checksum):
        if blob.size != path.stat().st_size or (blob.metadata or {}).get('sha256') != checksum:
            raise ValueError('Cloud upload verification failed; local file retained.')

    def upload(self, key, path, checksum):
        existing = self.bucket.get_blob(key, timeout=60)
        if existing is not None:
            self._verify(existing, path, checksum)
            return
        blob = self.bucket.blob(key, chunk_size=64 * 1024 * 1024)
        blob.metadata = {'sha256': checksum}
        blob.upload_from_filename(str(path),
            content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            if_generation_match=0, timeout=60, checksum='auto')
        blob.reload(timeout=60)
        self._verify(blob, path, checksum)

    def download(self, key, target):
        blob = self.bucket.get_blob(key, timeout=60)
        if blob is None:
            raise FileNotFoundError('Cloud asset is unavailable.')
        blob.download_to_filename(str(target), if_generation_match=blob.generation,
                                  timeout=60, checksum='auto')

    def keys(self, prefix):
        for blob in self.client.list_blobs(self.bucket, prefix=prefix, timeout=60):
            yield blob.name

    def url(self, key):
        return self.bucket.blob(key).generate_signed_url(
            version='v4', expiration=datetime.timedelta(hours=1), method='GET')
