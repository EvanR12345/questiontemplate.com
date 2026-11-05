"""Keep each referenced identity beside its name in generated current-scene prose."""
import re

def _mentions(project, shot, prompt, metadata):
 """Resolve full names before unambiguous short names in the current cast."""
 from image_provider import shot_chapter
 chapter=shot_chapter(project,shot) or {'people':[]}
 people={p['id']:p for p in project.get('characters',[])+chapter.get('people',[])}
 aliases={}
 for identity,person in people.items():
  name=str(person.get('name','')).strip()
  if not name:continue
  aliases.setdefault(name,set()).add(identity)
  first=name.split()[0]
  if len(first)>=4:aliases.setdefault(first,set()).add(identity)
 for alias,owners in aliases.items():
  for identity,person in people.items():
   if re.search(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',str(person.get('name','')),re.I):owners.add(identity)
 slots={}
 for index,ref in enumerate(metadata,1):
  if ref.get('characterId') in people:slots.setdefault(ref['characterId'],index)
 candidates=[]
 for alias,owners in aliases.items():
  if len(owners)!=1:continue
  identity=next(iter(owners))
  if identity not in slots:continue
  # First-name aliases must look like proper names, not lowercase common nouns.
  flags=re.I if alias==people[identity].get('name') and len(alias.split())>1 else 0
  pattern=re.compile(r'(?<![\w-])'+re.escape(alias)+r'(?![\w-])',flags)
  for match in pattern.finditer(prompt):candidates.append((match.start(),match.end(),identity))
 accepted=[]
 for start,end,identity in sorted(candidates,key=lambda x:x[1]-x[0],reverse=True):
  if not any(start<b and end>a for a,b,_ in accepted):accepted.append((start,end,identity))
 return sorted(accepted),people,slots

def ordered_references(project,shot,prompt,references,metadata):
 """Match automatic portrait order to first named appearance in the scene."""
 if not project['settings'].get('narrativeReferenceOrder',False) or any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return references,metadata
 mentions,_,_=_mentions(project,shot,prompt,metadata)
 def key(pair):
  index,ref=pair
  position=next((start for start,_,identity in mentions if identity==ref.get('characterId')),len(prompt)+index)
  return (position,index)
 order=[i for i,_ in sorted(enumerate(metadata),key=key)]
 return [references[i] for i in order],[metadata[i] for i in order]

def compose(project,shot,prompt,metadata):
 if any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return prompt
 from image_provider import reference_prompt
 mentions,people,slots=_mentions(project,shot,prompt,metadata)
 used=set();replacements=[]
 for start,end,identity in mentions:
  if identity in used:continue
  used.add(identity);replacements.append((start,end,people[identity]['name']+' (the person from image '+str(slots[identity])+')'))
 for start,end,value in reversed(replacements):prompt=prompt[:start]+value+prompt[end:]
 header=reference_prompt(project,shot,metadata,include_scene_facts=False)
 return (header+' '+prompt).strip() if header else prompt

def bind_automatic(project,shot,prompt,metadata):
 """Optional first-dispatch role binding; zero planning calls, same saved prose."""
 if not project.get('settings',{}).get('automaticReferenceBinding',False):return None
 if any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return None
 if len({r.get('characterId') for r in metadata if r.get('characterId')})<2:return None
 return compose(project,shot,prompt,metadata)
