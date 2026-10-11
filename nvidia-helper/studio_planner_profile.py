"""Read-only production setup for forecasts; never expose scripts or credentials."""
import copy
from studio_qc import check_level
from studio_performance import profile_for

FIELDS = ('style','generationMode','layoutMode','voice','speed','narrationDelivery',
          'soundEffects','automaticRepair','maxImageRetries','cloudImagesOnly')
GROUPS = {
    'video': ('width','height','fps','renderer','crf','imageFit','motionMode','zoomAmount','motionEasing'),
    'image': ('provider','model','workflow','width','height','steps','sampler','scheduler','guidance','referenceStrength'),
    'director': ('provider','model','executionMode','parallelism','reasoning','processingTier',
                 'factGroupSentences','visualPacking','compactCuts','factBeatsMode','sourceVisualOverlap','cadencePerMinute'),
}

def planner_profile(project, performance=None, config=None):
    from studio_overlap import shot_task_limit
    source = project['settings']
    settings = {k: copy.deepcopy(source[k]) for k in FIELDS if k in source}
    for group, fields in GROUPS.items():
        settings[group] = {k: copy.deepcopy(source.get(group, {})[k]) for k in fields if k in source.get(group, {})}
    settings['qcCheckLevel'] = check_level(source)
    settings['qcSampleEvery'] = source.get('qcSampleEvery', 5)
    intro = project.get('intro', {})
    chapters = [c for c in project['chapters'] if c.get('sourceText', '').strip()]
    shots = [s for c in chapters for sc in c.get('scenes', []) for s in sc.get('shots', [])]
    audio_seconds = sum(c.get('audio', {}).get('duration', 0) for c in chapters)
    estimates = {}
    if performance:
        # Exact profiles retain all failures and only teach from fresh successful
        # matching observations. No short-render cache is used to change throughput.
        profile = profile_for('Visual QC', project, config or {})
        result = performance.estimate(profile, {'kind':'images', 'value':1})
        estimates['visualQC'] = {k: result.get(k) for k in
            ('status','seconds','costUSD','samples','runs','retained','latest','rangeSeconds')}
        for stage, key, unit in (('AI directing','director','characters'),('Narration','narration','characters'),('Image pipeline','images','images')):
            result=performance.estimate(profile_for(stage,project,config or {}),{'kind':unit,'value':1})
            estimates[key]={k:result.get(k) for k in ('status','seconds','costUSD','samples','runs')}
    return {'version':1, 'project':project['id'], 'name':project['name'],
        'revision':project.get('revision', 0), 'settings':settings,
        'generationStrategy':project.get('generationStrategy', 'align'),
        'imageTasks':shot_task_limit(project,{}),
        'intro':{k:copy.deepcopy(intro[k]) for k in ('enabled','duration','placement') if k in intro},
        'introImages':len(intro.get('shots', [])), 'chapters':len(chapters),
        'plannedShots':len(shots), 'narrationSeconds':audio_seconds,
        'narrationComplete':bool(chapters) and all(c.get('audio', {}).get('duration', 0)>0 for c in chapters),
        'estimates':estimates, 'timedHistoryReady':all(estimates.get(k,{}).get('seconds') is not None for k in ('director','narration','images')),
        'scope':'Saved production settings, not unsaved editor choices. Read-only; no generation, paid calls or project changes.'}
