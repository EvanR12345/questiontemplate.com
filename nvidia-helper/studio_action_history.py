"""Conflict-aware manual edit history; never rolls back an entire project."""
import copy
import json
import re
import time
import uuid

EXCLUDED = {'actionHistory', 'revision', 'updated', 'created', 'schemaVersion',
            'production', 'exports', 'warnings', 'renderHistory', 'audioHistory',
            'history', 'timingHistory', 'settingsHistory', 'setupHistory', 'previewRenders'}
SECRET = re.compile(r'key|secret|token|password|credential|authorization', re.I)


def snapshot(p):
    return copy.deepcopy({k: v for k, v in p.items() if k not in EXCLUDED and not SECRET.search(k)})


def contains_secret(value):
    if isinstance(value, dict):
        return any((SECRET.search(str(k)) and bool(v)) or contains_secret(v) for k, v in value.items())
    return isinstance(value, list) and any(contains_secret(v) for v in value)


def changes(before, after, path=(), guards=()):
    if before == after:
        return []
    if isinstance(before, dict) and isinstance(after, dict):
        if 'id' in before:
            guards = (*guards, {'path': list(path + ('id',)), 'value': before['id']})
        result = []
        for key in sorted(before.keys() | after.keys()):
            if key in EXCLUDED or SECRET.search(key):
                continue
            if key in before and key in after:
                result.extend(changes(before[key], after[key], path + (key,), guards))
            else:
                result.append({'path': list(path + (key,)), 'beforeExists': key in before,
                    'afterExists': key in after, 'before': copy.deepcopy(before.get(key)),
                    'after': copy.deepcopy(after.get(key)), 'guards': list(guards)})
        return result
    if isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
        # Changed ordering is one guarded operation, not edits to the wrong person.
        ids = lambda rows: [r.get('id') if isinstance(r, dict) else None for r in rows]
        if ids(before) == ids(after):
            return [change for i, (a, b) in enumerate(zip(before, after))
                    for change in changes(a, b, path + (i,), guards)]
    return [{'path': list(path), 'beforeExists': True, 'afterExists': True,
             'before': copy.deepcopy(before), 'after': copy.deepcopy(after), 'guards': list(guards)}]


def record(p, before, label):
    delta = changes(before, p)
    if not delta:
        return
    # Never put an unbounded story snapshot in every autosave. Large operations
    # remain visible with an honest explanation, without pretending Undo exists.
    reversible = not contains_secret(delta) and len(json.dumps(delta, ensure_ascii=False).encode('utf-8')) <= 512 * 1024
    rows = p.setdefault('actionHistory', [])
    rows.append({'id': uuid.uuid4().hex, 'time': time.time(), 'label': label[:120],
                 'changes': delta if reversible else [], 'undoable': reversible,
                 'reason': '' if reversible else 'Large or sensitive edit; use saved chapter/asset versions instead.'})
    # Bound document growth as well as row count.
    while len(rows) > 50 or len(json.dumps(rows).encode('utf-8')) > 2 * 1024 * 1024:
        rows.pop(0)


def read(p, path):
    value = p
    try:
        for key in path:
            value = value[key]
        return True, value
    except (KeyError, IndexError, TypeError):
        return False, None


def undo(p, identity):
    entry = next((x for x in p.get('actionHistory', []) if x['id'] == identity), None)
    if not entry or not entry.get('undoable') or entry.get('undone'):
        raise ValueError('This edit is not available for Undo.')
    for item in entry['changes']:
        for guard in item['guards']:
            if read(p, guard['path']) != (True, guard['value']):
                raise ValueError('Items were moved or replaced. Undo newer edits first.')
        if read(p, item['path']) != (item['afterExists'], item['after']):
            raise ValueError('Newer work changed this value. Undo newer edits first; nothing was overwritten.')
    previous_settings = copy.deepcopy(p['settings'])
    for item in reversed(entry['changes']):
        parent = p
        for key in item['path'][:-1]:
            parent = parent[key]
        key = item['path'][-1]
        if item['beforeExists']:
            parent[key] = copy.deepcopy(item['before'])
        else:
            del parent[key]
    # Undo never declares a previously rendered file current or revives old QC.
    from studio_qc import retain_review_settings
    retain_review_settings(p, previous_settings)
    p['renderStale'] = True
    p.setdefault('intro', {})['renderStale'] = True
    visual_fields = {'sourceText','cleanNarrationText','people','scenes','analysis','continuity','handoff'}
    affected = {x['path'][1] for x in entry['changes'] if len(x['path']) > 2 and
                x['path'][0] == 'chapters' and isinstance(x['path'][1], int) and x['path'][2] in visual_fields}
    global_change = any(x['path'][0] in ('characters','locations','styleReferences','continuity','storyMemory') or
                        x['path'] == ['chapters'] or (x['path'][0] == 'settings' and
                        (len(x['path']) == 1 or x['path'][1] in ('style','imageVisualConstraints','visualConstraints','director','continuityStrictness')))
                        for x in entry['changes'])
    for i, c in enumerate(p['chapters']):
        if global_change or any(len(x['path']) > 1 and x['path'][:2] == ['chapters',i] for x in entry['changes']):
            c['renderStale'] = True
        if global_change or i in affected:
            for scene in c.get('scenes', []):
                for shot in scene.get('shots', []):
                    if shot.get('imagePath'):
                        shot['qc'] = {'status': 'UNREVIEWED', 'reason': 'Manual edit undone; review current story requirements.'}
                        for key in ('qcOverride', 'qcAcceptance', 'qcDecision', 'qcReviewSignature'):
                            shot.pop(key, None)
    if global_change or any(x['path'][0] == 'intro' for x in entry['changes']):
        for shot in p.get('intro',{}).get('shots',[]):
            if shot.get('imagePath'):
                shot['qc'] = {'status':'UNREVIEWED','reason':'Manual edit undone; review current intro requirements.'}
                shot.pop('qcOverride',None)
    entry['undone'] = time.time()

