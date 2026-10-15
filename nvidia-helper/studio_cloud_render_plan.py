"""Read-only resource planning; projections never count as verified exports."""
import math

MIB=2**20
GIB=2**30


def render_plan(project,source_bytes=0,config=None):
    config=config or {}
    chapters=[c for c in project['chapters'] if c.get('sourceText','').strip()]
    durations=[];shots=0
    for chapter in chapters:
        duration=chapter.get('audio',{}).get('duration')
        if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:
            raise ValueError('Generate saved narration for every chapter before planning its cloud export.')
        durations.append(duration)
        shots+=sum(len(s['shots']) for s in chapter.get('scenes',[]))
    if not durations:raise ValueError('A saved chapter is required.')
    intro=project['intro'];engagement=project['settings'].get('engagement',{})
    intro_seconds=float(intro.get('duration',15)) if intro.get('enabled') else 0
    count=len(chapters) if intro.get('placement')=='every_chapter' else 1
    duration=sum(durations)+intro_seconds*count
    # This is an upper planning allowance; the renderer uses the actual intro
    # audio duration and can end sooner when endOnNarration is enabled.
    destination=engagement.get('exportDestination','youtube')
    if engagement.get('outroEnabled') and destination!='patreon':
        duration+=max(float(engagement.get('outroDuration',15)),float(engagement.get('outroAudioDuration',0))+.5)
    if not math.isfinite(duration):
        raise ValueError('Invalid total export duration.')
    if type(source_bytes) is not int or source_bytes<0:raise ValueError('Invalid source byte count.')
    # Fixed-QP output size varies with content. Do not report this as a measured
    # bitrate, invoice, or hard upper bound. Headroom is checked during rendering.
    mbps=float(config.get('cloudRenderPlanningMbps',12))
    if not math.isfinite(mbps) or not 1<=mbps<=80:raise ValueError('Invalid cloud output planning bitrate.')
    rate=(mbps*1_000_000+128_000)/8
    output=math.ceil(duration*rate)
    longest=max(durations)+intro_seconds
    # Chapters, full visual, muxed story, finishing variant, optional split set,
    # and a chapter's temporary clips. Remote-only disposable scratch.
    copies=4+(1 if engagement.get('splitEnabled') else 0)
    # Inputs arrive lazily but are retained for exact chapter reuse. Account for
    # their full size until an eviction policy has actually been implemented.
    scratch=math.ceil((output*copies+longest*rate*2+source_bytes)*1.15+GIB)
    seconds_per_output=float(config.get('cloudRenderPlanningSecondsPerVideoSecond',.7))
    if not math.isfinite(seconds_per_output) or not .01<=seconds_per_output<=20:
        raise ValueError('Invalid cloud renderer planning speed.')
    return {'durationSeconds':duration,'chapterCount':len(chapters),'shotCount':shots,
        'sourceBytes':source_bytes,'estimatedOutputBytes':output,'scratchEstimateBytes':scratch,
        'recommendedDiskGB':max(25,math.ceil(scratch/1_000_000_000)),
        'suggestedExecutionSeconds':math.ceil(max(900,duration*seconds_per_output+600)),
        'planningMbps':mbps,'estimateKind':'conservative planning allowance, not measured throughput or a hard size guarantee',
        'laptopVideoBytes':0,'sourceDelivery':'lazy signed Google inputs',
        'longExportMeasured':False}


def timeout_for_bytes(size,remaining):
    """Bound a transfer by the actual remaining rental deadline, not 120s."""
    if not math.isfinite(remaining) or remaining<=0:raise ValueError('Cloud rental deadline reached.')
    return max(.1,min(remaining,max(120,size/(2*MIB)+60)))
