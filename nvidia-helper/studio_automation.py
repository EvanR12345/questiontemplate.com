"""Deterministic unattended policy; never calls a model or raises spending caps."""
import copy
import math
import re


def run_settings(settings):
    result = copy.deepcopy(settings)
    result.update(unattendedProduction=True, appearanceHandling='Automatic')
    if result.get('qcCheckLevel') != 'off':
        result.update(automaticRepair=True, qcRepairBudgetMode='project-cap')
        result['maxImageRetries'] = max(1, int(result.get('maxImageRetries', 2)))
    return result


def project_cap_reason(settings):
    """Existing API ledger and managed-rental guards remain the dispatch owners."""
    requirements = []
    if settings.get('director', {}).get('provider') == 'openai-luna':
        requirements.append(('API', settings.get('budget', {}).get('openaiUSD')))
    if settings.get('image', {}).get('provider') == 'comfyui':
        requirements.append(('GPU', settings.get('cloudWindow', {}).get('gpuBudgetUSD')))
    for name, amount in requirements:
        if type(amount) not in (int, float) or not math.isfinite(amount) or amount <= 0:
            return f'Unattended repairs require a positive saved {name} spending cap.'
    total = settings.get('budget', {}).get('totalUSD')
    if total is not None:
        if type(total) not in (int, float) or not math.isfinite(total) or total <= 0:
            return 'Unattended repairs require a positive saved total spending cap.'
        if sum(amount for _, amount in requirements) > total + 1e-9:
            return 'Saved API and GPU caps exceed the project total; no automatic repair was started.'
    return None


def source_conflicts(project, chapter):
    from story_grounding import owner_conflicts
    changes = [event for group in chapter.get('analysis', {}).get('groups', [])
               for event in group.get('changes', [])]
    return owner_conflicts(changes, project['characters'] + chapter.get('people', []))


def _narration(shot):
    return re.sub(r'\s+', ' ', shot.get('narrationSegment', '')).strip().casefold()


def _locked_fields(item):
    manual = item.get('manual', {})
    if isinstance(manual, dict):
        return [field for field, enabled in manual.items() if enabled and field in item]
    if manual:
        return [field for field in item if field not in
                ('id', 'chapterId', 'sceneId', 'shots', 'history', 'qc', 'imageMetadata')]
    return []


def preserve_manual_plan(old_scenes, proposed_scenes):
    """Apply current AI fields while retaining every matched manual field/asset.

    Unmatched locks fail without discarding edits. They are never guessed onto an
    unrelated story beat merely to make an automatic run report success.
    """
    scenes = copy.deepcopy(proposed_scenes)
    by_narration = {}
    for scene in scenes:
        for shot in scene['shots']:
            key = _narration(shot)
            if key: by_narration.setdefault(key, []).append(shot)
    used = set()
    for old_scene in old_scenes:
        scene_fields = _locked_fields(old_scene)
        if scene_fields:
            candidates = [scene for scene in scenes
                          if abs(scene.get('start', 0) - old_scene.get('start', 0)) < .01
                          and abs(scene.get('end', 0) - old_scene.get('end', 0)) < .01]
            if len(candidates) != 1:
                raise ValueError('Automatic planning could not place a locked scene; original edits retained.')
            for field in scene_fields:
                if field != 'shots': candidates[0][field] = copy.deepcopy(old_scene[field])
            candidates[0]['manual'] = copy.deepcopy(old_scene.get('manual'))
        for old in old_scene['shots']:
            fields = _locked_fields(old)
            if not fields: continue
            candidates = [shot for shot in by_narration.get(_narration(old), []) if id(shot) not in used]
            if len(candidates) > 1:
                candidates = [shot for shot in candidates
                              if abs(shot.get('start', 0) - old.get('start', 0)) < .01
                              and abs(shot.get('end', 0) - old.get('end', 0)) < .01]
            if len(candidates) != 1:
                raise ValueError('Automatic planning could not place a locked shot; original edits retained.')
            shot = candidates[0]; used.add(id(shot))
            for field in fields: shot[field] = copy.deepcopy(old[field])
            shot['manual'] = copy.deepcopy(old.get('manual'))
            if old.get('imagePath'):
                for field in ('imagePath', 'imageMetadata', 'qc', 'qcOverride', 'history',
                              'sourceImagePath', 'retryHistory', 'retryCount'):
                    if field in old: shot[field] = copy.deepcopy(old[field])
                shot['generationStale'] = 'imagePath' not in fields
                shot['status'] = 'READY_FOR_IMAGES' if shot['generationStale'] else old.get('status', 'COMPLETE')
    return scenes
