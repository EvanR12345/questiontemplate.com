"""Bind text review findings to immutable narration cuts, including sparse QC."""
import copy
import re
from collections import Counter

class ReviewBindingError(ValueError):
    """Settled model output cites the wrong cut; never apply its repairs."""


def named_aliases(context):
    aliases={p['id']:set(filter(None,[p.get('name',''),p.get('name','').split(' ')[0]]))
             for p in context.get('people',[])}
    counts=Counter(a.casefold() for names in aliases.values() for a in names)
    return {pid:[a for a in names if counts[a.casefold()]==1] for pid,names in aliases.items()}


def source_speech(context,shot):
    """Verify only a whole quoted cut followed immediately by a named cue.

    This handles multi-sentence quotations without trusting model speaker hints.
    Pronouns, incomplete quotes and cuts containing multiple speakers are left
    to the ordinary review; no character or story state is inferred or edited.
    """
    aliases=named_aliases(context);source=context['sentences'];open_at=None;spans=[]
    for n,line in enumerate(source):
        text=line['text'].replace('“','"').replace('”','"')
        for match in re.finditer('"',text):
            if open_at is None:open_at=n;continue
            start=open_at;open_at=None
            # Attribution must immediately follow the closing quotation.
            tail=text[match.end():].strip()
            cue=n
            if not tail and n+1<len(source):tail=source[n+1]['text'];cue=n+1
            actors=[pid for pid,names in aliases.items() if any(re.search(
                r'\b'+re.escape(a)+r'\s+(?:questioned|asked|said|replied|answered|whispered|shouted|cried|spoke|chuckled)\b',tail,re.I)
                for a in names)]
            if len(actors)==1 and '"' not in tail.replace('“','"').replace('”','"'):
                spans.append((start,n,cue,actors[0]))
    start,end=shot['startSentence'],shot['endSentence']
    covering=[span for span in spans if span[0]<=start<=end<=span[1]]
    if len(covering)!=1:return []
    action=shot.get('action','')
    actors=[pid for pid,names in aliases.items() if any(re.search(
        r'\b'+re.escape(a)+r'\s+(?:says|asks|answers|replies|shouts|whispers|cries|speaks|pleads|confesses|threatens|explains|describes)\b',action,re.I)
        for a in names)]
    if len(actors)!=1:return []
    span=covering[0]
    return [{'sentence':start,'cueSentence':span[2],'sourceSpeaker':span[3],'depictedSpeaker':actors[0]}]


def explicit_dialogue(context,shot):
    """Only verify an exact quoted line with an explicit named source cue.

    Proposed AI hints alone never count as proof. Ambiguous aliases, pronouns,
    unquoted actions and edited/paraphrased lines remain ordinary Luna review.
    """
    aliases=named_aliases(context)
    action=shot.get('action','').replace('“','"').replace('”','"')
    quotes=re.findall(r'"([^"\n]+)"',action)
    if len(quotes)!=1:return []
    actors=[pid for pid,names in aliases.items() if any(re.search(r'\b'+re.escape(a)+r'\s+(?:says|asks|answers|replies|shouts|whispers|cries)\b',action,re.I) for a in names)]
    if len(actors)!=1:return []
    result=[];source=context['sentences']
    for hint in context.get('speakerHints',[]):
        n,cue,pid=hint.get('sentence'),hint.get('cueSentence'),hint.get('characterId')
        if any(type(i) is not int or not 0<=i<len(source) for i in (n,cue)) or pid not in aliases:continue
        if not shot['startSentence']<=n<=shot['endSentence']:continue
        text=source[n]['text'].replace('“','"').replace('”','"').strip().strip('"')
        if text!=quotes[0]:continue
        cue_text=source[cue]['text']
        if not any(re.search(r'\b'+re.escape(a)+r'\s+(?:questioned|asked|said|replied|answered|whispered|shouted|cried|spoke|chuckled)\b',cue_text,re.I) for a in aliases[pid]):continue
        result.append({'sentence':n,'cueSentence':cue,'sourceSpeaker':pid,'depictedSpeaker':actors[0]})
    return result


