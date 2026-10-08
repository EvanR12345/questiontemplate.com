"""Verified archive migration; no local downloads, deletion or provider switch."""
import hashlib
import io
import json
import re
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import PurePosixPath
from studio_data import validate_project


def digest_bytes(value):return hashlib.sha256(value).hexdigest()


def migrate_to_google(source, target, progress=None, workers=1):
    """Publish each manifest only after every referenced immutable asset exists.

    The caller must hold production idle, verify restored projects and publishing
    parity, then explicitly switch configuration. This function never does so.
    Existing target objects are verified; conflicting records are not overwritten.
    """
    if type(workers) is not int or not 1<=workers<=3:
        raise ValueError('Use one to three bounded archive transfer workers.')
    keys=set(source.keys('studio/'))|set(source.keys('publisher/'))
    manifests=sorted(k for k in keys if re.fullmatch(r'studio/manifests/pr-[a-f0-9]{16}\.json',k))
    assets=sorted(k for k in keys if k.startswith('studio/assets/'))
    records=sorted(keys-set(assets)-set(manifests))
    result={'assets':0,'records':0,'projects':0,'bytes':0,'localFilesCreated':0,'sourceObjectsDeleted':0}
    verified={}

    def emit():
        if progress:progress(dict(result))  # Counts only; no OAuth keys/content.

    def copy_asset(key):
        match=re.fullmatch(r'studio/assets/pr-[a-f0-9]{16}/([a-f0-9]{64})/(.+)',key)
        if not match:raise ValueError('Invalid source asset identity; migration stopped safely.')
        obj=source.open_object(key)
        try:return key,match[1],obj['bytes'],target.import_stream(key,obj,match[1])
        finally:obj['body'].close()

    # Submit only the bounded active window. A failure must not keep consuming
    # the entire archive queue, and a slow asset must not hide completed ones.
    # Active transfers finish before any history/project record is published.
    with ThreadPoolExecutor(max_workers=workers,thread_name_prefix='archive-copy') as pool:
        remaining=iter(assets)
        active={pool.submit(copy_asset,key) for key in [next(remaining,None) for _ in range(workers)] if key is not None}
        while active:
            completed,active=wait(active,return_when=FIRST_COMPLETED)
            # Check the whole finished window before submitting further work.
            for future in completed:
                key,checksum,size,generation=future.result()
                verified[key]=(checksum,size,generation)
                result['assets']+=1;result['bytes']+=size;emit()
            for _ in completed:
                key=next(remaining,None)
                if key is not None:active.add(pool.submit(copy_asset,key))

    def copy_record(key,body,content_type='application/json'):
        old,_=target.read(key)
        if old is not None and old!=body:
            raise ValueError('Target record conflicts with source; both archives retained.')
        obj={'body':io.BytesIO(body),'bytes':len(body),'contentType':content_type}
        try:target.import_stream(key,obj,digest_bytes(body))
        finally:obj['body'].close()
        result['records']+=1;result['bytes']+=len(body);emit()

    for key in records:
        verification_text=bool(re.fullmatch(r'studio/verification/[a-f0-9]{32}\.txt',key))
        if not key.endswith('.json') and not verification_text:
            raise ValueError('Unexpected non-asset source object; review before migration.')
        body,_=source.read(key)
        if body is None:raise ValueError('Source changed during migration; retry from the preserved archive.')
        if verification_text and len(body)>4096:
            raise ValueError('Unexpected verification record size; review before migration.')
        copy_record(key,body,'text/plain' if verification_text else 'application/json')

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
