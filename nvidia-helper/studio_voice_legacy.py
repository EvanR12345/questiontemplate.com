"""Read legacy private notebook ownership from its original, signed request."""
import ast
import hashlib
import json
import re


def original_request(folder, signature):
    if not re.fullmatch(r'[0-9a-f]{64}', signature):
        raise ValueError('Invalid original Kaggle request signature.')
    with (folder / ('.kaggle-' + signature[:16]) / 'voice_job.py').open(encoding='utf-8') as script:
        first = script.readline()
    if not first.startswith('REQUEST = '):
        raise ValueError('Missing original Kaggle request.')
    request = ast.literal_eval(first.removeprefix('REQUEST = '))
    actual = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    if actual != signature:
        raise ValueError('Original Kaggle request failed its signature check.')
    return request


def matching_members(previous, request, chapter_id=None):
    fields = ('model', 'voice', 'speed', 'instruction', 'effects', 'qwenSpeaker', 'reference', 'runtimeVersion')
    if any(previous.get(k) != request.get(k) for k in fields):
        return set()
    wanted = {}
    for part in request['parts']:
        key = chapter_id or part['chapterId']
        wanted.setdefault(key, []).append(dict(part, chapterId=key))
    members = {}
    for part in previous['parts']:
        members.setdefault(part['chapterId'], []).append(part)
    return {key for key, parts in wanted.items() if members.get(key) == parts}


def overlapping_group(folder, request, chapter_id=None, current_journal=None):
    """Find an earlier notebook with the same chapter, input passages and actual voice."""
    if not request['parts'] or (not chapter_id and not request['parts'][0].get('chapterId')):
        return None
    wanted = {chapter_id} if chapter_id else {part['chapterId'] for part in request['parts']}
    for journal in folder.glob('*.opus.kaggle.json'):
        if journal == current_journal:
            continue
        saved = json.loads(journal.read_text(encoding='utf-8'))
        if saved.get('state') in ('REJECTED', 'FAILED'):
            continue
        # New journals carry the chapter IDs observed at admission. A possible
        # match still requires the original request and its SHA-256 check.
        chapter_ids = saved.get('chapterIds')
        if isinstance(chapter_ids, list) and chapter_ids and all(isinstance(key, str) for key in chapter_ids):
            if wanted.isdisjoint(chapter_ids):
                continue
        try:
            previous = original_request(folder, saved['signature'])
        except (OSError, ValueError, KeyError, TypeError, SyntaxError) as error:
            raise ValueError('Recover the original private Kaggle group request before admitting another chapter.') from error
        if matching_members(previous, request, chapter_id):
            return journal, saved, previous
    return None
