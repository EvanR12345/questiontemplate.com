"""Prepare one current-moment prompt before first dispatch; retain authored prompts."""
import copy,hashlib,json
from scene_repair import ROLE as REPAIR_ROLE,planning_context as repair_context

ROLE=REPAIR_ROLE.replace('Corrective scene planner','Current scene planner').replace(
 ' and confirmed defects','').replace('and confirmed defects','') + (
 ' This is the FIRST generation, not a repair. Do not describe errors or earlier attempts. '
 'Select exactly ONE simultaneous drawable moment from the current narration. Never show before/after copies of a person. '
 'Keep the saved focal cast and the narrated action. For multiple overlapping people, arrange distinct visible heads and torsos; '
 'a lap-seated person should have a visible head and torso offset from the seated person, with a traceable connection to their own legs. '
 'Do not merge two people to satisfy both descriptions. Genuine camera occlusion is allowed; do not add full-body views to a close-up. '
 'For a bound person, describe one physically coherent restrained pose, and omit an unsupported free-hand gesture. '
 'If both wrists or all limbs are tied and the source gives no attachment position, prefer empty hands held together with one visible continuous rope and knot enclosing both wrists. Preserve any explicitly saved behind-the-back, single-limb or fixed-attachment restraint. Keep hands outside a close-up when appropriate; do not widen it just to display restraints. '
 'For an internal concealed prop, show only the visible effect appropriate to this moment, not an invented externally strapped object. '
 'Write the complete image prompt, about100-180 words, with current action and named relationships first. '
 'Static trait notes are identity evidence only, never an instruction to repeat their past actions.')

def enabled(project,shot):
 return bool(project['settings'].get('firstPassScenePlanning',False)) and not any(
  shot.get('manual',{}).get(k) for k in ('prompt','referenceImages'))

def context(project,shot):
 if not enabled(project,shot):return None
 # Reuse the current-fact projection; old generated prose and old QC are excluded.
 fake={'pass':False,'issues':['Prepare the current moment.'],'findings':[{'issue':'Prepare the current moment.','severity':'major'}]}
 value=repair_context(project,shot,fake)
 if value is None:return None
 value.pop('confirmedDefects',None)
 value['originalPromptSHA256']=hashlib.sha256(str(shot.get('prompt','')).encode()).hexdigest()
 value['negativePrompt']=shot.get('negativePrompt','')
 value['model']=shot.get('imageModel')
 value['workflow']=shot.get('workflow')
 value['generationSettings']=copy.deepcopy(shot.get('generationSettings',{}))
 return value

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

def bind(prompt,value,model,reasoning):
 if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>2600:raise ValueError('Current-scene prompt is empty or exceeds its bounded size.')
 return {'version':1,'contextSHA256':fingerprint(value),'prompt':prompt.strip(),'model':model,'reasoning':reasoning}

def selected(project,shot):
 value=context(project,shot);plan=shot.get('firstPassScenePlan',{})
 if value is not None and plan.get('version')==1 and plan.get('contextSHA256')==fingerprint(value) and isinstance(plan.get('prompt'),str) and 0<len(plan['prompt'].strip())<=2600:return plan['prompt'].strip()
 return None

def required(project,shot):
 """Fail before GPU preparation rather than buying a rental for stale plans."""
 if not enabled(project,shot):return None
 prompt=selected(project,shot)
 if prompt is None:raise ValueError('Prepare the current-scene prompt before image rental; the saved original prompt is retained.')
 return prompt

def repair_source(project,shot):
 """Carry current-scene prose into repairs instead of restoring the old draft."""
 prompt=selected(project,shot)
 return shot | {'prompt':prompt} if prompt else shot
