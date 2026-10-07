"""Luna-led restyling and durable opt-in multi-panel image production."""
import copy
import json
import time
from pathlib import Path

from studio_data import digest, get_chapter, get_shot, uid
from director_provider import obj, arr, STR, short_text
from image_provider import select_references, reference_prompt
from panel_batch import panel_prompt, split_panels, validate_groups
from studio_render import SUPPORTED_MOTIONS

def pending_panels(shots, folder):
    """Resume partial canvases without replacing an already saved shot."""
    return [s for s in shots if not (s.get('imagePath') and not s.get('generationStale')
        and s['status'] in ('COMPLETE','PASSED') and (folder / s['imagePath']).is_file())]

def cloud_cost(started, rate, previous=0, reserve_seconds=0):
    return previous + max(0, time.time()-started+reserve_seconds)*rate/3600

def restyle_story(service, pid):
    p = service.store.load(pid)
    for chapter in p['chapters']:
        if not chapter['sourceText'].strip():
            continue
        service.unload_models()
        service.before_audio()
        service.measured_stage(pid,'Narration',service.narration,p,chapter,chapter=chapter['number'])
        p = service.store.load(pid)
        chapter = get_chapter(p, chapter['id'])
        if not chapter['scenes']:
            raise ValueError('Analyze this chapter before restyling its existing shots.')
        timings = chapter['audio']['sentences']
        for scene in chapter['scenes']:
            for shot in scene['shots']:
                a,b = shot['startSentence'],shot['endSentence']
                if not 0 <= a <= b < len(timings):
                    raise ValueError('Narration sentence boundaries changed; reanalyze this chapter before restyling.')
                shot.update(start=timings[a]['start'],end=timings[b]['end'],duration=timings[b]['end']-timings[a]['start'])
            scene.update(start=scene['shots'][0]['start'],end=scene['shots'][-1]['end'])
        shots = [s for scene in chapter['scenes'] for s in scene['shots']]
        signature = digest({'audio':chapter['audio']['signature'],'style':p['settings']['style'],
                            'image':p['settings']['image'],'director':p['settings']['director'],
                            'shots':[(s['id'],s['narrationSegment'],s['characters']) for s in shots]})
        if chapter.get('restyleSignature') == signature:
            continue
        service.select_director(p)
        directing_started = time.monotonic()
        service.director.timing_callback = lambda stage,seconds,details: service.record_timing(pid,stage,seconds,details | {'chapter':chapter['number'],'detail':True})
        people = {c['id']:c for c in p['characters'] + chapter['people']}
        active = {cast['id'] for s in shots for cast in s['characters']}
        bible = [{k:people[id].get(k) for k in ('id','name','description','permanentIdentity','defaultAppearance')} for id in active if id in people]
        groups = []
        for offset in range(0,len(shots),20):
            current = shots[offset:offset+20]
            ids = [s['id'] for s in current]
            schema = obj({'shots':arr(obj({'id':{'type':'string','enum':ids},'action':short_text(300),
                'expression':short_text(120),'pose':short_text(180),'lighting':short_text(160),'motion':{'type':'string','enum':sorted(SUPPORTED_MOTIONS)},
                'transition':{'type':'string','enum':['cut','crossfade']},
                'camera':obj({'shot':short_text(60),'angle':short_text(60),'composition':short_text(220)}),
                'prompt':short_text(1000)})),
                'groups':arr(arr({'type':'string','enum':ids}) | {'minItems':1,'maxItems':4}),
                'reason':short_text(240)})
            result = service.director.call(
                'Fantasy restyle director. You make the shot, camera, pose, expression, lighting, motion and prompt decisions. '
                'Preserve these existing 120-style narration intervals and cast assignments; the user wants the same image density. '
                'Each shot depicts ONLY its own source narration, one simultaneous visible moment. Do not introduce future cast, spells, '
                'or new story events. Use coherent vibrant 2D fantasy manhwa illustration, dramatic rim light, clean drawn faces and painterly backgrounds, '
                'not photorealism. Depict violence through staging/reactions rather than graphic gore. For each supplied id return one shot direction '
                'and a concise natural-language Qwen image prompt. Include the stated current appearance and correct character count; references establish identity. '
                'Group every shot exactly once into groups of up to four for an economy 2x2 canvas. Favor four when distinct quadrants can clearly '
                'separate the moments without identity confusion. The app extracts each quadrant as a standalone landscape image; no page or comic borders. '
                'No shot IDs, captions, speech bubbles or rendered text in image prompts.',
                {'style':p['settings']['style'],'characterBible':bible,
                 'shots':[{k:s.get(k) for k in ('id','narrationSegment','characters','location','camera','action','continuity','intentionalAppearanceChanges')} for s in current]},
                schema,service.gate)
            changes = {s['id']:s for s in result['shots']}
            if len(result['shots']) != len(ids) or set(changes) != set(ids):
                raise ValueError('Luna omitted or duplicated a shot; existing plan remains saved.')
            groups.extend(validate_groups(result['groups'],current))
            for shot in current:
                updated = changes[shot['id']]
                for key,value in updated.items():
                    if key != 'id' and not shot.get('manual',{}).get(key):
                        shot[key] = value
                image = p['settings']['image']
                shot.update(imageProvider=image['provider'],imageModel=image['model'],workflow=image['workflow'],
                    generationSettings=copy.deepcopy(image) | {'seed':shot['generationSettings'].get('seed',42)},
                    negativePrompt='white border, gutters, captions, text, photography',generationStale=True,status='READY_FOR_IMAGES')
        chapter.update(economyGroups=validate_groups(groups,shots),restyleSignature=signature,renderStale=True,status='READY_FOR_IMAGES')
        chapter['timeline'] = [{'shotId':s['id'],'start':s['start'],'end':s['end'],'motion':s['motion'],'transition':s['transition']} for s in shots]
        chapter.setdefault('analysis',{}).update(restyleDirector='gpt-6-luna',restyleStyle=p['settings']['style'],economyGroupCount=len(groups))
        def save(latest):
            get_chapter(latest,chapter['id']).update(chapter)
            latest['renderStale'] = True
        service.store.mutate(pid,save)
        service.record_timing(pid,'AI direction & prompts',time.monotonic()-directing_started,{'chapter':chapter['number'],'status':'COMPLETE'})
        p = service.store.load(pid)
    if p['intro']['enabled'] and p['intro'].get('voiceText') and not p['intro'].get('audioPath'):
        service.measured_stage(pid,'Intro narration',service.intro_audio,p)

