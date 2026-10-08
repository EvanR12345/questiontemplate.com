"""Verified archive migration; no local downloads, deletion or provider switch."""
import hashlib
import io
import json
import re
from pathlib import PurePosixPath
from studio_data import validate_project


def digest_bytes(value):return hashlib.sha256(value).hexdigest()


def migrate_to_google(source, target, progress=None):
    """Publish each manifest only after every referenced immutable asset exists.

    The caller must hold production idle, verify restored projects and publishing
    parity, then explicitly switch configuration. This function never does so.
    Existing target objects are verified; conflicting records are not overwritten.
    """
    keys=set(source.keys('studio/'))|set(source.keys('publisher/'))
    manifests=sorted(k for k in keys if re.fullmatch(r'studio/manifests/pr-[a-f0-9]{16}\.json',k))
    assets=sorted(k for k in keys if k.startswith('studio/assets/'))
    records=sorted(keys-set(assets)-set(manifests))
    result={'assets':0,'records':0,'projects':0,'bytes':0,'localFilesCreated':0,'sourceObjectsDeleted':0}
    verified={}

    def emit():
        if progress:progress(dict(result))  # Counts only; no OAuth keys/content.

    for key in assets:
        match=re.fullmatch(r'studio/assets/pr-[a-f0-9]{16}/([a-f0-9]{64})/(.+)',key)
        if not match:raise ValueError('Invalid source asset identity; migration stopped safely.')
        obj=source.open_object(key)
        try:verified[key]=(match[1],obj['bytes'],target.import_stream(key,obj,match[1]))
        finally:obj['body'].close()
        result['assets']+=1;result['bytes']+=obj['bytes'];emit()

    def copy_record(key,body):
        old,_=target.read(key)
        if old is not None and old!=body:
            raise ValueError('Target record conflicts with source; both archives retained.')
        obj={'body':io.BytesIO(body),'bytes':len(body),'contentType':'application/json'}
        try:target.import_stream(key,obj,digest_bytes(body))
        finally:obj['body'].close()
        result['records']+=1;result['bytes']+=len(body);emit()

    for key in records:
        if not key.endswith('.json'):
            raise ValueError('Unexpected non-asset source object; review before migration.')
        body,_=source.read(key)
        if body is None:raise ValueError('Source changed during migration; retry from the preserved archive.')
        copy_record(key,body)

    for key in manifests:
        body,etag=source.read(key)
        if body is None:raise ValueError('Source project disappeared during migration.')
        manifest=json.loads(body);project=validate_project(manifest['project'])
        pid=key.split('/')[-1][:-5]
        if project['id']!=pid:raise ValueError('Source manifest project identity mismatch.')
        for name,record in manifest['files'].items():
            parts=name.split('/')
            if PurePosixPath(name).is_absolute() or '\\' in name or any(part in ('','.','..') for part in parts):
                raise ValueError('Unsafe source manifest asset path.')
            sha=record.get('sha256','');size=record.get('bytes')
            if not re.fullmatch(r'[a-f0-9]{64}',sha) or type(size) is not int or size<0 or record.get('key')!=f'studio/assets/{pid}/{sha}/{name}':
                raise ValueError('Invalid source manifest asset record.')
            previous=verified.get(record['key'])
            if previous is None or previous[:2]!=(sha,size) or not target.matches_verified(record['key'],sha,size,previous[2]):
                verified[record['key']]=(sha,size,target.verify_object(record['key'],sha,size))
        current,current_etag=source.read(key)
        if current_etag!=etag or current!=body:raise ValueError('Source project changed; migration did not publish its manifest.')
        copy_record(key,body);result['projects']+=1;emit()
    return result
