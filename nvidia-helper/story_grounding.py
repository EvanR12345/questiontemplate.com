"""Conservative source guards and location-scoped visual state.

These guards reject explicit contradictions, not ambiguous pronouns. The AI
still resolves actors; the application preserves the evidence and history.
"""
import copy
import re
import unicodedata

OWNERSHIP_RESOLUTION_VERSION = 2

GLOBAL_ENVIRONMENT = frozenset(('time', 'weather', 'season'))
INTERNAL_ENVIRONMENT = frozenset(('location', 'locationId', 'mood', 'gunfire', 'elapsedTime', 'condition'))


def labels(person):
    return [str(person.get('name', '')), *map(str, person.get('aliases', []))]


def owner_conflicts(events, people):
    """Find only explicit named/role possessors contradicting the assigned ID.

    'His hand' alone remains unresolved here. Transfers involving multiple
    named people need the director, and are not rejected just for two names.
    """
    by_id = {p['id']: p for p in people}
    # Labels identify a different accepted person only if they distinguish that
    # ID. Two cast members can share a name/alias; a generic role is evidence
    # for the role check below, never proof of one named individual.
    normalize=lambda value:' '.join(unicodedata.normalize('NFKC',value).casefold().split())
    label_ids={}
    for accepted in people:
        for label in labels(accepted):
            key=normalize(label)
            if key:label_ids.setdefault(key,set()).add(accepted['id'])
    conflicts = []
    for index, event in enumerate(events):
        if event.get('field') in ('outfit','hairStyle','injury','accessories','wetness','dirt','makeup','disguise','age','transformation','status','restraint','posture'):
            continue
        quote = str(event.get('reason', ''))
        value = str(event.get('value', ''))
        person = by_id.get(event.get('characterId'))
        if not person or not re.search(r'\b(?:held|holding|hand|hands|carr(?:y|ies|ied)|possess)\b', quote+' '+value, re.I):
            continue
        # Restrict role checks to explicit possessive/location phrases. A
        # woman attacking a named man is not evidence he cannot hold a prop.
        owner = re.search(r"\b(?:in|into)\s+(?:the\s+)?(?:hand|hands)\s+of\s+(?:the\s+)?(woman|man|girl|boy)\b|\b(woman|man|girl|boy)[’']s\s+hands?\b|\b(?:the\s+)?(woman|man|girl|boy)(?:\s+(?:standing|sitting)\s+[^,.!?;]{0,70}?)?\s+(?:was\s+|is\s+)?(?:holding|holds|held|carrying|carries|had|has)\b", quote, re.I)
        if owner:
            role = next(g.lower() for g in owner.groups() if g)
            description = ' '.join([*labels(person), str(person.get('description','')), str(person.get('gender',''))])
            # Explicit role in that person's accepted description can resolve
            # an unnamed reference. Never infer gender from a name or image.
            aliases = {'woman': ('woman','female'), 'man': ('man','male'), 'girl': ('girl',), 'boy': ('boy',)}[role]
            if not any(re.search(r'\b'+word+r'\b', description, re.I) for word in aliases):
                conflicts.append({'changeIndex':index, 'issue':f'Explicit {role} possession is assigned to {person.get("name",person["id"])}. Resolve the owner against accepted cast; do not guess from adjacent names.'})
                continue
        # A named subject holding an object cannot be assigned to a different
        # person. Limit to a direct subject-verb clause, not transfer clauses.
        object_words = re.findall(r'[a-z]+',str(event.get('field','')).lower())
        for other in people:
            if other['id'] == person['id']: continue
            matches = [re.search(r'(?<!\w)'+re.escape(label)+r'\s+(?:was\s+|is\s+)?(?:holding|holds|held|carrying|carries)\b([^.;!?]*)',quote,re.I)
                for label in labels(other) if label and label_ids.get(normalize(label))=={other['id']}
                and not re.fullmatch(r'(?:(?:the|a|an) )?(?:woman|man|girl|boy|female|male)',normalize(label))]
            if any(match and any(re.search(r'\b'+re.escape(w)+r'\b',re.split(r'\b(?:while|and|but|as)\b',match[1],flags=re.I)[0],re.I) for w in object_words) for match in matches):
                conflicts.append({'changeIndex':index, 'issue':f'The quoted holder is {other["name"]}, not {person["name"]}. Preserve the explicit object owner.'})
                break
    return conflicts


