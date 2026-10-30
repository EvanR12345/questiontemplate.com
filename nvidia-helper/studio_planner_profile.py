"""Read-only production setup for forecasts; never expose scripts or credentials."""
import copy,math
from studio_qc import check_level
from studio_performance import profile_for,signature

FIELDS = ('unattendedProduction','qcRepairBudgetMode','style','generationMode','layoutMode','voice','voiceEngine','voiceCompute','qwenSpeaker','voiceInstruction','voiceChapterBatchSize','speed','narrationDelivery',
          'soundEffects','automaticRepair','maxImageRetries','cloudImagesOnly','qcClothingContinuity')
GROUPS = {
    'video': ('width','height','fps','renderer','crf','imageFit','motionMode','zoomAmount','motionEasing'),
    'image': ('provider','model','workflow','width','height','steps','sampler','scheduler','guidance','referenceStrength'),
    'director': ('provider','model','executionMode','parallelism','reasoning','processingTier',
                 'factGroupSentences','visualPacking','compactCuts','factBeatsMode','sourceVisualOverlap','cadencePerMinute'),
}

def planner_profile(project, performance=None, config=None, rental=None, renderer_backend=None, assembly=None):
    from studio_overlap import shot_task_limit
    from studio_director_limits import adaptive_image_window,ADAPTIVE_IMAGE_ADMISSION
    from studio_image_workers import model_copies
    copies=model_copies(config or {}) if project['settings']['image'].get('provider')=='comfyui' else 1
    if project['settings'].get('unattendedProduction') is not False:
        from studio_automation import run_settings
        project = {**project, 'settings':run_settings(project['settings'])}
    source = project['settings']
    config=config or {}
    from studio_gpu_broker import gpu_configured
    dense_eligible=bool(source.get('unattendedProduction') and source['image'].get('provider')=='comfyui'
        and source['director'].get('provider')=='openai-luna' and not source.get('economyPanels') and gpu_configured(config))
    selected_strategy=project.get('generationStrategy') or project.get('production',{}).get('generationStrategy') or 'align'
    project={**project,'generationStrategy':selected_strategy}
    qc_profile=profile_for('Visual QC',project,config)
    # Policy and API concurrency select separate calibration cohorts. Keep all
    # other review compatibility inputs private behind the existing digest.
    qc_compatibility={key:value for key,value in qc_profile.items()
                      if key not in ('policy','reviewAPISlots','processingTier')}
    qc_history_identity=None
    candidate=adaptive_image_window(source,copies,copies*2 if copies>1 else 1)
    adaptive=selected_strategy!='dense' and config.get('experimentalAdaptiveImageAdmission') is True and candidate
    render_backend=renderer_backend if renderer_backend in ('cpu','native') else config.get('renderBackend','cpu')
    settings = {k: copy.deepcopy(source[k]) for k in FIELDS if k in source}
    for group, fields in GROUPS.items():
        settings[group] = {k: copy.deepcopy(source.get(group, {})[k]) for k in fields if k in source.get(group, {})}
    settings['qcCheckLevel'] = check_level(source)
    settings['qcSampleEvery'] = source.get('qcSampleEvery', 5)
    intro = project.get('intro', {})
    chapters = [c for c in project['chapters'] if c.get('sourceText', '').strip()]
    shots = [s for c in chapters for sc in c.get('scenes', []) for s in sc.get('shots', [])]
    from studio_service import StudioService
    def current_audio(chapter):
        audio=chapter.get('audio',{})
        duration=audio.get('duration')
        return bool(audio.get('path') and audio.get('signature')==StudioService.narration_signature(project,chapter)
                    and type(duration) in (int,float) and math.isfinite(duration) and duration>0)
    audio_seconds=sum(c['audio']['duration'] for c in chapters if current_audio(c))
    fresh_audio=bool(chapters) and all(current_audio(c) for c in chapters)
    estimates = {}
    director_observation=None
    gpu_quote=None
    verified_market=(isinstance(rental,dict) and config and rental.get('provider') in ('vast','lium')
        and rental['provider'] in config.get('gpuImageVerifiedProviders',[]) and rental.get('project')==project['id']
        and rental.get('workerGPU')=='NVIDIA GeForce RTX 5090')
    verified_runpod=(isinstance(rental,dict) and config and rental.get('podId')==config.get('runpodManagedPodId')
            and config.get('runpodRequiredGPUType')=='NVIDIA GeForce RTX 5090')
    if ((verified_market or verified_runpod)
            and rental.get('workersVerified') is True and rental.get('workerCopies')==2
            and type(rental.get('hourlyUSD')) in (int,float) and math.isfinite(rental['hourlyUSD']) and rental['hourlyUSD']>0
            and type(rental.get('started')) in (int,float) and math.isfinite(rental['started'])):
        gpu_quote={'hourlyUSD':rental['hourlyUSD'],'observedAt':rental['started'],
                   'provider':rental.get('provider','runpod'),'workingDiskIncluded':rental.get('workingDiskIncludedInQuote',False),
                   'scope':'Last observed quote for the configured, verified two-copy 5090; not a fresh availability/price guarantee.'}
    if performance:
        # Exact profiles retain all failures and only teach from fresh successful
        # matching observations. No short-render cache is used to change throughput.
        result = performance.estimate(qc_profile, {'kind':'images', 'value':1})
        estimates['visualQC'] = {k: result.get(k) for k in
            ('status','seconds','costUSD','samples','runs','retained','latest','rangeSeconds')}
        if type(result.get('seconds')) in (int,float) and math.isfinite(result['seconds']) and result['seconds']>0:
            qc_history_identity={'policy':qc_profile['policy'],'apiSlots':qc_profile['reviewAPISlots'],
                'processingTier':qc_profile['processingTier'],'compatibilityHash':signature(qc_compatibility)}
        for stage, key, unit in (('AI directing','director','characters'),('Narration','narration','characters'),('Image pipeline','images','images')):
            result=performance.estimate(profile_for(stage,project,config or {}),{'kind':unit,'value':1})
            estimates[key]={k:result.get(k) for k in ('status','seconds','costUSD','samples','runs')}
        chars=sum(len(StudioService.narration_text(project,c)) for c in chapters)
        row=estimates.get('director',{})
        if fresh_audio and chars>0 and row.get('seconds') is not None and row.get('costUSD') is not None:
            ratio=chars/audio_seconds
            director_observation={'secondsPerStorySecond':row['seconds']*ratio,
                'costPerTwoHours':row['costUSD']*ratio*7200,'samples':row['samples'],'runs':row['runs'],
                'status':row['status'],'scope':'Matching fresh production profile; character-normalized rate scaled by this project’s measured narration. Small-sample extrapolation, not a guarantee.'}
    return {'version':1, 'project':project['id'], 'name':project['name'],
        'revision':project.get('revision', 0), 'settings':settings,
        'qcCompatibilityHash':signature(qc_compatibility),
        'qcProcessingTier':source['director'].get('processingTier') or config.get('openaiServiceTier','default'),
        'qcHistoryIdentity':qc_history_identity,
        'generationStrategy':selected_strategy,
        'denseImagePhase':{'version':1,'supported':True,'generationTasks':5,
            'qcTaskSlots':min(4,source['director'].get('parallelism',1)),
            'descriptorLimit':8192,'maxRepairRounds':source.get('maxImageRetries',2),'allPlansFirst':True,
            'eligible':dense_eligible},
        'imageTasks':shot_task_limit(project,{}),
        'imageAdmission':{'policy':ADAPTIVE_IMAGE_ADMISSION if adaptive else 'fixed-v1',
                          'base':shot_task_limit(project,{}),
                          'maximumAfterVerification':8 if adaptive else shot_task_limit(project,{}),
                          'requiresVerifiedWorkers':adaptive,'requiresMemoryHeadroom':adaptive,
                          'benchmarkStatus':'no-reliable-eight-task-gain' if candidate else None,
                          'forecastSpeedupApplied':False},
        'imageExecution':{'backend':'comfyui-api' if source.get('image',{}).get('provider')=='comfyui' else source.get('image',{}).get('provider'),
                          'transport':('private-http-keepalive-v1' if (config or {}).get('comfyPersistentHTTP',True) is True
                                       else 'urllib-close-v1') if source.get('image',{}).get('provider')=='comfyui' else None,
                          'gpuLanes':copies,'modelCopies':copies,'requestSlots':copies*2 if copies>1 else 1,
                          'multipleModelCopiesVerified':False,
                          'computeQuote':gpu_quote,
                          'requiredGPU':(config or {}).get('runpodRequiredGPUType','')},
        'phaseOverlap':{'audioLookahead':(config or {}).get('studioAudioLookahead',True) is not False,
                        'chapterRender':(config or {}).get('studioRenderOverlap',True) is not False},
        'renderExecution':{'backend':render_backend,'location':'local-helper',
                           'frameProgressPolicy':'sampled-250ms-v1' if render_backend=='native' else None,
                           'fullAssemblyCapability':'direct-concat-original-audio-v1',
                           'fullAssemblyPolicy':('direct-concat-original-audio-v1' if (assembly or {}).get('eligibility')=='verified'
                                                 else 'legacy-two-pass-v1'),
                           'fullAssemblyEligibility':(assembly or {}).get('eligibility','unknown'),
                           'scope':'Currently selected helper renderer; project auto mode does not select another backend.'},
        'directorCalibrationProfile':profile_for('AI directing',project,config or {}),
        'directorObservation':director_observation,
        'intro':{k:copy.deepcopy(intro[k]) for k in ('enabled','duration','placement') if k in intro},
        'introImages':len(intro.get('shots', [])), 'chapters':len(chapters),
        'plannedShots':len(shots), 'narrationSeconds':audio_seconds,
        'narrationComplete':fresh_audio,
        'estimates':estimates, 'timedHistoryReady':all(estimates.get(k,{}).get('seconds') is not None for k in ('director','narration','images')),
        'scope':'Saved production settings with the selected full-video automation policy, not unsaved editor choices. Read-only; no generation, paid calls or project changes.'}
