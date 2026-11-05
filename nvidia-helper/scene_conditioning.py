"""Subject-bound scene facts and source-preserving identity-only reference regions.

Candidate stays opt-in until a fresh matched quality batch qualifies it. Derived
assets never replace canonical portraits or change hand-authored references.
"""
import base64,copy,errno,hashlib,io,json,os,re,time,uuid
from contextlib import contextmanager
from pathlib import Path
from PIL import Image

DEFAULT_ENABLED=False
VERSION=1

def enabled(project,shot):
 return bool(project.get('settings',{}).get('sceneConditioning',DEFAULT_ENABLED)) and not any(
  shot.get('manual',{}).get(k) for k in ('prompt','referenceImages'))

def _people(project,shot):
 chapter=next((c for c in project.get('chapters',[]) if c['id']==shot.get('chapterId')), {})
 return {c['id']:c for c in project.get('characters',[])+chapter.get('people',[])}

def needs_identity_only(project,shot,character_id):
 """Do not crop unchanged costumes, creatures, or angle-specific references."""
 person=_people(project,shot).get(character_id,{})
 selected=next((x for x in shot.get('characters',[]) if x['id']==character_id),{})
 baseline=person.get('defaultAppearance',{});current=selected.get('appearanceState',{})
 old=str(baseline.get('outfit') or baseline.get('clothing') or '').strip().casefold()
 new=str(current.get('outfit') or current.get('clothing') or '').strip().casefold()
 angle=str(shot.get('camera',{}).get('angle','')).lower()
 return bool(old and new and ' '.join(old.split())!=' '.join(new.split()) and 'profile' not in angle and 'behind' not in angle)

def scene_facts(project,shot):
 from image_provider import visible_appearance
 people=_people(project,shot);facts=[]
 for selected in shot.get('characters',[]):
  person=people.get(selected['id'],{});state=visible_appearance(shot,selected)
  facts.append({'subjectId':selected['id'],'name':person.get('name','Unnamed scene person'),'appearance':copy.deepcopy(state),
   'description':person.get('description','') if selected.get('type')=='temporary' else ''})
 return facts

def scene_contract(project,shot):
 facts=scene_facts(project,shot)
 if not facts:return ''
 text=['Subject-bound current scene facts. These override the reference portraits\' clothing and staging.']
 for f in facts:
  state='; '.join(str(k)+'='+str(v) for k,v in f['appearance'].items() if v)
  text.append(f['name']+': '+(f['description']+'; ' if f['description'] else '')+state+'.')
 bound=[f['name'] for f in facts if f['appearance'].get('restraint') and not re.search(
  r'\b(?:unbound|untied|released|unrestrained|free)\b|\b(?:no|without)\s+restraints?\b',str(f['appearance']['restraint']),re.I)]
 if bound:text.append('Only '+', '.join(bound)+' has specified restraints. Keep each restraint attached to its named subject; do not transfer it to other people.')
 if any(re.search(r'\b(?:stripped|naked|unclothed)\b',str(f['appearance'].get('outfit','')),re.I) for f in facts):
  text.append('Use non-explicit staging for the stated bare appearance: preserve visible shoulders and chest while keeping intimate lower anatomy outside the frame or naturally occluded. Preserve the required focal cast and action; do not invent a new covering outfit.')
 text.append('Intended focal cast: '+', '.join(f['name'] for f in facts)+'. Keep their separate bodies complete and connected wherever visible, with the saved camera and composition. Each held object belongs to its named owner.')
 return ' '.join(text)

def _write(path,value):
 temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.writing')
 with temporary.open('x',encoding='utf-8') as stream:
  json.dump(value,stream,ensure_ascii=False);stream.flush();os.fsync(stream.fileno())
 temporary.replace(path)

