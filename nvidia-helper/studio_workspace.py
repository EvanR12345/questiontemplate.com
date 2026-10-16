"""Reversible workspace edits. No provider calls, rentals, or media deletion."""
import copy
import time
from studio_data import digest, get_chapter, get_shot

SCALARS = ('style', 'layoutMode', 'generationMode', 'voice', 'voiceEngine', 'voiceCompute',
           'voiceInstruction', 'qwenSpeaker', 'speed', 'narrationDelivery', 'soundEffects',
           'emphasisPhrases', 'continuityStrictness', 'appearanceHandling', 'maxImageRetries',
           'visionQC', 'qcSampleEvery', 'qcCheckLevel', 'automaticRepair', 'qcPolicy',
           'focusedPrompts', 'cloudImagesOnly', 'unattendedProduction', 'qcRepairBudgetMode')
GROUPS = {
    'director': ('provider', 'model', 'reasoning', 'parallelism', 'concisePrompts', 'serviceTier'),
    'image': ('provider', 'model', 'workflow', 'preset', 'width', 'height', 'steps', 'sampler',
              'scheduler', 'guidance', 'referenceStrength', 'fallbackEnabled'),
    'video': ('width', 'height', 'fps', 'motion', 'transition', 'transitionDuration', 'renderer',
              'crf', 'preset', 'codec', 'motionAmount', 'motionMode', 'imageFit'),
    'customLayout': ('sceneCount', 'minDuration', 'maxDuration', 'imagesPerMinute', 'minShots',
                     'maxShots', 'pacing', 'cameraVariety', 'closeUpFrequency',
                     'establishingFrequency', 'transition', 'movement', 'promptDetail'),
    'engagement': ('enabled', 'popupEnabled', 'popupText', 'dingEnabled', 'intervalMinMinutes',
                   'intervalMaxMinutes', 'minMinutes', 'maxMinutes', 'popupDuration', 'position',
                   'dingVolume', 'outroEnabled', 'outroText', 'outroDuration', 'exportDestination',
                   'patreonPopupEnabled', 'patreonOutroEnabled', 'splitMode', 'chaptersPerPart',
                   'splitEnabled', 'partMinutes', 'nextPartTeaser'),
}


def setup_snapshot(project):
    settings = project['settings']
    result = {k: copy.deepcopy(settings[k]) for k in SCALARS if k in settings}
    for group, keys in GROUPS.items():
        result[group] = {k: copy.deepcopy(settings.get(group, {})[k])
                         for k in keys if k in settings.get(group, {})}
    return {'settings': result, 'intro': {k: copy.deepcopy(project.get('intro', {}).get(k))
            for k in ('enabled', 'duration', 'placement', 'endOnNarration') if k in project.get('intro', {})}}


def image_versions(shot):
    return [item for item in shot.get('history', []) if isinstance(item, dict) and item.get('imagePath')]


