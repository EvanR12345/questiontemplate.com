"""Explicit recovery of a private immutable history edition with no current project."""
import hashlib
import json
import math
import re
from pathlib import PurePosixPath

from studio_data import new_chapter, new_project, validate_project


PROJECT = r'pr-[a-f0-9]{16}'
EDITION = r'(?:0|[1-9][0-9]*)-[a-f0-9]{16}'
HISTORY = re.compile(rf'studio/history/({PROJECT})/({EDITION})\.json')
HEAD = re.compile(rf'studio/manifests/({PROJECT})\.json')


def checked_path(archive, pid, name):
    if not isinstance(name, str) or not name or '\\' in name or ':' in name:
        raise ValueError('Invalid archived asset path.')
    path = PurePosixPath(name)
    if path.is_absolute() or path.as_posix() != name or any(part.startswith('.') for part in path.parts):
        raise ValueError('Invalid archived asset path.')
    if name.casefold() in {'project.json', 'revision.json', 'project.previous.json'}:
        raise ValueError('Reserved archived asset path.')
    archive.store.asset_local(pid, name)


def required_paths(project):
    """Saved media fields only. Diagnostics and execution provenance can also have a 'path'."""
    def links(owner, fields, lists=()):
        if not isinstance(owner, dict):
            return
        for field in fields:
            name = owner.get(field)
            if isinstance(name, str) and name:
                yield name
        for field in lists:
            for item in owner.get(field, []):
                yield from links(item, ('path', 'imagePath'))

    def export(value):
        yield from links(value, ('path',), ('parts', 'chapters'))
        if isinstance(value, dict):
            yield from links(value.get('narrationRender'), ('path',))

    def shot_media(shot):
        yield from links(shot, ('imagePath', 'sourceImagePath'), ('referenceImages', 'history'))

    yield from links(project, (), ('styleReferences', 'thumbnails', 'renderHistory'))
    yield from export(project.get('render'))
    intro = project['intro']
    yield from links(intro, ('audioPath', 'visualPath', 'videoPath'), ('renderHistory',))
    for shot in intro.get('shots', []):
        yield from shot_media(shot)
    yield from links(project['settings'].get('watermark'), ('imagePath',))
    yield from links(project['settings'].get('engagement'), ('outroAudioPath',))
    for record in project.get('characters', []) + project.get('locations', []):
        yield from links(record, (), ('references',))
    for chapter in project['chapters']:
        yield from links(chapter.get('audio'), ('path',))
        yield from links(chapter, (), ('renderHistory',))
        yield from export(chapter.get('render'))
        for person in chapter.get('people', []):
            yield from links(person, (), ('references',))
        for scene in chapter.get('scenes', []):
            for shot in scene.get('shots', []):
                yield from shot_media(shot)


def read_edition(archive, pid, edition):
    key = f'studio/history/{pid}/{edition}.json'
    raw, _ = archive.objects.read(key)
    if raw is None:
        raise ValueError('This archived edition is unavailable.')
    manifest = json.loads(raw)
    if not isinstance(manifest, dict) or manifest.get('version') != 1:
        raise ValueError('Unsupported archived edition.')
    project = validate_project(manifest['project'])
    files = manifest['files']
    if (project['id'] != pid or type(project['revision']) is not int or project['revision'] < 0
            or not isinstance(project['name'], str) or type(project['updated']) not in (int, float)
            or not math.isfinite(project['updated']) or not isinstance(files, dict)
            or archive.history_key(pid, project, files) != key):
        raise ValueError('Archived edition identity does not match.')
    for path, record in files.items():
        checked_path(archive, pid, path)
        if (not isinstance(record, dict) or not isinstance(record.get('sha256'), str)
                or not re.fullmatch(r'[a-f0-9]{64}', record['sha256'])
                or type(record.get('bytes')) is not int or record['bytes'] < 0
                or record.get('key') != f'studio/assets/{pid}/{record["sha256"]}/{path}'):
            raise ValueError('Invalid archived asset identity.')
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError('Conflicting archived asset names.')
    missing = {name.replace('\\', '/') for name in required_paths(project)} - files.keys()
    aliases = manifest.get('renderCache', {})
    if not isinstance(aliases, dict):
        raise ValueError('Invalid archived render cache.')
    for alias, record in aliases.items():
        checked_path(archive, pid, alias)
        if not isinstance(record, dict) or not isinstance(record.get('path'), str):
            raise ValueError('Invalid archived render cache.')
        target = files.get(record['path'])
        if not target or any(record.get(k) != target[k] for k in ('key', 'bytes', 'sha256')):
            raise ValueError('Archived render cache references an unknown asset.')
    summary = {k:project[k] for k in ('id', 'name', 'revision', 'updated')}
    summary.update(edition=edition, files=len(files), chapters=len(project['chapters']),
                   storyCharacters=sum(len(c.get('sourceText','')) for c in project['chapters']))
    identity = hashlib.sha256(f'studio-writing-copy/{pid}/{edition}'.encode()).hexdigest()[:16]
    summary.update(missingMedia=len(missing), recoverable=not missing, writingCopyId=f'pr-{identity}')
    return manifest, summary


