"""Compact export input receipts, without story, credentials or spending grants."""
import copy

FIELDS = ('style','layoutMode','generationMode','voice','speed','narrationDelivery',
          'soundEffects','continuityStrictness','appearanceHandling','cloudImagesOnly')
NESTED = {
 'video':('width','height','fps','crf','imageFit','motionMode','zoomAmount','motionEasing'),
 'image':('provider','model','workflow','preset','width','height','steps','sampler',
          'scheduler','guidance','referenceStrength','denoisingStrength'),
 'director':('provider','model','reasoning','executionMode','parallelism','processingTier',
             'factGroupSentences','visualPacking','sourceVisualOverlap','factBeatsMode','compactCuts')}

def capture(project, chapter=None):
    source=project.get('settings',{})
    settings={k:copy.deepcopy(source[k]) for k in FIELDS if k in source}
    for group,keys in NESTED.items():
        settings[group]={k:copy.deepcopy(source.get(group,{})[k]) for k in keys if k in source.get(group,{})}
    chapters=[chapter] if chapter else project.get('chapters',[])
    images={};voices={}
    for ch in chapters:
        a=ch.get('audio',{})
        voice={k:a[k] for k in ('voice','speed','narrationDelivery','soundEffects') if k in a}
        if voice: voices[repr(sorted(voice.items()))]=voice
        for scene in ch.get('scenes',[]):
            for shot in scene.get('shots',[]):
                metadata=shot.get('imageMetadata',{})
                if not metadata:continue
                parameters=metadata.get('settings',shot.get('generationSettings',{}))
                spec={k:parameters[k] for k in ('steps','guidance','width','height') if k in parameters}
                for key in ('model','provider','sampler','scheduler'):
                    if key in metadata:spec[key]=metadata[key]
                if metadata.get('resolution'):
                    spec['width'],spec['height']=metadata['resolution']
                workflow=metadata.get('requestedWorkflow')
                if workflow:spec['workflow']=workflow
                marker=repr(sorted(spec.items()))
                if marker not in images:images[marker]={'settings':spec,'images':0}
                images[marker]['images']+=1
    if len(voices)==1:settings.update(next(iter(voices.values())))
    if len(images)==1:settings['image'].update(next(iter(images.values()))['settings'])
    intro=project.get('intro',{})
    return {'version':1,'source':'export-input-snapshot','settings':settings,
        'imageGroups':list(images.values()),'audioGroups':list(voices.values()),
        'mixedImageSettings':len(images)>1,'mixedVoices':len(voices)>1,
        'intro':{k:copy.deepcopy(intro[k]) for k in ('enabled','duration','placement','showTitle','endOnNarration') if k in intro},
        'visualChecks':source.get('qcCheckLevel','practical' if source.get('visionQC') else 'off')}
