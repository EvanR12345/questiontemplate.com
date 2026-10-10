"""Visual-only scene/shot/camera planning; source facts are accepted separately."""
import copy
import math
from director_provider import obj, arr, short_text, INT, SHOT, CAMERA,validate_schema


ROLE = 'Visual storyboard director:'


def storyboard_schema(context):
    ids = [p['id'] for p in context['people']]
    shot = obj({
        'startSentence': INT,
        'characters': arr({'type':'string','enum':ids}) if ids else arr(short_text(64)) | {'maxItems':0},
        'action': short_text(220), 'expression':short_text(90), 'pose':short_text(110),
        'lighting':short_text(110), 'motion':SHOT['properties']['motion'],
        'transition':SHOT['properties']['transition'],
        'camera':obj({key: copy.deepcopy(CAMERA['properties'][key]) for key in ('shot','angle','composition')}),
    })
    shot['properties']['camera']['properties']['composition'] = short_text(200)
    # An unused source slot must be null, never a paid empty direction object.
    # Keep the compiler's stronger whitespace/content checks as a second guard.
    shot['properties']['action']['minLength']=1
    target=context.get('cadenceTarget',{}).get('approximateShots')
    maximum=min(len(context['sentences']),math.ceil(target*1.15)+1) if type(target) in (int,float) and target>0 else len(context['sentences'])
    minimum=min(maximum,max(1,math.ceil(target*.9))) if type(target) in (int,float) and target>0 else 1
    metadata={'purpose':short_text(160),'location':short_text(120),'mood':short_text(80),'pacingReason':short_text(160)}
    for field in metadata.values():field['minLength']=1
    if context.get('directorPayloadVersion') in (6,7,8):
        # Source-keyed slots make duplicate/out-of-order cuts impossible while
        # Luna still chooses every optional cut. Definitions avoid repeating
        # the complete camera/action schema for each source sentence.
        scene={'$ref':'#/$defs/scene'}
        fields={k:v for k,v in shot['properties'].items() if k!='startSentence'}
        direction=obj({**fields,'scene':{'anyOf':[scene,{'type':'null'}]}})
        required=set(context.get('requiredVisualChangeBoundaries',[])) | {0}
        if context.get('directorPayloadVersion') in (7,8):
            if any(type(n) is not int or not 0<=n<len(context['sentences']) for n in required):
                raise ValueError('Required storyboard cut escapes its source range.')
            if len(required)>maximum:raise ValueError('Required changes exceed selected cadence; needs review.')
            optional=[n for n in range(len(context['sentences'])) if n not in required]
            additional=obj({**copy.deepcopy(direction['properties']),
                'startSentence':{'type':'integer','enum':optional or [0]}})
            return obj({'openingScene':scene,
                'requiredCuts':obj({f'cut{n}':{'$ref':'#/$defs/direction'} for n in sorted(required)}),
                'additionalCuts':arr(additional)|{'minItems':max(0,minimum-len(required)),
                    'maxItems':max(0,maximum-len(required))}})|{'$defs':{'scene':obj(metadata),'direction':direction}}
        slots={f'cut{n}':({'$ref':'#/$defs/direction'} if n in required else
            {'anyOf':[{'$ref':'#/$defs/direction'},{'type':'null'}]}) for n in range(len(context['sentences']))}
        return obj({'openingScene':scene,'cuts':obj(slots)}) | {'$defs':{'scene':obj(metadata),'direction':direction}}
    first=obj({**shot['properties'],**metadata,'startSentence':{'type':'integer','enum':[0]},
               'newScene':{'type':'boolean','enum':[True]}})
    variants=[first]
    if len(context['sentences'])>1:
        bounds={'type':'integer','minimum':1,'maximum':len(context['sentences'])-1}
        variants.append(obj({**shot['properties'],**metadata,'startSentence':bounds,
                            'newScene':{'type':'boolean','enum':[True]}}))
        variants.append(obj({**shot['properties'],**{key:{'type':'string','enum':['']} for key in metadata},
                            'startSentence':bounds,'newScene':{'type':'boolean','enum':[False]}}))
    return obj({'shots':arr({'anyOf':variants}) | {'minItems':minimum,'maxItems':maximum}})


