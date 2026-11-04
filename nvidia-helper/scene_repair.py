"""Bounded full-scene repair plans, separate from original user/story prompts."""
import copy,hashlib,json
ROLE=('Corrective scene planner: write ONE complete positive image prompt for the CURRENT moment, '
 'using current narration, saved cast, identities, appearances and confirmed defects. This replaces the old baked image prompt. '
 'Put the essential current action and corrected body arrangement FIRST. Explicitly bind each action, restraint and essential prop to its named owner. '
 'Source cast notes may describe earlier or later actions; use them only for static physical traits. Never turn a past shooter into a living gunman in a current dead-body scene. '
 'Honor the stated restraint scope and side. When both hands or all limbs are tied, keep those hands restrained or genuinely behind the body. A single-wrist or ankle-only restraint must not constrain the other hand. '
 'Preserve important people, source facts, illustrated style and required props. A concealed internal object need not be externalized. '
 'Use a practical composition with separate connected bodies; a small framing adjustment may resolve an impossible overlap while preserving the narrated action. '
 'Respect clothingContinuity: Advisory tolerates non-plot-critical garments; Required enforces the saved outfit. Plot-critical uniforms and disguises remain essential. Keep non-explicit staging where appropriate. '
 'Use concise natural prose, about120-220 words; no repeated boilerplate, instruction tags, error descriptions, captions or collage. Do not invent story facts.')

def confirmed_defects(settings,qc):
 from studio_qc import effective_issue_levels
 levels=effective_issue_levels(settings,qc)
 issues=[f['issue'] for f,level in zip(qc.get('findings',[]),levels) if level=='major']
 issues += [b['issue'] for b in qc.get('criticalStoryBlockers',[])]
 return list(dict.fromkeys(issues))

def planning_context(project,shot,qc):
 from scene_conditioning import scene_facts
 if shot.get('manual',{}).get('prompt'):return None
 defects=confirmed_defects(project['settings'],qc)
 if not defects:return None
 chapter=next((c for c in project.get('chapters',[]) if c['id']==shot.get('chapterId')), {})
 people={c['id']:c for c in project.get('characters',[])+chapter.get('people',[])}
 facts=scene_facts(project,shot)
 return {'currentNarration':shot.get('narrationSegment',''),'currentAction':shot.get('action',''),
  'currentPose':shot.get('pose',''),'camera':copy.deepcopy(shot.get('camera',{})),
  'location':shot.get('location',''),'lighting':shot.get('lighting',''),
  'intendedPeople':[{'name':f['name'],'currentAppearance':f['appearance'],
    'canonicalIdentity':people.get(f['subjectId'],{}).get('permanentIdentity',{}),
    'staticTraitNotesOnly':people.get(f['subjectId'],{}).get('description','')} for f in facts],
  'confirmedDefects':defects,'visualStyle':project['settings'].get('style',''),
  'visualConstraints':project['settings'].get('imageVisualConstraints',project['settings'].get('visualConstraints','')),
  'clothingContinuity':project['settings'].get('qcClothingContinuity','required'),
  'planningPolicy':{k:project['settings'].get('director',{}).get(k) for k in ('provider','model','reasoning','processingTier')}}

def bind(prompt,image_sha,spec,context,model,reasoning):
 if not isinstance(prompt,str) or not prompt.strip() or len(prompt)>2600:raise ValueError('Corrective scene plan is empty or exceeds its bounded prompt size.')
 return {'prompt':prompt.strip(),'sourceImageSHA256':image_sha,'shotSpecSignature':spec,
  'contextSHA256':hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest(),
  'model':model,'reasoning':reasoning,'version':1}

def selected_plan(project,shot,spec,image_sha):
 if shot.get('manual',{}).get('prompt'):return None
 plan=shot.get('qc',{}).get('productionSceneRepair',{})
 if (plan.get('version')==1 and plan.get('shotSpecSignature')==spec
     and plan.get('sourceImageSHA256')==image_sha and isinstance(plan.get('prompt'),str)
     and 0<len(plan['prompt'].strip())<=2600):
  context=planning_context(project,shot,shot.get('qc',{}))
  if context is not None and plan.get('contextSHA256')==hashlib.sha256(json.dumps(context,sort_keys=True).encode()).hexdigest():return plan['prompt'].strip()
 return None
