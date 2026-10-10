"""Ordered source facts, then bounded independent visual work.

No factual checkpoint is published before a complete chapter plan commits.
Frozen snapshots are request-local; existing chapter/handoff/edit guards own commit.
"""
import copy
import math
from studio_data import apply_changes, align_evidence, normalize_object_change, grounded_object_changes


def apply_source_changes(state,changes,chapter,scene):
    """Accumulate supported injury deltas in the app instead of asking Luna to.

    Other appearance/object fields keep their existing replacement semantics.
    Original per-event evidence remains in appearanceHistory, never rewritten.
    """
    result=copy.deepcopy(state)
    for event in changes:
        previous=result.get('characters',{}).get(event.get('characterId'),{}).get('injury','')
        history=len(result.get('appearanceHistory',[]))
        updated=apply_changes(result,[event],chapter,scene)
        value=event.get('to',{}).get('injury')
        if (event.get('type')=='injury' and isinstance(value,str) and value.strip()
                and len(updated.get('appearanceHistory',[]))>history
                and value.strip().casefold() not in ('none','healed','fully healed','uninjured','no injuries')):
            parts=[piece.strip() for piece in str(previous).split(';') if piece.strip()]
            if value.strip().casefold() not in {piece.casefold() for piece in parts}:parts.append(value.strip())
            updated['characters'][event['characterId']]['injury']='; '.join(parts)
        result=updated
    return result


def project_cadence(project,chapter):
    chosen=project['settings']['director'].get('cadencePerMinute')
    if type(chosen) in (int,float) and math.isfinite(chosen) and chosen>0:return chosen
    # Preserve the user's observed picture frequency on regeneration. New
    # projects use their selected layout target; no arbitrary 2–3 image preset.
    candidates=[chapter] if chapter.get('scenes') else project['chapters']
    count=duration=0
    for item in candidates:
        seconds=item.get('audio',{}).get('duration',0)
        shots=sum(len(scene['shots']) for scene in item.get('scenes',[]))
        if shots and type(seconds) in (int,float) and math.isfinite(seconds) and seconds>0:
            count+=shots;duration+=seconds
    if duration:return count*60/duration
    return project['settings'].get('customLayout',{}).get('imagesPerMinute')


def advance_memory(memory, analysis, chapter_number, group_index):
    return {
        **copy.deepcopy(memory),
        'previousChapterSummary':analysis['summary'],
        'majorEvents':(memory.get('majorEvents',[]) +
                       [{'chapter':chapter_number,'group':group_index,'summary':analysis['summary']}])[-12:],
        'objects':list(dict.fromkeys(memory.get('objects',[])+analysis['objects']))[-32:],
        'goals':analysis['goals'],
        'unresolved':list(dict.fromkeys(memory.get('unresolved',[])+analysis['unresolved']))[-24:],
        'locationsVisited':list(dict.fromkeys(memory.get('locationsVisited',[])+
                                              [l['name'] for l in analysis['locations']]))[-24:],
    }