@contextmanager
def _region_lock(path,gate):
 handle=path.open('a+b')
 if handle.seek(0,os.SEEK_END)==0:handle.write(b'0');handle.flush()
 acquired=False;deadline=time.monotonic()+180
 try:
  while not acquired:
   gate('Preparing identity reference region')
   try:
    handle.seek(0)
    if os.name=='nt':
     import msvcrt
     msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
    else:
     import fcntl
     fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    acquired=True
   except OSError as error:
    if error.errno not in (errno.EACCES,errno.EAGAIN,errno.EDEADLK):raise
    if time.monotonic()>deadline:raise RuntimeError('Identity reference preparation is still owned by another request.')
    time.sleep(.05)
  yield
 finally:
  if acquired:
   handle.seek(0)
   if os.name=='nt':
    import msvcrt
    msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
   else:
    import fcntl
    fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
  handle.close()

def _paths(project,ref,store):
 source=store.asset(project['id'],ref['path']);raw=source.read_bytes();digest=hashlib.sha256(raw).hexdigest()
 folder=store.folder(project['id'])/'reference-conditioning';folder.mkdir(exist_ok=True)
 stem='v'+str(VERSION)+'-'+digest
 return source,raw,digest,folder/(stem+'.json'),folder/(stem+'.png')

def prepare_identity_regions(project,shot,store,director,gate,references=None):
 if any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return
 if references is None:
  people=_people(project,shot)
  references=[dict(p['references'][0],characterId=p['id']) for s in shot.get('characters',[])
   if (p:=people.get(s['id'])) and s.get('type')=='main' and p.get('references')]
 for ref in references:
  if not ref.get('characterId') or not needs_identity_only(project,shot,ref['characterId']):continue
  source,raw,digest,record,crop=_paths(project,ref,store)
  with _region_lock(record.with_suffix('.lock'),gate):
   if record.exists():
    previous=json.loads(record.read_text('utf-8'))
    if previous.get('status') in ('COMPLETE','UNCERTAIN'):continue
    raise RuntimeError('An earlier identity-region request needs reconciliation; no automatic replay.')
   _write(record,{'status':'SUBMITTING_UNKNOWN','sourceSHA256':digest,'version':VERSION})
   result=director.locateIdentityRegion({'coordinateSpace':'Integers 0..1000 relative to original image dimensions',
    '_images':['data:image/png;base64,'+base64.b64encode(raw).decode('ascii')]},gate)
   metadata={'status':'UNCERTAIN','sourceSHA256':digest,'version':VERSION,'review':result}
   with Image.open(io.BytesIO(raw)) as image:
    image.load();box=result.get('box',[])
    valid=result.get('usable') is True and result.get('confidence')=='high' and len(box)==4 and all(type(x) is int and 0<=x<=1000 for x in box)
    if valid:
     pixel=[round(box[0]*image.width/1000),round(box[1]*image.height/1000),round(box[2]*image.width/1000),round(box[3]*image.height/1000)]
     width,height=pixel[2]-pixel[0],pixel[3]-pixel[1]
     valid=width>=64 and height>=64 and width*height<=image.width*image.height*.8
    if valid:
     cut=image.crop(pixel);temporary=crop.with_name(crop.stem+'.'+uuid.uuid4().hex+'.png');cut.save(temporary,'PNG');temporary.replace(crop)
     metadata.update(status='COMPLETE',pixelBox=pixel,size=list(cut.size),cropPath=crop.relative_to(store.folder(project['id'])).as_posix(),cropSHA256=hashlib.sha256(crop.read_bytes()).hexdigest())
   _write(record,metadata)

def conditioned_reference(project,ref,store):
 source,raw,digest,record,crop=_paths(project,ref,store);meta=copy.deepcopy(ref)
 if record.exists():
  cached=json.loads(record.read_text('utf-8'))
  if cached.get('status')=='COMPLETE' and cached.get('sourceSHA256')==digest:
   data=crop.read_bytes()
   if hashlib.sha256(data).hexdigest()!=cached['cropSHA256']:raise ValueError('Identity crop changed; original references preserved.')
   meta['identityRegion']={k:cached[k] for k in ('version','sourceSHA256','pixelBox','size','cropPath','cropSHA256')}
   return 'data:image/png;base64,'+base64.b64encode(data).decode('ascii'),meta
 return 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii'),meta

