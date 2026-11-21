"""Shared immutable-input guards for local and cloud render publication."""
from functools import cache
from studio_data import digest, file_sha256, get_chapter, shot_chapter


def visual_spec_signature(project, shot, store, renderer=None, *, reference_digest=None):
    fields = ('prompt','negativePrompt','imageProvider','imageModel','workflow','characters',
              'camera','location','action','pose','expression','lighting','narrationSegment',
              'continuity','intentionalAppearanceChanges')
    settings = {k:v for k,v in shot.get('generationSettings',{}).items() if k != 'seed'}
    ids = {person['id'] for person in shot.get('characters',[])}
    chapter = shot_chapter(project,shot) or {}
    intro = project.get('intro', {})
    source = ({k:intro.get(k) for k in ('voiceText','sourceBrief','duration')}
              if any(s.get('id')==shot.get('id') for s in intro.get('shots',[]))
              else chapter.get('sourceText',''))
    people = [person for person in project['characters']+chapter.get('people',[]) if person['id'] in ids]
    references = [ref for person in people for ref in person.get('references',[])]
    references += [ref for location in project.get('locations',[]) if location['id']==shot.get('locationId')
                   for ref in location.get('references',[])]
    references += project.get('styleReferences',[])
    if shot.get('manual',{}).get('referenceImages'):
        references += shot.get('referenceImages',[])
    fingerprints=[]
    for ref in references:
        name=ref.get('path')
        if not name:continue
        if reference_digest is not None:
            # Readiness uses local bytes or the verified cloud digest without restoring a file.
            fingerprint=reference_digest(project['id'],name) or {'missing':True}
        else:
            path=store.asset(project['id'],name)
            if not path.is_file():fingerprint={'missing':True}
            elif callable(getattr(renderer,'asset_identity',None)):
                fingerprint=renderer.asset_identity(project['id'],name)
            else:fingerprint=file_sha256(path)
        fingerprints.append({'reference':ref,'bytes':fingerprint})
    return digest({'shot':{k:shot.get(k) for k in fields},'settings':settings,
        'source':source,'style':project['settings']['style'],
        'constraints':project['settings'].get('imageVisualConstraints',project['settings'].get('visualConstraints','')),
        'people':[{k:person.get(k) for k in ('id','description','permanentIdentity','references')}
                  for person in people],'referenceAssets':fingerprints})


def manual_motion_override(shot, video):
    # Omit the field for legacy jobs whose automatic motion is unaffected.
    if video.get('motionMode') == 'gentle' and shot.get('manual', {}).get('motion'):
        from studio_render import effective_motion
        automatic = dict(shot, manual={})
        if effective_motion(shot, video) != effective_motion(automatic, video):
            return {'manualMotion': True}
    return {}


def intro_render_inputs(intro, asset):
    fields = {k:intro.get(k) for k in ('enabled','duration','placement','title','subtitle',
              'showTitle','voiceText','visualPath','audioPath','motion','endOnNarration')}
    shots = []
    for shot in intro.get('shots', []):
        item = {k:shot.get(k) for k in ('start', 'end', 'motion')}
        item['image'] = asset(shot.get('imagePath'))
        if shot.get('motionSettings'):
            item['motionSettings'] = shot['motionSettings']
        shots.append(item)
    return dict(fields, visualBytes=asset(intro.get('visualPath')),
                audioBytes=asset(intro.get('audioPath')), shots=shots)


def render_submission_signature(project, store, renderer=None, chapter_id=None):
    """Publication guard; progress/review receipts cannot invalidate a render."""
    # Each publication check snapshots a file once. A later check reads it again.
    @cache
    def asset(name):
        if not name:return None
        path=store.asset(project['id'],name)
        if not path.is_file():return {'path':name,'missing':True}
        if callable(getattr(renderer,'asset_identity',None)):
            return renderer.asset_identity(project['id'],name)
        return file_sha256(path)
    def reference_digest(project_id, name):
        identity = asset(name)
        # The visual spec uses its own existing marker for a missing reference.
        return identity if isinstance(identity, str) else None

    chapters=[get_chapter(project,chapter_id)] if chapter_id else project['chapters']
    inputs=[]
    for ch in chapters:
        inputs.append({'id':ch['id'],'number':ch['number'],'source':ch.get('sourceText',''),
            'narration':ch.get('cleanNarrationText',''),
            'audio':{'path':ch.get('audio',{}).get('path'),
                     'duration':ch.get('audio',{}).get('duration'),
                     'bytes':asset(ch.get('audio',{}).get('path'))},
            'shots':[{'id':s['id'],'image':asset(s.get('imagePath')),
                'spec':visual_spec_signature(project,s,store,renderer,reference_digest=reference_digest),
                **manual_motion_override(s, project['settings']['video']),
                **{k:s.get(k) for k in ('start','end','motion','motionSettings','transition')}}
                for sc in ch.get('scenes',[]) for s in sc['shots']]})
    intro=project['intro']
    intro_input=None
    if intro['enabled'] and (chapter_id is None or intro['placement']=='every_chapter'):
        intro_input = intro_render_inputs(intro, asset)
    from studio_branding import identity as watermark_identity
    from studio_voice_provider import identity as voice_identity
    return digest({'chapters':inputs,'video':project['settings']['video'],'intro':intro_input,
        'watermark':watermark_identity(project,store),
        'engagement':{k:v for k,v in project['settings'].get('engagement',{}).items() if k not in ('outroAudioPath','outroAudioSignature')},
        'voiceSettings':{**voice_identity(project['settings']),**{k:project['settings'].get(k) for k in ('voice','speed','narrationDelivery','soundEffects','emphasisPhrases')}}})
