"""Location evidence for the file dashboard, without downloading or deleting assets."""
from pathlib import Path


def located_files(store, pid, rows, storage_enabled=False, sync_status=None):
    result = {r['path']: dict(r) for r in rows}
    inspected = set()
    # Show references that have lost BOTH copies, including saved historical versions.
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('path', 'imagePath', 'audioPath', 'visualPath', 'sourceImagePath') and isinstance(child, str) and child and '://' not in child:
                    # Repeated references/history entries are common in long
                    # stories. Known inventory rows are checked once below;
                    # unknown references need only one check in this request.
                    if child in result or child in inspected:
                        continue
                    inspected.add(child)
                    try:
                        local = store.asset_local(pid, child)
                    except ValueError:
                        continue
                    if not local.exists():
                        result[child] = {'path': child, 'bytes': 0, 'cloud': False, 'category':
                            'Video' if local.suffix == '.mp4' else 'Audio' if local.suffix in ('.wav','.flac','.m4a','.opus','.mp3') else 'Images'}
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value: visit(child)
    visit(store.load_local(pid))
    for name, row in result.items():
        local = store.asset_local(pid, name).is_file()
        cloud = bool(row.get('cloud'))
        row.update(local=local, storageStatus='CLOUD_SAVED' if cloud else
                   'PENDING_UPLOAD' if local and storage_enabled and sync_status in ('UPLOADING','QUEUED','PENDING') else
                   'LOCAL_ONLY' if local else 'MISSING',
                   removalAdvice='Cloud checksum recorded at upload; reverify remotely before deleting a local copy.' if cloud and local else
                   'Already cloud-only; no local copy to remove.' if cloud else
                   'Do not remove: no matching cloud copy is recorded.' if local else 'Neither copy is recorded. Restore a backup or regenerate.')
    return [result[name] for name in sorted(result)]