def structural_repair(qc):
 if any(b.get('code') in ('BODY_DISCONNECTED','RESTRAINT_TRANSFER','FREE_BOUND_HAND') for b in qc.get('criticalStoryBlockers',[])):return True
 findings=qc.get('findings',[])
 for f in findings:
  if f.get('severity')!='major':continue
  text=str(f.get('issue',''))
  if re.search(r'\b(?:no|without)\s+(?:fused|duplicate|extra)\b',text,re.I):continue
  human=r'(?:(?:bearded|bloodied|shirtless|wounded|restrained|bound|injured|adult|male|female|young|old)[,\s]+){0,5}(?:man|woman|person|head|torso|body|character)\b'
  duplicate=re.search(r'\b(?:second|another|extra)[,\s]+(?:near[- ]identical|identical|duplicate[ds]?)[,\s]+'+human+r'|\b(?:appears?|is|looks?)\s+(?:(?:visibly|clearly)\s+)?duplicated\s*:\s*(?:a|an)\s+(?:separate|extra|second)\s+'+human,text,re.I)
  if duplicate and not re.search(r'\b(?:no|not|without)\s+(?:(?:any|a|an|the)\s+)?$',text[max(0,duplicate.start()-20):duplicate.start()],re.I):return True
  identity=re.search(r'\b(?:wrong|incorrect|swapped|mixed|exchanged)\s+(?:main[- ]character\s+)?(?:identit(?:y|ies)\b|faces?\b(?!\s+(?:angle|orientation|expression|pose|lighting|position|direction)s?\b))|\bidentit(?:y|ies)\b.{0,30}\b(?:swapped|mixed|exchanged|reversed)\b',text,re.I)
  if identity and not re.search(r'\b(?:no|not|without)\b',identity.group(),re.I) and not re.search(r'\b(?:no|not|without)\s+(?:(?:any|a|an|the)\s+)?$',text[max(0,identity.start()-20):identity.start()],re.I):return True
  if re.search(r'fus(?:ed|ion)|duplicat.{0,25}(?:bod|person|people|head|torso)|(?:extra|miniature|detached|disconnected|missing)\s+(?:head|torso|body)|(?:missing|absent).{0,25}(?:woman|women|person|people|character)|(?:woman|women|person|people|character).{0,25}(?:missing|absent)',text,re.I):return True
 return False

def critical_story_blockers(facts,observations):
 """Separate production rules over explicit observations; raw AI findings stay raw."""
 if sorted(x.get('subjectId','') for x in observations)!=sorted(x['subjectId'] for x in facts):
  raise ValueError('Visual subject observations omitted, duplicated or replaced a required identity.')
 by_id={x['subjectId']:x for x in observations};blockers=[]
 def restrained(f):
  value=str(f.get('appearance',{}).get('restraint',''))
  return bool(value and not re.search(r'\b(?:unbound|untied|released|unrestrained|free)\b|\b(?:no|without)\s+restraints?\b',value,re.I))
 has_prisoner=any(restrained(f) for f in facts)
 for fact in facts:
  obs=by_id[fact['subjectId']];name=fact['name'];state=fact.get('appearance',{})
  def add(code,message):blockers.append({'code':code,'subjectId':fact['subjectId'],'issue':message,'basis':'Explicit structured visual observation compared with the saved subject state'})
  if obs.get('body')=='severely_disconnected':add('BODY_DISCONNECTED',name+' has a visibly disconnected or fused body; restore one complete connected person.')
  if has_prisoner and not restrained(fact) and obs.get('restraint')=='visible':add('RESTRAINT_TRANSFER',name+' visibly has restraints that belong only to the planned prisoner; keep this subject free.')
  constraint=str(state.get('restraint',''))
  bilateral=bool(re.search(r'\b(?:wrists|hands|arms|limbs)\b',constraint,re.I))
  if re.search(r'\b(?:one|single|left|right)\b',constraint,re.I) and not re.search(r'\b(?:both|all|together)\b',constraint,re.I):bilateral=False
  if restrained(fact) and bilateral and obs.get('hands')=='visibly_free':add('FREE_BOUND_HAND',name+' visibly gestures with a free hand despite the specified hand/arm restraint; preserve the saved restraint.')
 return blockers