def generate_panel_story(service, pid, options):
    p = service.store.load(pid)
    if not p['settings'].get('economyPanels',False):
        raise ValueError('Enable economy storyboard canvases explicitly before using this operation.')
    provider = service.provider('comfyui')
    service.unload_models()
    service.before_image()
    service.providers['existing'].unload()
    started = options.get('cloudStartedAt')
    rate = float(options.get('gpuHourlyUSD',0))
    previous = float(options.get('previousGPUUSD',0))
    previous_seconds = float(options.get('previousGPUSeconds',0))
    cap = p['settings'].get('budget',{}).get('runpodUSD')
    if cap and (not started or not rate):
        raise ValueError('Cloud budget needs the actual worker start time and hourly rate before generation.')
    completed_batches = []
    for chapter in p['chapters']:
        shots = [s for scene in chapter['scenes'] for s in scene['shots']]
        groups = validate_groups(chapter.get('economyGroups',[]),shots)
        for index, ids in enumerate(groups):
            p = service.store.load(pid)
            chapter = get_chapter(p,chapter['id'])
            current = pending_panels([get_shot(p,chapter['id'],sid) for sid in ids], service.store.folder(pid))
            if not current:
                continue
            predicted = max(15, max(completed_batches[-3:],default=15))
            if cap and cloud_cost(started,rate,previous,predicted+25) > cap:
                raise RuntimeError('Cloud budget reached. Stop the rented GPU; all completed panels are saved. Finish remaining shots locally or explicitly raise the budget.')
            service.production_progress(pid,f"Chapter {chapter['number']} · economy canvas {index+1}/{len(groups)}",chapterId=chapter['id'],totalImages=len(shots))
            aggregate = copy.deepcopy(current[0])
            cast = {c['id']:c for shot in current for c in shot['characters']}
            aggregate['characters'] = list(cast.values())
            references, metadata = select_references(p,aggregate,service.store,provider)
            header = reference_prompt(p,aggregate,metadata)
            header += ' Identity references apply only to the named people in each quadrant. Each quadrant prompt controls its own clothing and injuries.'
            settings = copy.deepcopy(p['settings']['image']) | {'width':1344,'height':768,'steps':4,'seed':current[0]['generationSettings']['seed']}
            prompt = panel_prompt(current,header,p['settings']['style'])
            began = time.monotonic()
            result = provider.generateImage({'prompt':prompt,'negativePrompt':'','referenceImages':references,'settings':settings,
                'workflow':'qwen-reference-fast-4' if references else 'qwen-text-fast-4'},service.checkpoint)
            canvas = result.pop('pil')
            folder = service.store.folder(pid) / chapter['id']
            folder.mkdir(exist_ok=True)
            name = 'canvas-' + uid()
            canvas_path = folder / (name+'.png')
            canvas.save(canvas_path,'PNG')
            split = split_panels(canvas,len(current))
            shared = {'model':result['model'],'workflow':result['workflow'],'provider':'comfyui',
                'seed':result['seed'],'settings':result['settings'],'result':result,'prompt':prompt,'referenceImages':metadata,
                'batch':{'sourcePath':str(canvas_path.relative_to(service.store.folder(pid))).replace('\\','/'),
                    'shots':[s['id'] for s in current],'originalGroup':ids,'prompts':[{'id':s['id'],'prompt':s['prompt']} for s in current],
                    'qualityTradeoff':'Four shots share a 1344x768 canvas. Each extracted landscape is 640x360.'},'time':time.time()}
            saved = []
            for shot,tile in zip(current,split):
                path = folder / (shot['id']+'-'+name+'.png')
                tile.pop('pil').save(path,'PNG')
                image_metadata = copy.deepcopy(shared) | {'resolution':[640,360],'batch':shared['batch'] | tile}
                path.with_suffix('.json').write_text(json.dumps(image_metadata,indent=2),encoding='utf-8')
                saved.append((shot['id'],str(path.relative_to(service.store.folder(pid))).replace('\\','/'),image_metadata))
            def commit(latest):
                for sid,path,meta in saved:
                    s = get_shot(latest,chapter['id'],sid)
                    if s.get('imagePath'):
                        s.setdefault('history',[]).append({'imagePath':s['imagePath'],'metadata':s.get('imageMetadata',{}),'qc':s.get('qc',{})})
                    s.update(imagePath=path,imageMetadata=meta,referenceImages=metadata,status='COMPLETE',generationStale=False,generationError='',
                        qc={'status':'UNREVIEWED','pass':None,'issues':['Economy canvas: automatic pixel validation only; inspect composition and panel correspondence.']})
                latest['renderStale'] = True
                get_chapter(latest,chapter['id'])['renderStale'] = True
            service.store.mutate(pid,commit)
            if p['settings'].get('qcCheckLevel') in ('sampled','practical','strict'):
                from studio_qc import should_check, decision
                for sid,_,_ in saved:
                    latest = service.store.load(pid)
                    shot = get_shot(latest,chapter['id'],sid)
                    if should_check(latest,shot):
                        service.inspect_shot(latest,chapter['id'],sid)
                        reviewed = service.store.load(pid)
                        if decision(reviewed,get_shot(reviewed,chapter['id'],sid),service.store)['blocking']:
                            raise ValueError('Economy image needs your review. No replacement was purchased; accept it or request a repair.')
            seconds = time.monotonic()-began
            completed_batches.append(seconds)
            service.record_timing(pid,'Economy image canvas',seconds,{'chapter':chapter['number'],'shots':[s['id'] for s in current],'images':len(current),
                'generationSeconds':result['seconds'],'estimatedGPUUSD':seconds*rate/3600})
            if started:
                service.store.mutate(pid,lambda q:q.setdefault('production',{}).setdefault('costs',{}).update(
                    gpuWindowEstimatedUSD=cloud_cost(started,rate,previous),gpuHourlyUSD=rate,
                    gpuWindowSeconds=previous_seconds+time.time()-started,gpuStopped=False))
            if options.get('maxBatches') and len(completed_batches) >= int(options['maxBatches']):
                return
    service.store.mutate(pid,lambda q:q.setdefault('production',{}).update(status='READY_TO_RENDER',stage='All economy images saved'))