def require_owners(events, people):
    if owner_conflicts(events, people):
        raise ValueError('Object ownership contradicts the quoted story. Reanalyze the source facts before generating images; the existing plan is retained.')


def casting_gaps(text, people):
    """Target individually staged prop holders, not every background person.

    This coverage guard catches the observed omission without creating people
    through regex. The director supplies evidence-backed temporary profiles.
    """
    quotes = [s.strip() for s in re.split(r'(?<=[.!?])\s+',text)
              if re.search(r'\bwom[ae]n\b',s,re.I) and re.search(
                  r'\b(?:holding|holds|held|hands?|glass|plate|lap)\b',s,re.I)]
    if not quotes: return []
    def present(person):
        quote = str(person.get('evidence','')).strip().strip('\"\u201c\u201d').strip()
        return (bool(quote) and quote.casefold() in text.casefold()) or any(
            name and re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',text,re.I) for name in labels(person))
    individually_known = {p.get('id',p.get('name')) for p in people if present(p) and p.get('type') not in ('group','background') and
        re.search(r'\b(?:woman|female|girl)\b',' '.join([*labels(p),str(p.get('description','')),str(p.get('gender',''))]),re.I)}
    required = 2 if any(re.search(r'\b(?:other|another|second|two)\s+wom[ae]n\b',q,re.I) for q in quotes) else 1
    return quotes[:8] if len(individually_known)<required else []


def location_key(name, location_id=None):
    return str(location_id or ' '.join(str(name or '').casefold().split()))


def set_environment_location(state, name, location_id=None):
    """Move local facts out of the current room, without deleting history."""
    env = state.setdefault('environment', {})
    old = location_key(env.get('location'),env.get('locationId'))
    new = location_key(name,location_id)
    # IDs can appear after an earlier name-only director pass. Same named room
    # remains the same scope; do not reset its facts just to attach the ID.
    same_name = bool(name) and ' '.join(str(env.get('location','')).casefold().split()) == ' '.join(str(name).casefold().split())
    if old != new and not same_name:
        local = {k:copy.deepcopy(v) for k,v in env.items() if k not in GLOBAL_ENVIRONMENT and k not in INTERNAL_ENVIRONMENT}
        scopes = state.setdefault('environmentScopes', {})
        if old: scopes[old] = local
        stable = {k:copy.deepcopy(v) for k,v in env.items() if k in GLOBAL_ENVIRONMENT}
        # Unknown legacy room facts have no reliable new-room provenance.
        env.clear();env.update(stable);env.update(copy.deepcopy(scopes.get(new,{})))
    env.update(location=name,locationId=location_id)


def apply_environment_event(state, event, chapter=None, scene=None):
    field = event['field']
    if field in ('location', 'locationId'):
        current = state.get('environment',{})
        set_environment_location(state,event['value'] if field=='location' else current.get('location',''),
                                 event['value'] if field=='locationId' else None)
    else:
        state.setdefault('environment',{})[field] = event['value']
    state.setdefault('environmentHistory',[]).append(copy.deepcopy(event) | {
        'chapter':chapter,'scene':scene,'location':state['environment'].get('location'),
        'locationId':state['environment'].get('locationId')})


def visual_environment(shot):
    """Legacy local facts require current visual evidence; history stays saved."""
    env = shot.get('continuity',{})
    context = ' '.join(str(shot.get(k,'')) for k in ('action','narrationSegment','pose','lighting'))
    context += ' '+str(shot.get('camera',{}).get('composition',''))
    names_match = location_key(env.get('location')) == location_key(shot.get('location'))
    result = {}
    for key,value in env.items():
        if key in INTERNAL_ENVIRONMENT: continue
        if key in GLOBAL_ENVIRONMENT:
            result[key]=copy.deepcopy(value);continue
        # A location match in old snapshots is insufficient: they stored all
        # past local facts under each new room name. Require explicit framing.
        words = [w for w in re.findall(r'[a-z]+',str(key).lower()) if len(w)>2 and w not in ('the','and','state','status')]
        if names_match and words and all(re.search(r'\b'+re.escape(w)+r'\b',context,re.I) for w in words):
            result[key]=copy.deepcopy(value)
    return result