def prepare_staged(service, project, chapter, groups, cast, state, memory, expected):
    incoming = copy.deepcopy(state); story_memory = copy.deepcopy(memory)
    canonical = [{'id':c['id'],'name':c['name'],'description':c['description'],
                  'identity':c['permanentIdentity']} for c in project['characters']]
    known = {person['id'] for person in cast}
    prepared = []; calls = []; owners = []; review_inputs = []
    for index, group in enumerate(groups):
        service.gate(f'Accepting source facts {index+1}/{len(groups)}')
        text = [{'index':i,'text':s['text'],'start':s['start'],'end':s['end']} for i,s in enumerate(group)]
        prior = {k:copy.deepcopy(v) for k,v in incoming.items() if k!='appearanceHistory'}
        context = {'sentences':text,'knownMainCharacters':canonical,'priorState':prior,
                   'storyMemory':copy.deepcopy(story_memory),'chapterCast':cast,
                   'layoutMode':project['settings']['layoutMode'],'customTargets':project['settings']['customLayout'],
                   'visualConstraints':project['settings'].get('visualConstraints',''),
                   'productionDirection':project['settings'].get('productionDirection','')}
        analysis = service.director.analyzeFacts(context,service.gate)
        if analysis['people']:
            raise ValueError('Staged analysis must use the accepted cast without creating new identities.')
        continuity = service.director.checkSourceFacts({'knownState':prior,'people':cast,
            'objects':analysis['objects'],'changes':analysis['changes'],'sentences':text,'shots':[]},service.gate)
        if 'correctedChanges' in continuity:analysis['changes']=copy.deepcopy(continuity['correctedChanges'])
        object_changes = align_evidence(grounded_object_changes(continuity['objectChanges'],analysis['objects'],cast),text)
        events = [normalize_object_change(e,analysis['objects'],cast) for e in analysis['changes']] + object_changes
        events=list({(e['characterId'],e['field'],e['value'],e['sentence']):e for e in events}.values())
        events.sort(key=lambda event:event['sentence'])
        for event in events + analysis.get('environmentChanges',[]):
            n = event.get('sentence')
            if (type(n) is not int or not 0<=n<len(text) or not event.get('reason')
                    or event['reason'].casefold() not in text[n]['text'].casefold()):
                raise ValueError('Staged facts require exact source sentence evidence.')
            if 'characterId' in event and event['characterId'] not in known:
                raise ValueError('Staged facts contain an unknown character identity.')
        for event in object_changes:
            if event['field']=='accessories':raise ValueError('Object facts cannot replace clothing accessories.')
        duration=group[-1]['end']-group[0]['start']
        cadence=project_cadence(project,chapter)
        visual = {**context,'analysis':analysis,'people':cast,'acceptedChanges':events,
                  'requiredVisualChangeBoundaries':sorted({e['sentence'] for e in events}),
                  'style':project['settings']['style'],'promptFormat':'natural_language'}
        if type(cadence) in (int,float) and cadence>0:
            visual['cadenceTarget']={'imagesPerMinute':cadence,'approximateShots':max(1,round(duration*cadence/60)),
                'instruction':'Keep this approximate selected cadence. Do not cut on every sentence. Split for genuinely distinct visible moments and required state changes; explain exceptions in pacingReason.'}
        prepared.append({'analysis':analysis,'continuity':continuity,'context':context})
        review_inputs.append(visual)
        # Required state changes are application-owned cut points. Independent
        # visual jobs cannot accidentally hide one inside an earlier-action shot.
        points={0,len(text),*visual['requiredVisualChangeBoundaries']}
        visual_size=0;visual_start=0
        for n,sentence in enumerate(text):
            # Bound visible output as well as input. A long group with many
            # cuts can exhaust the response limit and buy a second full call.
            # Factual groups stay chronological; these smaller visual slices
            # remain independent and retain the same pictures-per-minute.
            seconds=text[n]['end']-text[visual_start]['start']
            too_many_shots=type(cadence) in (int,float) and cadence>0 and seconds*cadence/60>8
            if n>visual_start and (n-visual_start>=24 or visual_size+len(sentence['text'])>6000 or too_many_shots):
                points.add(n);visual_start=n;visual_size=0
            visual_size+=len(sentence['text'])
        points=sorted(points)
        for start,end in zip(points,points[1:]):
            sliced=copy.deepcopy(visual)
            sliced['sentences']=[{**s,'index':n} for n,s in enumerate(text[start:end])]
            sliced['requiredVisualChangeBoundaries']=[0]
            sliced['priorState']=apply_source_changes(prior,[{'characterId':e['characterId'],'type':e['field'],
                'to':{e['field']:e['value']},'reason':e['reason']} for e in events if e['sentence']<start],chapter['number'],'facts-visual')
            for event in analysis.get('environmentChanges',[]):
                if event['sentence']<start:sliced['priorState'].setdefault('environment',{})[event['field']]=event['value']
            sliced['acceptedChanges']=[{**e,'sentence':e['sentence']-start} for e in events if start<=e['sentence']<end]
            sliced['analysis']=copy.deepcopy(analysis)
            for name in ('changes','environmentChanges','beats'):
                sliced['analysis'][name]=[{**e,'sentence':e['sentence']-start} for e in analysis.get(name,[]) if start<=e['sentence']<end]
            sliced['adjacentNarration']={'before':[s['text'] for s in text[max(0,start-2):start]],
                                       'after':[s['text'] for s in text[end:min(len(text),end+2)]]}
            sliced['adjacentNarrationInstruction']='Adjacent text is context only; all returned shot actions must belong to sentences, never before/after context.'
            if 'cadenceTarget' in sliced:
                sliced['cadenceTarget']['approximateShots']=max(1,round((text[end-1]['end']-text[start]['start'])*cadence/60))
            calls.append(('planStoryboard',sliced));owners.append((index,start))
        incoming = apply_source_changes(incoming,[{'characterId':e['characterId'],'type':e['field'],
            'to':{e['field']:e['value']},'reason':e['reason']} for e in events],chapter['number'],'facts-'+str(index))
        for event in analysis.get('environmentChanges',[]):
            incoming.setdefault('environment',{})[event['field']] = event['value']
        story_memory = advance_memory(story_memory,analysis,chapter['number'],index)
    service.gate('Directing independent accepted visual groups')
    outputs = service.director_calls(project,chapter['id'],calls,expected)
    if len(outputs)!=len(calls):raise ValueError('Incomplete staged visual groups; existing plan retained.')
    for item in prepared:item['storyboard']={'plan':{'scenes':[]},'detail':{'shots':[]},'cameras':[],'prompts':[]}
    for (owner,start),output in zip(owners,outputs):
        target=prepared[owner]['storyboard'];scene_offset=len(target['plan']['scenes']);shot_offset=len(target['detail']['shots'])
        for scene in output['plan']['scenes']:
            target['plan']['scenes'].append({**scene,'startSentence':scene['startSentence']+start,'endSentence':scene['endSentence']+start})
        for shot in output['detail']['shots']:
            target['detail']['shots'].append({**shot,'sceneIndex':shot['sceneIndex']+scene_offset,
                'startSentence':shot['startSentence']+start,'endSentence':shot['endSentence']+start})
        for field in ('cameras','prompts'):
            target[field].extend({**entry,'shotIndex':entry['shotIndex']+shot_offset} for entry in output[field])
    if project['settings']['director'].get('executionMode') == 'staged-review':
        reviews = service.director_calls(project,chapter['id'],[('checkStoryboard',{
            **payload,'shots':output['detail']['shots'],'cameras':output['cameras'],
            'visualDirection':output['prompts']}) for payload,output in zip(review_inputs,[item['storyboard'] for item in prepared])],expected)
        repair_calls=[];repair_owners=[]
        for index,(item,review) in enumerate(zip(prepared,reviews)):
            item['visualReview']=review
            if review['majorIssues']:
                indices=sorted({issue['shotIndex'] for issue in review['majorIssues']})
                repair_calls.append(('repairStoryboard',{**review_inputs[index],
                    'shots':item['storyboard']['detail']['shots'],'cameras':item['storyboard']['cameras'],
                    'issues':review['majorIssues'],'repairIndices':indices}))
                repair_owners.append(index)
        if repair_calls:
            replacements=service.director_calls(project,chapter['id'],repair_calls,expected)
            for index,replacement in zip(repair_owners,replacements):
                item=prepared[index];output=item['storyboard']
                item['repairHistory']={'initialReview':item['visualReview'],'repairs':replacement}
                for corrected in replacement['repairs']:
                    n=corrected['shotIndex'];shot=output['detail']['shots'][n]
                    shot.update({k:v for k,v in corrected.items() if k not in ('shotIndex','camera')})
                    output['cameras'][n]={'shotIndex':n,**corrected['camera']}
                    output['prompts'][n]={'shotIndex':n,'prompt':' '.join([corrected['action'],
                        corrected['camera']['composition'],corrected['expression'],corrected['pose'],corrected['lighting']]).strip()}
            final_reviews=service.director_calls(project,chapter['id'],[('checkStoryboard',{
                **review_inputs[index],'shots':prepared[index]['storyboard']['detail']['shots'],
                'cameras':prepared[index]['storyboard']['cameras'],'visualDirection':prepared[index]['storyboard']['prompts']})
                for index in repair_owners],expected)
            for index,review in zip(repair_owners,final_reviews):
                prepared[index]['visualReview']=review
                prepared[index]['repairHistory']['finalReview']=review
                if review['majorIssues']:
                    raise ValueError('Staged storyboard still has source-fidelity issues after one targeted repair. Existing chapter stays intact; review the saved responses before retrying.')
    return prepared