def repair_schema(context):
    items=storyboard_schema({**context,'directorPayloadVersion':2})['properties']['shots']['items']
    fields=copy.deepcopy((items['anyOf'][0] if 'anyOf' in items else items)['properties'])
    fields.pop('startSentence')
    for field in ('scene','newScene','purpose','location','mood','pacingReason'):fields.pop(field,None)
    fields['shotIndex']={'type':'integer','enum':context['repairIndices']}
    return obj({'repairs':arr(obj(fields)) | {'minItems':len(context['repairIndices']),'maxItems':len(context['repairIndices'])}})


def compile_storyboard(value, context):
    """The model chooses each cut; the application restores ends and indices."""
    count = len(context['sentences'])
    if context.get('directorPayloadVersion') in (7,8):
        validate_schema(value,storyboard_schema(context))
        alternatives={}
        cuts={f'cut{n}':None for n in range(count)}
        cuts.update(copy.deepcopy(value['requiredCuts']))
        for entry in value['additionalCuts']:
            raw=copy.deepcopy(entry);n=raw.pop('startSentence')
            if cuts[f'cut{n}'] is not None:
                if context['directorPayloadVersion']==7:raise ValueError('Duplicate source cut in chapter storyboard.')
                if not raw['action'].strip():raise ValueError('Empty alternative visual action.')
                alternatives.setdefault(n,[]).append(raw)
                continue
            cuts[f'cut{n}']=raw
        output=compile_storyboard({'openingScene':value['openingScene'],'cuts':cuts},
            {**context,'directorPayloadVersion':6})
        minimum=storyboard_schema(context)['properties']['additionalCuts']['minItems']+len(value['requiredCuts'])
        if len(output['detail']['shots'])<minimum:
            raise ValueError('Distinct compact cuts fall below selected cadence; duplicates are alternatives, never extra pictures.')
        for shot in output['detail']['shots']:
            if shot['startSentence'] in alternatives:
                shot['alternateDirections']=[dict(raw,qcStatus='UNREVIEWED') for raw in alternatives[shot['startSentence']]]
                shot['timingRepair']={'reason':'Duplicate source cuts retained as unreviewed alternatives; primary direction still receives full fidelity review.',
                    'alternativeCount':len(shot['alternateDirections'])}
        return output
    if context.get('directorPayloadVersion')==6 and 'cuts' in value:
        shots=[]
        for n in range(count):
            raw=value['cuts'][f'cut{n}']
            if raw is None:continue
            entry=copy.deepcopy(raw);scene=entry.pop('scene')
            if n==0:scene=value['openingScene']
            entry.update(startSentence=n,newScene=scene is not None,
                **(scene if scene is not None else {field:'' for field in ('purpose','location','mood','pacingReason')}))
            shots.append(entry)
        value={'shots':shots}
    if 'shots' in value:
        scenes=[]
        original_order=[shot['startSentence'] for shot in value['shots']]
        for shot in sorted(value['shots'],key=lambda item:item['startSentence']):
            if shot.get('newScene') or not scenes:
                scenes.append({key:shot[key] for key in ('purpose','location','mood','pacingReason')} | {'shots':[]})
            scenes[-1]['shots'].append({k:copy.deepcopy(v) for k,v in shot.items()
                if k not in ('newScene','purpose','location','mood','pacingReason')})
            if original_order!=sorted(original_order):
                scenes[-1]['shots'][-1]['timingRepair']={'reason':'Source cuts sorted in narration order.','originalCutOrder':original_order}
        value={'scenes':scenes}
    flat = [shot for scene in value['scenes'] for shot in scene['shots']]
    starts = [shot['startSentence'] for shot in flat]
    if not starts or min(starts) != 0 or any(type(n) is not int or not 0 <= n < count for n in starts):
        raise ValueError('Storyboard cuts must start at zero and use source sentence indices.')
    if any(a >= b for a,b in zip(starts,starts[1:])):
        # Ordering/index repair is deterministic. Never manufacture a new cut or
        # buy the whole plan again. Keep every duplicate direction as an editable
        # alternative, matching the existing classic overlap repair behavior.
        entries={};alternatives={}
        for si,scene in enumerate(value['scenes']):
            for shot in scene['shots']:
                n=shot['startSentence']
                if n in entries:alternatives.setdefault(n,[]).append(copy.deepcopy(shot))
                else:entries[n]=(si,scene,copy.deepcopy(shot))
        normalized=[];previous=None
        for n,(si,scene,shot) in sorted(entries.items()):
            if si!=previous:
                normalized.append({**{k:copy.deepcopy(v) for k,v in scene.items() if k!='shots'},'shots':[]});previous=si
            if n in alternatives:shot['alternateDirections']=alternatives[n]
            shot['timingRepair']={'reason':'Source cuts sorted; duplicate directions retained as alternatives.','originalCutOrder':starts}
            normalized[-1]['shots'].append(shot)
        value={'scenes':normalized}
        flat=[shot for scene in value['scenes'] for shot in scene['shots']]
        starts=[shot['startSentence'] for shot in flat]
    if context.get('directorPayloadVersion') not in (4,6) and len(flat)<storyboard_schema(context)['properties']['shots']['minItems']:
        raise ValueError('Distinct storyboard cuts fall below the selected cadence; no additional cuts were invented.')
    required = set(context.get('requiredVisualChangeBoundaries', []))
    if required - set(starts):
        raise ValueError('Storyboard omitted a required clothing, injury or object-change cut.')
    known = {p['id'] for p in context['people']}
    if any(set(shot['characters']) - known for shot in flat):
        raise ValueError('Storyboard contains an unknown character identity.')
    result = {'plan':{'scenes':[]}, 'detail':{'shots':[]}, 'cameras':[], 'prompts':[]}
    cursor = 0
    for si, scene in enumerate(value['scenes']):
        first = cursor
        for raw in scene['shots']:
            i = cursor; cursor += 1
            end = starts[i+1]-1 if i+1 < len(starts) else count-1
            shot = {k:copy.deepcopy(raw[k]) for k in SHOT['properties'] if k not in ('sceneIndex','endSentence')}
            shot.update(sceneIndex=si,endSentence=end)
            for field in ('timingRepair','alternateDirections'):
                if field in raw:shot[field]=copy.deepcopy(raw[field])
            result['detail']['shots'].append(shot)
            result['cameras'].append({'shotIndex':i, **copy.deepcopy(raw['camera'])})
            # No second AI description of the same composition. The model-aware
            # prompt compiler already preserves every structured visual choice.
            direction=' '.join([raw['action'],raw['camera']['composition'],raw['expression'],raw['pose'],raw['lighting']]).strip()
            if not raw['action'].strip() or not direction:
                raise ValueError('Storyboard has empty visual action. Use null for an unused cut.')
            result['prompts'].append({'shotIndex':i,'prompt':direction})
        end = result['detail']['shots'][-1]['endSentence']
        result['plan']['scenes'].append({key:scene[key] for key in ('purpose','location','mood','pacingReason')} |
            {'startSentence':starts[first], 'endSentence':end,'shotCount':cursor-first,
             'characters':list(dict.fromkeys(c for shot in scene['shots'] for c in shot['characters']))})
    return result


