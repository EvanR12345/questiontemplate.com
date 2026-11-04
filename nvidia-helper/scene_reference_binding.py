"""Keep each referenced identity beside its name in generated current-scene prose."""
import re

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
