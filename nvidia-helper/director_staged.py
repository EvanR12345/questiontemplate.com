"""Ordered source facts, then bounded independent visual work.

No factual checkpoint is published before a complete chapter plan commits.
Frozen snapshots are request-local; existing chapter/handoff/edit guards own commit.
"""
import copy
import math
import re
from studio_data import apply_changes, align_evidence, normalize_object_change, grounded_object_changes
from director_pipeline import fact_beats_mode


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


def relevant_main_cast(known, chapter, passes, previous=None):
    """Request-local scope only; never edit the permanent character library."""
    detected=[person for item in passes if item.get('pass')=='casting-supervisor'
              for person in item.get('output',{}).get('people',[])]
    names={person.get('name','').casefold() for person in detected}
    ids={person.get('id') for person in detected}
    ids.update(c['id'] for scene in chapter.get('scenes',[]) for shot in scene.get('shots',[]) for c in shot['characters'])
    if previous and previous.get('scenes'):
        ids.update(c['id'] for shot in previous['scenes'][-1]['shots'] for c in shot['characters'])
    text=chapter['sourceText']
    selected=[person for person in known if person['id'] in ids or person['name'].casefold() in names or
        any(label and re.search(r'(?<!\w)'+re.escape(label)+r'(?!\w)',text,re.I)
            for label in [person['name'],*person.get('aliases',[])])]
    # Pronoun-only/ambiguous openings must not silently lose possible actors.
    return copy.deepcopy(selected or known)


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


def repair_review_context(payload, output, indices):
    """Initial review covers all shots; recheck changed directions and neighbors.

    Source indices, full narration and chronological facts are unchanged. Explicit
    shot indices prevent a sparse recheck from attaching issues to the wrong shot.
    """
    count=len(output['detail']['shots'])
    selected=sorted({n for index in indices for n in (index-1,index,index+1) if 0<=n<count})
    return {**payload,'reviewShotIndices':selected,'repairIndices':list(indices),
        'shots':[dict(output['detail']['shots'][n],shotIndex=n) for n in selected],
        'cameras':[output['cameras'][n] for n in selected]}


def prepare_staged(service, project, chapter, groups, cast, state, memory, expected):
    from director_pipeline import AcceptedVisualQueue,source_visual_overlap_enabled
    enabled=source_visual_overlap_enabled(service,project['settings']['director'])
    with AcceptedVisualQueue(service,project,chapter['id'],expected,enabled) as dispatch:
        return _prepare_staged(service,project,chapter,groups,cast,state,memory,expected,dispatch)