def workspace_change(store, pid, body):
    """Revision checked inside ProjectStore's lock; only known operations are accepted."""
    def change(p):
        if type(body.get('revision')) is not int or body['revision'] != p['revision']:
            raise ValueError('This project changed. Refresh its saved state before applying this change.')
        operation = body.get('operation')
        if operation == 'undo':
            from studio_action_history import undo
            undo(p, body.get('actionId'))
            return
        from studio_action_history import snapshot
        before = snapshot(p)
        def remember():
            from studio_action_history import record
            record(p, before, operation.replace('-', ' '))
        if operation == 'save-setup':
            name = str(body.get('name', '')).strip()
            if not name or len(name) > 80:
                raise ValueError('Use a setup name between 1 and 80 characters.')
            setups = p.setdefault('savedSetups', [])
            setup = setup_snapshot(p)
            # A matching name updates that preset; its previous contents remain recoverable.
            old = next((s for s in setups if s['name'].casefold() == name.casefold()), None)
            if old:
                p.setdefault('setupHistory', []).append(copy.deepcopy(old))
                setups.remove(old)
            setups.append({'name': name, 'created': time.time(), **setup})
            remember()
            return
        if operation == 'apply-setup':
            source = store.load(body.get('sourceProject') or pid)
            preset = next((s for s in source.get('savedSetups', []) if s['name'] == body.get('name')), None)
            if preset is None:
                raise ValueError('This saved setup no longer exists.')
            # Re-sanitize stored/imported JSON; never import keys, spending caps or media paths.
            clean = setup_snapshot(preset)
            p.setdefault('settingsHistory', []).append({'settings': copy.deepcopy(p['settings']),
                                                       'reason': 'Before saved setup', 'time': time.time()})
            previous = copy.deepcopy(p['settings'])
            for key, value in clean['settings'].items():
                if key in GROUPS:
                    p['settings'].setdefault(key, {}).update(value)
                else:
                    p['settings'][key] = value
            p['intro'].update(clean['intro'])
            from studio_qc import retain_review_settings
            retain_review_settings(p, previous)
            for c in p['chapters']:
                c['renderStale'] = True
            p['renderStale'] = True
            remember()
            return
        c = get_chapter(p, body.get('chapter'))
        if operation in ('restore-image', 'attach-image'):
            shot = get_shot(p, c['id'], body['shot'])
            if operation == 'attach-image':
                from studio_storage import sha
                path = store.asset_local(pid, body.get('path', ''))
                if path.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp') or not path.is_file():
                    raise ValueError('Upload an available illustration first.')
                selected = {'imagePath': body['path'], 'metadata': {'source': 'manual-upload',
                            'imageSHA256': sha(path)}}
            else:
                versions = image_versions(shot)
                index = body.get('index')
                if type(index) is not int or not 0 <= index < len(versions):
                    raise ValueError('Image version no longer exists.')
                selected = copy.deepcopy(versions[index])
            # Validate path without fetching large media. Cloud-only assets remain cloud-only.
            store.asset_local(pid, selected['imagePath'])
            shot.setdefault('history', []).append({'imagePath': shot.get('imagePath'),
                'metadata': copy.deepcopy(shot.get('imageMetadata', {})), 'qc': copy.deepcopy(shot.get('qc', {})),
                'reason': 'Before choosing saved image', 'selected': False})
            shot['imagePath'] = selected['imagePath']
            shot['imageMetadata'] = copy.deepcopy(selected.get('metadata') or selected.get('imageMetadata') or {})
            shot['qc'] = {'status': 'UNREVIEWED', 'reason': 'Saved version selected; review current story requirements.'}
            for key in ('qcAcceptance', 'qcDecision', 'qcReviewSignature'):
                shot.pop(key, None)
            shot.setdefault('manual', {})['imagePath'] = True
            shot.update(status='COMPLETE', generationError='', generationStale=False)
        elif operation == 'restore-audio':
            versions = [a for a in c.get('audioHistory', []) if isinstance(a, dict) and a.get('path')]
            index = body.get('index')
            if type(index) is not int or not 0 <= index < len(versions):
                raise ValueError('Audio version no longer exists.')
            selected = copy.deepcopy(versions[index])
            if selected.get('textDigest') != digest(c['cleanNarrationText']):
                raise ValueError('This recording belongs to a different narration script. Keep it for comparison; regenerate the current script instead.')
            store.asset_local(pid, selected['path'])
            if c.get('audio', {}).get('path'):
                c.setdefault('audioHistory', []).append(copy.deepcopy(c['audio']))
            old_duration = c.get('audio', {}).get('duration', 0)
            c['audio'] = selected
            new_duration = selected.get('duration', 0)
            if old_duration and new_duration and old_duration != new_duration:
                c.setdefault('timingHistory', []).append({'scenes': copy.deepcopy(c.get('scenes', [])),
                    'timeline': copy.deepcopy(c.get('timeline', [])), 'audioPath': c.get('audioHistory', [{}])[-1].get('path'),
                    'reason': 'Before restored-audio timing scale', 'time': time.time()})
                ratio = new_duration / old_duration
                for scene in c.get('scenes', []):
                    for item in [scene, *scene.get('shots', [])]:
                        for key in ('start', 'end'):
                            if key in item: item[key] *= ratio
                for item in c.get('timeline', []):
                    for key in ('start', 'end'):
                        if key in item: item[key] *= ratio
                c['timingNeedsReview'] = True
                c['timingNote'] = 'Saved audio selected: scene boundaries scaled to its duration. Review sentence alignment before export.'
        else:
            raise ValueError('Unknown workspace operation.')
        c['renderStale'] = True
        p['renderStale'] = True
        remember()
    return store.mutate(pid, change)