def proven_listener(context,shot):
    proofs=shot.get('verifiedDialogue',[])
    if not any(p['sourceSpeaker']==p['depictedSpeaker'] for p in proofs):return False
    names=[p.get('name','') for p in context.get('people',[])]
    aliases=set(filter(None,names+[n.split(' ')[0] for n in names]))
    # An exact two-person listening action, not any action that happens to
    # contain a valid quotation alongside unrelated invented events.
    pattern=r'(?:'+ '|'.join(re.escape(n) for n in sorted(aliases,key=len,reverse=True))+r')'
    action=shot.get('action','').replace('“','"').replace('”','"').strip()
    return bool(re.fullmatch(pattern+r'\s+(?:listens|reacts|watches)\s+as\s+'+pattern+r'\s+(?:says|asks|answers|replies|shouts|whispers|cries),?\s+"[^"\n]+"\.?',action,re.I))


def bound_context(context):
    source=context['sentences'];shots=context['shots']
    indices=context.get('reviewShotIndices',list(range(len(shots))))
    if not indices or len(indices)!=len(shots) or len(set(indices))!=len(indices) or any(type(i) is not int or i<0 for i in indices):
        raise ValueError('Invalid storyboard review indices.')
    result=copy.deepcopy(context);bindings={}
    for index,shot in zip(indices,result['shots']):
        if 'reviewShotIndices' in context and shot.get('shotIndex')!=index:
            raise ValueError('Sparse storyboard review indices do not match supplied shots.')
        start,end=shot.get('startSentence'),shot.get('endSentence')
        if any(type(n) is not int for n in (start,end)) or not 0<=start<=end<len(source):
            raise ValueError('Storyboard review narration range is invalid.')
        key=f's{start}-e{end}'
        if key in bindings:raise ValueError('Storyboard review contains duplicate narration cuts.')
        bindings[key]={'index':index,'start':start,'end':end}
        shot.update(shotIndex=index,shotKey=key,
                    ownNarration=[{'index':n,'text':source[n]['text']} for n in range(start,end+1)])
        shot['verifiedDialogue']=explicit_dialogue(context,shot)
        shot['verifiedSpeech']=source_speech(context,shot)
    result['reviewBindingVersion']=1
    return result,bindings


def validate_findings(context,value):
    """Also called before a provider response is published to its cache."""
    _,bindings=bound_context(context)
    seen=set()
    for issue in value['majorIssues']:
        binding=bindings.get(issue.get('shotKey'))
        sentence=issue.get('sentence')
        if binding is None or type(sentence) is not int or not binding['start']<=sentence<=binding['end']:
            raise ReviewBindingError('Storyboard finding cites narration outside its named shot; existing chapter retained.')
        marker=(issue['shotKey'],sentence,issue['issue'])
        if marker in seen:raise ValueError('Duplicate storyboard review finding.')
        seen.add(marker)
    return bindings


def restore_findings(context,value):
    bindings=validate_findings(context,value);result=copy.deepcopy(value)
    for issue in result['majorIssues']:
        issue['shotIndex']=bindings[issue['shotKey']]['index']
        issue['sourceQuote']=context['sentences'][issue['sentence']]['text']
    shots={s['shotKey']:s for s in context['shots']}
    kept=[]
    for issue in result['majorIssues']:
        proof=next((p for p in shots[issue['shotKey']].get('verifiedDialogue',[]) if p['sentence']==issue['sentence']),None)
        same_speaker=issue.get('category')=='speaker' and proof and proof['sourceSpeaker']==proof['depictedSpeaker']
        valid_reaction=issue.get('category') in ('speaker','action','timing') and proven_listener(context,shots[issue['shotKey']])
        if same_speaker or valid_reaction:
            result['advisories'].append('Dialogue finding rejected: the exact quotation and named source cue verify a coherent moment inside the whole shot range. '+issue['shotKey'])
        else:kept.append(issue)
    result['majorIssues']=kept
    for shot in context['shots']:
        for proof in shot.get('verifiedDialogue',[])+shot.get('verifiedSpeech',[]):
            if proof['sourceSpeaker']==proof['depictedSpeaker']:continue
            if any(i['shotKey']==shot['shotKey'] and i['sentence']==proof['sentence'] for i in kept):continue
            kept.append({'shotKey':shot['shotKey'],'shotIndex':shot['shotIndex'],'sentence':proof['sentence'],
                'category':'speaker','issue':'The exact quoted action assigns a different speaker from the explicit named source attribution.',
                'sourceQuote':context['sentences'][proof['sentence']]['text'],'verifiedBy':'explicit-source-cue'})
    result['coverage']={'scope':'repair-and-neighbors' if 'reviewShotIndices' in context else 'all-shots',
                        'shotIndices':[b['index'] for b in bindings.values()],
                        'bindingVersion':1}
    return result
