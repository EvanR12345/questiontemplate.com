"""Immutable file-body transport and bounded owned upload admission.

Mix this implementation into GCSObjects after review. It preserves an immutable
body and returns the generation from the upload response, never a later GET.
An owner catching ReceiptVerificationError must durably record native_receipt
on its existing UNKNOWN row before closing admission; it must not adopt it.
"""
from contextlib import contextmanager
import base64
import hashlib
import mimetypes
from pathlib import Path
import tempfile


def stamp(path):
    s = path.stat()
    return (s.st_size, s.st_mtime_ns, s.st_ctime_ns)


class ReceiptVerificationError(ValueError):
    def __init__(self, receipt):
        super().__init__('Cloud upload verification failed; native receipt retained.')
        self.native_receipt = receipt


@contextmanager
def frozen_body(path, checksum):
    """Bounded-memory copy, verified before any remote request or paid admission."""
    path = Path(path)
    before = stamp(path)
    digest = hashlib.sha256()
    size = 0
    with tempfile.TemporaryFile('w+b') as snapshot:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
                snapshot.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        if stamp(path) != before or size != before[0] or digest.hexdigest() != checksum:
            raise ValueError('Source changed before immutable upload admission.')
        snapshot.flush()
        snapshot.seek(0)
        yield snapshot, size


def native_receipt(blob, checksum, reused):
    return {'generation': str(blob.generation) if blob.generation is not None else None,
            'bytes': blob.size, 'sha256': (blob.metadata or {}).get('sha256'),
            'crc32c': getattr(blob, 'crc32c', None),
            'storageClass': getattr(blob, 'storage_class', None), 'reused': reused}


class ImmutableUploadMixin:
    def upload(self, key, path, checksum):
        path = Path(path)
        with frozen_body(path, checksum) as (snapshot, size):
            return self.upload_frozen(key, snapshot, size, checksum, path.name)

    def upload_frozen(self, key, snapshot, size, checksum, original_name):
        existing = self.bucket.get_blob(key, timeout=60)
        if existing is not None:
            receipt = native_receipt(existing, checksum, True)
            if existing.size != size or (existing.metadata or {}).get('sha256') != checksum:
                raise ReceiptVerificationError(receipt)
            # Existing bytes are not writer attribution. The owner must
            # compare the returned generation to its previous owned map.
            return receipt
        blob = self.bucket.blob(key, chunk_size=self.upload_chunk_size)
        blob.metadata = {'sha256': checksum}
        blob.upload_from_file(snapshot, size=size, rewind=True,
            content_type=mimetypes.guess_type(original_name)[0] or 'application/octet-stream',
            if_generation_match=0, timeout=120, checksum='auto')
        receipt = native_receipt(blob, checksum, False)
        # Capture native identity before evaluating any later condition.
        generation = receipt['generation']
        if (not isinstance(generation, str) or not generation.isascii()
                or not generation.isdecimal() or receipt['bytes'] != size
                or (blob.metadata or {}).get('sha256') != checksum):
            raise ReceiptVerificationError(receipt)
        return receipt


class ImmutableOwnedUploadMixin(ImmutableUploadMixin):
    """Place before existing OwnedObjects in MRO; preserves its admission ledger.

    begin/complete/finish retain their existing lock, cap, ownership and durable
    journal semantics. This method changes only the asset transport body.
    """
    def upload(self, key, path, checksum):
        import google_crc32c
        path = Path(path)
        with frozen_body(path, checksum) as (snapshot, size):
            crc = google_crc32c.Checksum()
            for chunk in iter(lambda: snapshot.read(4 * 1024 * 1024), b''):
                crc.update(chunk)
            expected_crc = base64.b64encode(crc.digest()).decode()
            snapshot.seek(0)
            # All local copying and integrity work precedes paid admission.
            row = self.begin(key, size, 'asset')
            previous = self.owned.get(key)
            try:
                receipt = self.upload_frozen(key, snapshot, size, checksum, path.name)
                # Preserve the original response on both VERIFIED and UNKNOWN
                # rows. Later live-path edits or a foreign reuse do not change it.
                with self.lock:
                    row['receipt'] = dict(receipt)
                self.complete(row, receipt, expected_crc, previous)
            except BaseException as error:
                receipt = getattr(error, 'native_receipt', None)
                if receipt is not None:
                    with self.lock:
                        row['receipt'] = dict(receipt)
                self.finish(row, error)
                raise
            self.finish(row)
            return receipt


class SourceChangedAfterUpload(ValueError):
    """Known local supersession; this does not invalidate a completed remote receipt."""
    def __init__(self, receipt):
        super().__init__('An asset changed during upload. Previous cloud snapshot preserved.')
        self.native_receipt = dict(receipt) if isinstance(receipt, dict) else None
