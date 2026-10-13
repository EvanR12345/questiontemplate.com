"""Source-preserving cut opportunities when long sentences limit visual cadence."""
import copy,math,re


def visual_timing_units(sentences,cadence):
    units=copy.deepcopy(sentences)
    if type(cadence) not in (int,float) or not math.isfinite(cadence) or cadence<=0 or not units:return units
    duration=sum(max(0,s['end']-s['start']) for s in units)
    target=duration*cadence/60
    if len(units)>=max(1,math.ceil(target*.9)):return units
    # Supply options, not images. The director still chooses meaningful cuts;
    # no new action, sentence or event is manufactured to reach the target.
    wanted=max(len(units),math.ceil(target*1.2)+1) if target>=1 else len(units)
    while len(units)<wanted:
        candidates=[]
        for index,item in enumerate(units):
            words=list(re.finditer(r'\S+',item['text']))
            if len(words)>1 and item['end']>item['start']:
                candidates.append((item['end']-item['start'],index,words))
        if not candidates:break
        _,index,words=max(candidates,key=lambda row:row[0]);item=units[index];text=item['text']
        middle=len(text)/2
        choices=words[1:]
        # Prefer a nearby clause boundary while retaining every exact word.
        preferred=[w for w in choices if text[:w.start()].rstrip().endswith((',', ';', ':')) or w.group().lower() in ('and','but','while','as','when','before','after','because')]
        close=[w for w in preferred if abs(w.start()-middle)<=len(text)*.25]
        cut=min(close or choices,key=lambda w:abs(w.start()-middle)).start()
        left=text[:cut].rstrip();right=text[cut:].lstrip()
        if not left or not right:break
        boundary=item['start']+(item['end']-item['start'])*cut/len(text)
        source_index=item.get('originalSentenceIndex',item.get('index',index))
        common={'originalSentenceIndex':source_index,'_sourceText':item.get('_sourceText',text),'timingSource':'Estimated intra-sentence cut within measured narration bounds; word alignment unavailable.'}
        units[index:index+1]=[{**item,**common,'text':left,'end':boundary},{**item,**common,'text':right,'start':boundary}]
    for index,item in enumerate(units):item['index']=index
    return units


def rebase_source_indices(value,units):
    """Accepted facts stay unchanged; map their source index to its first visual slot."""
    result=copy.deepcopy(value);mapping={}
    for index,unit in enumerate(units):mapping.setdefault(unit.get('originalSentenceIndex',unit['index']),index)
    def visit(item):
        if isinstance(item,dict):
            for key,child in list(item.items()):
                if key in ('sentence','cueSentence') and type(child) is int and child in mapping:item[key]=mapping[child]
                else:visit(child)
        elif isinstance(item,list):
            for child in item:visit(child)
    visit(result);return result