def list_editions(archive):
    if not archive.enabled:
        raise ValueError('Connect private cloud storage to find archived projects.')
    try:
        active = {m[1] for key in archive.objects.keys('studio/manifests/') if (m := HEAD.fullmatch(key))}
        projects = []
        unreadable = 0
        for key in archive.objects.keys('studio/history/'):
            match = HISTORY.fullmatch(key)
            if not match:
                continue
            pid, edition = match.groups()
            if pid in active or archive.store.folder(pid).exists():
                continue
            try:
                _, summary = read_edition(archive, pid, edition)
            except (ValueError, KeyError, TypeError, AttributeError):
                unreadable += 1
                continue
            projects.append(summary)
        projects.sort(key=lambda p:(p['updated'], p['revision'], p['id'], p['edition']), reverse=True)
        return {'projects':projects, 'unreadableEditions':unreadable}
    except Exception as error:
        raise ValueError('Archived projects could not be listed. Check private cloud access and retry.') from error


def writing_copy(archive, source, summary):
    pid = summary['writingCopyId']
    origin = {'project':source['id'], 'edition':summary['edition']}
    head = f'studio/manifests/{pid}.json'

    def result(project):
        if project.get('id') != pid or project.get('archiveWritingSource') != origin:
            raise ValueError('A different project already uses the writing-copy ID.')
        return {'writingOnly':True, 'project':{
            'id':pid, 'name':project['name'], 'updated':project['updated'], 'revision':project['revision'],
            'chapters':len(project['chapters']), 'edition':summary['edition']}}

    copy = new_project(source['name'] + ' (recovered writing)')
    copy.update(id=pid, archiveWritingSource=origin, chapters=[])
    if isinstance(source.get('notes'), str):
        copy['notes'] = source['notes']
    if isinstance(source['intro'].get('voiceText'), str):
        copy['intro']['voiceText'] = source['intro']['voiceText']
    for number, chapter in enumerate(source['chapters'], start=1):
        target = new_chapter(number)
        for key in ('name', 'sourceText', 'cleanNarrationText', 'narrationMode'):
            if isinstance(chapter.get(key), str):
                target[key] = chapter[key]
        if type(chapter.get('includeChapterLabel')) is bool:
            target['includeChapterLabel'] = chapter['includeChapterLabel']
        copy['chapters'].append(target)
    validate_project(copy)
    with archive.store.lock:
        if archive.store.folder(pid).exists():
            return result(archive.store.load_local(pid))
        existing, _ = archive.objects.read(head)
        if existing is not None:
            return result(json.loads(existing)['project'])
        if archive.store.folder(source['id']).exists():
            raise ValueError('The original project already has local files.')
        manifest = {'version':1, 'project':copy, 'files':{}, 'savedAt':copy['updated']}
        try:
            archive.objects.put_json(head, manifest, create=True)
        except Exception:
            # Concurrent requests or a lost success response can already have created it.
            existing, _ = archive.objects.read(head)
            if existing is None:
                raise ValueError('Could not confirm creation of the writing copy.')
            copy = json.loads(existing)['project']
        archive.manifests.pop(pid, None)
        with archive.catalogue_lock:
            archive.catalogue_at = 0
        return result(copy)


def recover_edition(archive, pid, edition, mode=None):
    if not archive.enabled:
        raise ValueError('Connect private cloud storage to recover a project.')
    if mode not in (None, 'writing'):
        raise ValueError('Choose an available recovery action.')
    if not isinstance(pid, str) or not re.fullmatch(PROJECT, pid) or not isinstance(edition, str) or not re.fullmatch(EDITION, edition):
        raise ValueError('Choose a listed archived project and edition.')
    head = f'studio/manifests/{pid}.json'
    if archive.store.folder(pid).exists():
        raise ValueError('This project already has local files.')
    try:
        current, _ = archive.objects.read(head)
    except Exception as error:
        raise ValueError('Could not verify the current private project.') from error
    if current is not None:
        raise ValueError('A current cloud project already exists.')
    try:
        manifest, summary = read_edition(archive, pid, edition)
        if mode == 'writing':
            return writing_copy(archive, manifest['project'], summary)
        if summary['missingMedia']:
            raise ValueError('The archived manifest lacks referenced media.')
        for record in manifest['files'].values():
            archive.objects.verify_object(record['key'], record['sha256'], record['bytes'])
    except Exception as error:
        raise ValueError('The archived edition or one of its assets could not be verified. Recovery stopped.') from error
    with archive.store.lock:
        if archive.store.folder(pid).exists():
            raise ValueError('This project already has local files.')
        try:
            archive.objects.put_json(head, manifest, create=True)
        except Exception as error:
            raise ValueError('Could not confirm recovery. Refresh the current projects before retrying.') from error
        archive.manifests.pop(pid, None)
        with archive.catalogue_lock:
            archive.catalogue_at = 0
    return {'project':summary}
