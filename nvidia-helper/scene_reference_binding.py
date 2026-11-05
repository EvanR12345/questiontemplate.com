"""Keep each referenced identity beside its name in generated current-scene prose."""
import re

def ordered_references(project,shot,prompt,references,metadata):
 """Match automatic portrait order to first named appearance in the scene."""
 if not project['settings'].get('narrativeReferenceOrder',False) or any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return references,metadata
 from image_provider import shot_chapter
 chapter=shot_chapter(project,shot) or {'people':[]}
 people={p['id']:p for p in project.get('characters',[])+chapter.get('people',[])}
 def key(pair):
  index,ref=pair;name=people.get(ref.get('characterId'),{}).get('name','')
  match=re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',prompt,re.I) if name else None
  return (match.start() if match else len(prompt)+index,index)
 order=[i for i,_ in sorted(enumerate(metadata),key=key)]
 return [references[i] for i in order],[metadata[i] for i in order]

def compose(project,shot,prompt,metadata):
 if any(shot.get('manual',{}).get(k) for k in ('prompt','referenceImages')):return prompt
 from image_provider import reference_prompt,shot_chapter
 chapter=shot_chapter(project,shot) or {'people':[]}
 people={p['id']:p for p in project.get('characters',[])+chapter.get('people',[])}
 used=set()
 for slot,ref in enumerate(metadata,1):
  identity=ref.get('characterId');person=people.get(identity,{})
  name=person.get('name')
  if not name or identity in used:continue
  used.add(identity)
  pattern=re.compile(r'(?<!\w)'+re.escape(name)+r'(?!\w)')
  prompt=pattern.sub(lambda m:name+' (the person from image '+str(slot)+')',prompt,count=1)
 header=reference_prompt(project,shot,metadata,include_scene_facts=False)
 return (header+' '+prompt).strip() if header else prompt