def plan_storyboard(provider, context, gate):
    scene_format=('The first shot MUST start a scene with newScene=true and its scene purpose/location/mood/pacingReason. '
                  'For later shots choose newScene=true when the story needs a new scene; otherwise newScene=false and those four scene fields are empty. ')
    shape='Return one flat shots array. '+scene_format
    if context.get('directorPayloadVersion')==6:
        shape=('Return openingScene metadata and cuts keyed cut0, cut1, etc. The NUMBER in each key is its source sentence index. '
            'At every chosen cut return its visual direction. Use null at sentences that continue an existing shot. '
            'cut0 and all requiredVisualChangeBoundaries must be non-null. Choose approximately cadenceTarget.approximateShots non-null cuts. '
            'Each direction has scene=null to continue its scene, or scene metadata for a new scene. cut0 uses openingScene metadata. '
            'Do not generate a picture at every sentence. Do not include an additional startSentence field. ')
    start_format=('The application derives ordered startSentence values from cut keys. '
                  if context.get('directorPayloadVersion')==6 else
                  'For each shot return only its startSentence. Starts must increase and begin at 0. '
                  'Each cut uses a DIFFERENT sentence index. Do not return two camera variations at the same startSentence. ')
    if context.get('directorPayloadVersion') in (7,8):
        shape=('Return openingScene metadata, requiredCuts and additionalCuts. '
            'Fill every fixed requiredCuts key with its direction. The number in cutN is the source sentence index. '
            'Never return null for a required cut. For other director-selected cuts return an additionalCuts array '
            'with startSentence and direction. Obey its minimum/maximum item count. '
            'Choose approximately cadenceTarget.approximateShots TOTAL shots, counting both collections. '
            'Never duplicate a cut or put required indices in additionalCuts. '
            'Each direction has scene=null to continue or scene metadata to start a scene. '
            'cut0 uses openingScene. Do not repeat metadata on continuing shots. ')
        start_format='The application sorts source cut indices and derives end times. '
    value = provider.call(ROLE +
        ' Work only on the accepted source facts and narration. Plan story-driven scenes, then individual visible shots, '
        'their cameras. The application compiles the complete model-aware image prompt from those AI decisions. '
        'Use the same visual cadence: favor 3–15 second shots; '
        'longer holds only for genuinely quiet beats. Do not reduce cuts to make planning faster. '
        'One shot depicts ONE simultaneous visible moment; separate successive actions and reveals. '
        +shape+
        'Continuing shots inherit their previous scene. No separate scene IDs or arrays need to be repeated. '
        'Follow cadenceTarget.approximateShots; the application validates the total shot count on BOTH sides to preserve the existing picture frequency. '
        +start_format+
        'Include EVERY supplied requiredVisualChangeBoundaries index. The application supplies each end from the next start and the final narration end; '
        'no sentence can be skipped. New scenes begin at their first shot. Use only supplied character IDs and source actions. '
        'Choose meaningful cameras/composition, never random angle rotations. Action/pose/expression/light should be drawable, '
        'brief and specific. '
        'Use roughly 6–14 words for action, 1–3 for expression, 2–5 for pose, 3–6 for light, '
        '6–12 for composition. Do not repeat the same facts in several fields. '
        'The application retains FULL canonical identity, current clothing/injury, references, source action and model-aware prompt. '
        'Never rewrite facts, remove clothing, infer ethnicity, add major people/props or render text. '
        'Follow the selected layout/style/custom targets. Keep continuity-sensitive objects correctly owned and placed. '
        'For dialogue, preserve the actual named or described speaker in that shot range; do not assign every line to the protagonist. '
        'A speaker attribution often follows the quotation. Read speakerHints and attribution cues; '
        'an image can depict the speaker while the following sentence identifies who spoke. '
        'For bystander reaction shots select the relevant accepted bystander/group ID, not the protagonist or attacking faction. '
        'Known main characters need not appear in every shot. '
        'Sustained restraints and posture in priorState/acceptedChanges persist until an explicit source change. '
        'A bound kneeling character cannot stand or sit freely merely because the camera changes. '
        'Current appearance applies at each source event, not permanently across the whole group.',
        context, storyboard_schema(context), gate)
    validate_schema(value,storyboard_schema(context))
    return compile_storyboard(value,context)