def _prepare_staged(service, project, chapter, groups, cast, state, memory, expected, dispatch):
    lean=project['settings']['director'].get('executionMode')=='staged-lean'
    visual_sentence_limit,visual_character_limit,visual_shot_limit=visual_group_limits(project['settings']['director'])
    incoming = copy.deepcopy(state); story_memory = copy.deepcopy(memory)
    canonical = [{'id':c['id'],'name':c['name'],'description':c['description'],
                  'identity':c['permanentIdentity']} for c in project['characters']]
    known = {person['id'] for person in cast}
    prepared = []; calls = []; owners = []; review_inputs = []
    for index, group in enumerate(groups):
        dispatch.check()
        group_call_start=len(calls)
        service.gate(f'Accepting source facts {index+1}/{len(groups)}')
        text = [{'index':i,'text':s['text'],'start':s['start'],'end':s['end']} for i,s in enumerate(group)]
        prior = {k:copy.deepcopy(v) for k,v in incoming.items() if k!='appearanceHistory'}
        if lean and 'characters' in prior:
            prior['characters']={k:v for k,v in prior['characters'].items() if k in known}
        context = {'sentences':text,'knownMainCharacters':canonical,'priorState':prior,
                   'storyMemory':copy.deepcopy(story_memory),'chapterCast':cast,
                   'layoutMode':project['settings']['layoutMode'],'customTargets':project['settings']['customLayout'],
                   'visualConstraints':project['settings'].get('visualConstraints',''),
                   'productionDirection':project['settings'].get('productionDirection','')}
        # Facts need the source, accepted identities and incoming state. Layout
        # instructions are for the visual planner, not repeated factual context.
        fact_context=({key:context[key] for key in ('sentences','chapterCast','priorState','storyMemory')}
                      if lean else context)
        # Narration clock values cannot change source facts. Keep sentence
        # indices/text but avoid rebuying identical facts when only voice timing
        # changes. Visual planning still receives its full audio timing/cadence.
        fact_text=[{'index':s['index'],'text':s['text']} for s in text] if lean else text
        if lean:fact_context['sentences']=fact_text
        if lean and fact_beats_mode(project['settings']['director'])=='storyboard':
            fact_context['visualPlanningOwnsBeats']=True
        analysis = service.director.analyzeFacts(fact_context,service.gate)
        if analysis['people']:
            raise ValueError('Staged analysis must use the accepted cast without creating new identities.')
        continuity = service.director.checkSourceFacts({'knownState':prior,'people':cast,
            'objects':analysis['objects'],'changes':analysis['changes'],'sentences':fact_text,'shots':[]},service.gate)
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
        if lean:
            visual.pop('knownMainCharacters');visual.pop('chapterCast')
            # Full facts remain in prepared/project data. Visual requests carry
            # identity once and relevant locations/objects/changes, not another
            # verbatim copy of every beat or summary of the future group.
            visual['analysis']={key:copy.deepcopy(analysis.get(key,[])) for key in ('locations','objects','environmentChanges')}
            # Unique source-keyed cut slots share scene/direction definitions.
            visual['directorPayloadVersion']=8 if project['settings']['director'].get('compactCuts',False) else 6
            # Reviews/repairs must receive the same proposed attributions as
            # visual requests. These are hints, never replacements for source.
            visual['speakerHints']=[{'sentence':e['sentence'],'characterId':e['characterId'],
                'cueSentence':e['cueSentence']} for e in analysis.get('dialogueSpeakers',[])
                if e['characterId'] in known and 0<=e['sentence']<len(text) and 0<=e['cueSentence']<len(text)]
            visual['speakerHintInstruction']='Proposed speaker hints, not canonical facts. Exact narration and its actual attribution take precedence.'
        if type(cadence) in (int,float) and cadence>0:
            visual['cadenceTarget']={'imagesPerMinute':cadence,'approximateShots':max(1,round(duration*cadence/60)),
                'instruction':'Keep this approximate selected cadence. Do not cut on every sentence. Split for genuinely distinct visible moments and required state changes; explain exceptions in pacingReason.'}
        prepared.append({'analysis':analysis,'continuity':continuity,'context':context})
        review_inputs.append(visual)
        # Required state changes are application-owned cut points. Independent
        # visual jobs cannot accidentally hide one inside an earlier-action shot.
        # A required SHOT cut is not a required API-request cut. Lean groups can
        # hold multiple changes; exact indices and per-shot state stay locked.
        points={0,len(text)} if lean else {0,len(text),*visual['requiredVisualChangeBoundaries']}
        visual_size=0;visual_start=0
        for n,sentence in enumerate(text):
            # Bound visible output as well as input. A long group with many
            # cuts can exhaust the response limit and buy a second full call.
            # Factual groups stay chronological; these smaller visual slices
            # remain independent and retain the same pictures-per-minute.
            seconds=text[n]['end']-text[visual_start]['start']
            too_many_shots=type(cadence) in (int,float) and cadence>0 and seconds*cadence/60>visual_shot_limit
            if n>visual_start and (n-visual_start>=visual_sentence_limit or visual_size+len(sentence['text'])>visual_character_limit or too_many_shots):
                points.add(n);visual_start=n;visual_size=0
            visual_size+=len(sentence['text'])
        points=sorted(points)
        for start,end in zip(points,points[1:]):
            sliced=copy.deepcopy(visual)
            sliced['sentences']=[{**s,'index':n} for n,s in enumerate(text[start:end])]
            sliced['requiredVisualChangeBoundaries']=sorted({0,*[n-start for n in visual['requiredVisualChangeBoundaries'] if start<=n<end]}) if lean else [0]
            sliced['priorState']=apply_source_changes(prior,[{'characterId':e['characterId'],'type':e['field'],
                'to':{e['field']:e['value']},'reason':e['reason']} for e in events if e['sentence']<start],chapter['number'],'facts-visual')
            # History belongs in persistent project data. A frozen current
            # state already contains its effects; do not resend the full log.
            if lean:sliced['priorState'].pop('appearanceHistory',None)
            for event in analysis.get('environmentChanges',[]):
                if event['sentence']<start:sliced['priorState'].setdefault('environment',{})[event['field']]=event['value']
            sliced['acceptedChanges']=[{**e,'sentence':e['sentence']-start} for e in events if start<=e['sentence']<end]
            if lean:
                sliced['speakerHints']=[{'sentence':e['sentence']-start,'characterId':e['characterId'],
                    'cue':text[e['cueSentence']]['text']} for e in analysis.get('dialogueSpeakers',[])
                    if start<=e['sentence']<end and e['characterId'] in known and 0<=e['cueSentence']<len(text)]
                sliced['speakerHintInstruction']='Proposed speaker hints, not canonical facts. Exact narration and its actual attribution take precedence.'
            sliced['analysis']=copy.deepcopy(visual['analysis'] if lean else analysis)
            for name in ('changes','environmentChanges','beats'):
                if not lean or name=='environmentChanges':
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
        dispatch.add(calls[group_call_start:])
    service.gate('Directing independent accepted visual groups')
    outputs = dispatch.finish(calls)
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
    if lean:
        # Group-local pacing may vary. Check accepted distinct cuts against the
        # whole selected cadence, never count duplicate camera alternatives as
        # extra pictures and never fabricate source cuts to meet a quota.
        cadence=project_cadence(project,chapter)
        if type(cadence) in (int,float) and cadence>0:
            duration=sum(group[-1]['end']-group[0]['start'] for group in groups)
            target=duration*cadence/60
            shots=sum(len(item['storyboard']['detail']['shots']) for item in prepared)
            if shots<math.ceil(target*.9) or shots>math.ceil(target*1.2)+len(groups):
                raise ValueError('Lean storyboard differs too much from the selected picture cadence; existing chapter retained.')
    if project['settings']['director'].get('executionMode') in ('staged-review','staged-lean'):
        reviews = service.director_calls(project,chapter['id'],[('checkStoryboard',{
            **payload,'shots':output['detail']['shots'],'cameras':output['cameras'],
            **({} if lean else {'visualDirection':output['prompts']})}) for payload,output in zip(review_inputs,[item['storyboard'] for item in prepared])],expected)
        repair_calls=[];repair_owners=[]
        for index,(item,review) in enumerate(zip(prepared,reviews)):
            item['visualReview']=review
            if review['majorIssues']:
                indices=sorted({issue['shotIndex'] for issue in review['majorIssues']})
                repair_context={**review_inputs[index],
                    'shots':item['storyboard']['detail']['shots'],'cameras':item['storyboard']['cameras'],
                    'issues':review['majorIssues'],'repairIndices':indices}
                if lean:
                    source=review_inputs[index]['sentences']
                    repair_context['repairSlots']=[{'shotIndex':n,
                        'startSentence':repair_context['shots'][n]['startSentence'],
                        'endSentence':repair_context['shots'][n]['endSentence'],
                        'ownNarration':source[repair_context['shots'][n]['startSentence']:repair_context['shots'][n]['endSentence']+1],
                        'originalDirection':repair_context['shots'][n],
                        'originalCamera':repair_context['cameras'][n]} for n in indices]
                    # Only flagged directions need to be rewritten. Full source
                    # and chronological state remain, but unrelated old shots
                    # are not another large competing prompt.
                    repair_context.pop('shots');repair_context.pop('cameras')
                repair_calls.append(('repairStoryboard',repair_context))
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
            final_reviews=service.director_calls(project,chapter['id'],[('checkStoryboard',
                repair_review_context(review_inputs[index],prepared[index]['storyboard'],
                    [r['shotIndex'] for r in prepared[index]['repairHistory']['repairs']['repairs']]) if lean else {
                **review_inputs[index],'shots':prepared[index]['storyboard']['detail']['shots'],
                'cameras':prepared[index]['storyboard']['cameras'],'visualDirection':prepared[index]['storyboard']['prompts']})
                for index in repair_owners],expected)
            for index,review in zip(repair_owners,final_reviews):
                prepared[index]['visualReview']=review
                prepared[index]['repairHistory']['finalReview']=review
                if review['majorIssues']:
                    findings='; '.join(f"draft group {index+1}, shot {issue['shotIndex']+1}: {issue['issue']}"
                                      for issue in review['majorIssues'][:3])
                    raise ValueError('Staged storyboard still has source-fidelity issues after one targeted repair. '
                        'Existing chapter stays intact. '+findings+
                        ' Review the draft/source before retrying; this is not a passed plan.')
    if lean and fact_beats_mode(project['settings']['director'])=='storyboard':
        for item in prepared:
            item['analysis']['beats']=[{'sentence':scene['startSentence'],
                'action':item['context']['sentences'][scene['startSentence']]['text'],
                'emotion':scene['mood'],'origin':'accepted-storyboard-scene'} for scene in item['storyboard']['plan']['scenes']]
    return prepared
