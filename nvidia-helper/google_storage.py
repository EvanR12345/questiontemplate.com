"""Private Google Cloud Storage adapter for the existing immutable archive.

No ambient credentials, public ACLs, bucket creation or deletion. Generation
preconditions preserve concurrent project edits; the archive verifies SHA-256
again when restoring an asset. Loading this module does not load a model.
"""
import datetime
import json
import mimetypes
import re
import hashlib
import io
from studio_storage import attachment_disposition


class HashingReader(io.RawIOBase):
    """Bounded, non-seekable source used only for streamed cloud migration."""
    def __init__(self, source):
        super().__init__();self.source=source;self.checksum=hashlib.sha256();self.count=0
    def readable(self):return True
    def seekable(self):return False
    def tell(self):return self.count
    def read(self, size=-1):
        data=self.source.read(size);self.checksum.update(data);self.count+=len(data);return data
    def readinto(self, buffer):
        data=self.read(len(buffer));buffer[:len(data)]=data;return len(data)


class GCSObjects:
    provider = 'Google Cloud Storage'
    publisher_compatible = True  # Publisher must still verify its active provider.

    def __init__(self, config, client=None):
        name = config.get('bucket', '')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', name):
            raise ValueError('Enter a valid private bucket name.')
        chunk_mib=config.get('uploadChunkMiB',8)
        if type(chunk_mib) is not int or chunk_mib not in (8,16,32,64):
            raise ValueError('Google upload chunks must be 8, 16, 32 or 64 MiB.')
        self.upload_chunk_size=chunk_mib*1024*1024
        if client is None:
            from google.cloud import storage
            if config.get('authMode') == 'federated':
                import google.auth
                from urllib.parse import urlparse
                info = config.get('externalAccount', {})
                issuer = urlparse(config.get('identityIssuer', ''))
                source = info.get('credential_source', {})
                impersonation = info.get('service_account_impersonation_url', '')
                email = config.get('serviceAccountEmail', '')
                if (issuer.scheme != 'https' or not issuer.hostname or not issuer.hostname.endswith('.workers.dev') or
                    issuer.username or issuer.password or issuer.path not in ('','/') or issuer.query or issuer.fragment or
                    not re.fullmatch(r'[a-z][a-z0-9-]{4,62}',config.get('projectId','')) or
                    info.get('type') != 'external_account' or
                    set(info) != {'type','audience','subject_token_type','token_url','service_account_impersonation_url','credential_source'} or
                    info.get('token_url') != 'https://sts.googleapis.com/v1/token' or
                    source.get('url') != issuer.geturl().rstrip('/')+'/identity/token' or
                    source.get('format') != {'type':'json','subject_token_field_name':'token'} or
                    set(source) != {'url','headers','format'} or
                    set(source.get('headers', {})) != {'Authorization'} or
                    not isinstance(source['headers']['Authorization'], str) or
                    not source['headers']['Authorization'].startswith('Bearer ') or
                    not re.fullmatch(r'[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com', email) or
                    impersonation != 'https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/'+email+':generateAccessToken' or
                    not re.fullmatch(r'//iam\.googleapis\.com/projects/\d+/locations/global/workloadIdentityPools/[a-z0-9-]+/providers/[a-z0-9-]+',info.get('audience','')) or
                    info.get('subject_token_type') != 'urn:ietf:params:oauth:token-type:jwt'):
                    raise ValueError('Invalid private Studio workload identity configuration.')
                from google.auth import impersonated_credentials
                source_info = dict(info)
                source_info.pop('service_account_impersonation_url')
                source_credentials, _ = google.auth.load_credentials_from_dict(source_info, scopes=['https://www.googleapis.com/auth/cloud-platform'])
                # Explicit impersonation exposes Google's managed signBlob signer
                # for existing private preview URLs; no downloaded signing key.
                credentials = impersonated_credentials.Credentials(source_credentials=source_credentials,
                    target_principal=email, target_scopes=['https://www.googleapis.com/auth/devstorage.read_write'], lifetime=3600)
                client = storage.Client(project=config.get('projectId'), credentials=credentials)
            else:
                if config.get('authMode') not in (None,'service-account'):
                    raise ValueError('Unsupported private Google authentication mode.')
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
        blob = self.bucket.blob(key, chunk_size=self.upload_chunk_size)
        blob.metadata = {'sha256': checksum}
        blob.upload_from_filename(str(path),
            content_type=mimetypes.guess_type(path.name)[0] or 'application/octet-stream',
            if_generation_match=0, timeout=120, checksum='auto')
        blob.reload(timeout=60)
        self._verify(blob, path, checksum)

    def download(self, key, target):
        blob = self.bucket.get_blob(key, timeout=60)
        if blob is None:
            raise FileNotFoundError('Cloud asset is unavailable.')
        blob.download_to_filename(str(target), if_generation_match=blob.generation,
                                  timeout=60, checksum='auto')

    def verify_object(self, key, checksum, size):
        blob=self.bucket.get_blob(key, timeout=60)
        if blob is None:raise FileNotFoundError('Migration target asset is missing.')
        if blob.size!=size:raise ValueError('Migration target size mismatch; originals retained.')
        digest=hashlib.sha256()
        with blob.open('rb', chunk_size=8*1024*1024, if_generation_match=blob.generation) as stream:
            for part in iter(lambda:stream.read(8*1024*1024), b''):digest.update(part)
        if digest.hexdigest()!=checksum:raise ValueError('Migration target checksum mismatch; originals retained.')
        return str(blob.generation)

    def matches_verified(self, key, checksum, size, generation):
        blob=self.bucket.get_blob(key, timeout=60)
        return bool(blob is not None and str(blob.generation)==generation and
                    blob.size==size and (blob.metadata or {}).get('sha256')==checksum)

    def import_stream(self, key, source, checksum):
        """Copy bytes between clouds in bounded memory, without local cache files."""
        existing=self.bucket.get_blob(key, timeout=60)
        if existing is not None:
            return self.verify_object(key,checksum,source['bytes'])
        # Smaller replayable HTTP chunks keep laptop/cloud migration memory low
        # and avoid sending a single 64 MiB request through an unstable uplink.
        blob=self.bucket.blob(key, chunk_size=8*1024*1024)
        blob.metadata={'sha256':checksum}
        reader=HashingReader(source['body'])
        blob.upload_from_file(reader, size=source['bytes'], rewind=False,
                             content_type=source.get('contentType','application/octet-stream'),
                             if_generation_match=0, timeout=120, checksum='auto')
        if reader.count!=source['bytes'] or reader.checksum.hexdigest()!=checksum:
            raise ValueError('Migration source checksum mismatch; no project manifest published.')
        blob.reload(timeout=60)
        if blob.size!=source['bytes'] or (blob.metadata or {}).get('sha256')!=checksum:
            raise ValueError('Migration target verification failed; originals retained.')
        return str(blob.generation)

    def keys(self, prefix):
        for blob in self.client.list_blobs(self.bucket, prefix=prefix, timeout=60):
            yield blob.name

    def url(self, key, download_name=None):
        options={}
        if download_name is not None:options['response_disposition']=attachment_disposition(download_name)
        return self.bucket.blob(key).generate_signed_url(
            version='v4', expiration=datetime.timedelta(hours=1), method='GET', **options)
