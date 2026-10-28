"""Pure director/settings validation shared with render-only workers."""


from studio_qc import check_level


ADAPTIVE_IMAGE_ADMISSION = 'verified-dual-qc-window-v1'


def adaptive_image_window(settings, model_copies, request_slots):
    """Configured eligibility only; admission also requires live proof and RAM."""
    return (settings.get('image', {}).get('provider') == 'comfyui'
            and check_level(settings) == 'practical'
            and model_copies == 2 and request_slots == 4
            and type(settings.get('director', {}).get('parallelism')) is int
            and settings['director']['parallelism'] == 4)


def shot_task_limit(project, options):
    """Keep one image lane supplied while bounded API reviews are in flight.

    This is admission concurrency, not additional model copies or GPUs. A
    modest five-task ceiling bounds snapshots, API reservations and recovery.
    Explicit settings retain the caller's smaller limit.
    """
    settings = project['settings']
    parallelism = settings['director'].get('parallelism', 1)
    default = min(5, max(3, parallelism + 1)) if check_level(settings) != 'off' else 3
    limit = options.get('overlapShots', default)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 2 <= limit <= 5:
        raise ValueError('Choose two to five in-flight cloud shot tasks.')
    return limit


def validate_overlap(project, options):
    settings=project['settings']
    if settings['image']['provider']!='comfyui' or settings['director'].get('provider')!='openai-luna':
        raise ValueError('Cloud overlap requires ComfyUI images and the Luna director.')
    if settings.get('economyPanels'):
        raise ValueError('Panel generation does not support cloud overlap.')
    cap=settings.get('budget',{}).get('openaiUSD')
    if isinstance(cap,bool) or not isinstance(cap,(int,float)) or not 0<cap<float('inf'):
        raise ValueError('Set an explicit API spending cap before using cloud overlap.')
    fallback=settings['image'].get('fallback',{})
    if settings['image'].get('fallbackEnabled') and fallback.get('provider')!='comfyui':
        raise ValueError('Cloud overlap requires a cloud-only image fallback.')
    director_limit=settings['director'].get('parallelism',1)
    director_max=8 if settings['director'].get('executionMode','classic').startswith('staged') else 3
    if isinstance(director_limit,bool) or not isinstance(director_limit,int) or not 1<=director_limit<=director_max:
        raise ValueError('Choose bounded parallel director calls: up to three classic or eight staged.')
    shot_task_limit(project, options)
    for chapter in project['chapters']:
        for scene in chapter['scenes']:
            if any(shot['imageProvider']!='comfyui' for shot in scene['shots']):
                raise ValueError('Every shot must use ComfyUI before enabling cloud overlap.')


FACT_GROUP_LIMITS={48:9000,96:18000,128:24000,256:32000}


VISUAL_PACKING_LIMITS={'small':(12,6000,8),'balanced':(24,12000,12),'large':(40,18000,20)}


def fact_group_limits(director):
    count=director.get('factGroupSentences',48)
    if type(count) is not int or count not in FACT_GROUP_LIMITS:
        raise ValueError('Choose a bounded source-fact group of 48, 96, 128 or 256 sentences.')
    if count==256 and director.get('executionMode')!='staged-lean':
        raise ValueError('Wider source-fact groups require Lean Luna.')
    return count,FACT_GROUP_LIMITS[count]


def visual_group_limits(director):
    packing=director.get('visualPacking','small')
    if packing not in VISUAL_PACKING_LIMITS:
        raise ValueError('Choose small, balanced or large visual request groups.')
    if director.get('executionMode')!='staged-lean':
        if packing!='small':raise ValueError('Larger visual request groups require Lean Luna.')
        return 24,6000,8
    return VISUAL_PACKING_LIMITS[packing]


def validate_source_visual_overlap(director):
    value=director.get('sourceVisualOverlap',False)
    if type(value) is not bool:raise ValueError('Accepted source/visual overlap must be enabled or disabled.')
    if value and director.get('executionMode')!='staged-lean':
        raise ValueError('Accepted source/visual overlap requires Lean Luna.')
    return value


def fact_beats_mode(director):
    value=director.get('factBeatsMode','analyst')
    if value not in ('analyst','storyboard'):raise ValueError('Choose analyst or storyboard emotional-beat ownership.')
    if value=='storyboard' and director.get('executionMode')!='staged-lean':
        raise ValueError('Storyboard-owned beats require Lean Luna.')
    return value
