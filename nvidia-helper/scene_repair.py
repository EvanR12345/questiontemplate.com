"""Bounded full-scene repair plans, separate from original user/story prompts."""
import copy,hashlib,json,re
ROLE=('Corrective scene planner: write ONE complete positive image prompt for the CURRENT moment, '
 'using current narration, saved cast, identities, appearances and confirmed defects. This replaces the old baked image prompt. '
 'Put the essential current action and corrected body arrangement FIRST. Explicitly bind each action, restraint and essential prop to its named owner. '
 'Source cast notes may describe earlier or later actions; use them only for static physical traits. Never turn a past shooter into a living gunman in a current dead-body scene. '
 'The separate currentSceneRole field for a supporting individual is CURRENT staging, not historical identity notes: preserve its relationship, position and owned props unless the current narration explicitly changes them. '
 'Honor the stated restraint scope and side. When both hands or all limbs are tied, keep those hands restrained or genuinely behind the body. A single-wrist or ankle-only restraint must not constrain the other hand. '
 'When both wrists are restrained and no attachment position is specified, use empty hands held together inside one continuous rope with a visible shared knot, wherever the saved framing shows the hands. Preserve explicit behind-the-back or fixed-attachment positions. '
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

def framing_subjects(settings, shot, qc, *, project=None):
    """Return cast IDs for a confirmed garment conflict that can be restaged non-explicitly."""
    if not shot or not settings.get('automaticSceneRestaging', True):
        return []
    if any(shot.get('manual', {}).get(field) for field in ('prompt', 'camera', 'referenceImages')):
        return []
    from studio_qc import effective_issue_levels
    state = r'(?:stripped(?:\s+(?:of\s+(?:(?:all|his|her|their|the)\s+)*(?:clothing|clothes)|naked))?|(?:fully\s+)?(?:unclothed|undressed|naked))'
    eligible = [person['id'] for person in shot.get('characters', []) if person.get('id') and re.fullmatch(
        state, str(person.get('appearanceState', {}).get('outfit', '')).strip(' .'), re.I)]
    if not eligible:return []
    cast={s['id'] for s in shot['characters'] if s.get('id')}
    people=[]
    if project:
        chapter=next((c for c in project.get('chapters',[]) if c['id']==shot.get('chapterId')), {})
        people=[p for p in project.get('characters',[])+chapter.get('people',[]) if p.get('id') in cast and p.get('name')]
    found=set()
    for finding,level in zip(qc.get('findings',[]),effective_issue_levels(settings,qc)):
        if level!='major':continue
        text=finding.get('issue','')
        expected=re.search(r'\b(?:stripped|unclothed|undressed|naked)\b',text,re.I)
        seen=re.search(r'\b(?:wearing|wears|clothed|dressed|clad)\b',text,re.I)
        if not expected or not seen or expected.start()<seen.end():continue
        before=text[:seen.start()]
        if re.search(r'\b(?:not|never|no longer)\s+(?:visibly\s+)?$',before,re.I):continue
        named={p['id'] for p in people if any(re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',before,re.I)
               for name in (p['name'],p['name'].split()[0]))}
        between=text[seen.end():expected.start()]
        if any(p['id'] not in named and re.search(r'(?<!\w)'+re.escape(p['name'].split()[0])+r'(?!\w)',between,re.I)
               for p in people):continue
        owner=finding.get('subjectId')
        if owner:
            if owner in eligible:found.add(owner)
        elif len(named)==1:
            found.update(named.intersection(eligible))
        elif not named and len(eligible)==1:
            found.add(eligible[0])
    return [pid for pid in eligible if pid in found]

def planning_context(project,shot,qc):
 from scene_conditioning import scene_facts
 if shot.get('manual',{}).get('prompt'):return None
 defects=confirmed_defects(project['settings'],qc)
 if not defects:return None
 chapter=next((c for c in project.get('chapters',[]) if c['id']==shot.get('chapterId')), {})
 people={c['id']:c for c in project.get('characters',[])+chapter.get('people',[])}
 facts=scene_facts(project,shot)
 selections={s['id']:s for s in shot.get('characters',[])}
 def current_appearance(fact):
  from image_provider import visible_appearance
  selected=selections.get(fact['subjectId'],{})
  if selected.get('type')!='temporary':return fact['appearance']
  # Current supporting roles can name an owned prop absent from the focal
  # action. Reuse the visibility filter rather than restoring raw inventory.
  return visible_appearance(shot | {'action':str(shot.get('action',''))+' '+fact['description']},selected)
 context={'currentNarration':shot.get('narrationSegment',''),'currentAction':shot.get('action',''),
  'currentPose':shot.get('pose',''),'camera':copy.deepcopy(shot.get('camera',{})),
  'location':shot.get('location',''),'lighting':shot.get('lighting',''),
  'intendedPeople':[{'name':f['name'],'currentAppearance':current_appearance(f),
    'currentSceneRole':f['description'] if selections.get(f['subjectId'],{}).get('type')=='temporary' else '',
    'canonicalIdentity':people.get(f['subjectId'],{}).get('permanentIdentity',{}),
    'staticTraitNotesOnly':people.get(f['subjectId'],{}).get('description','')} for f in facts],
  'confirmedDefects':defects,'visualStyle':project['settings'].get('style',''),
  'visualConstraints':project['settings'].get('imageVisualConstraints',project['settings'].get('visualConstraints','')),
  'clothingContinuity':project['settings'].get('qcClothingContinuity','required'),
  'planningPolicy':{k:project['settings'].get('director',{}).get(k) for k in ('provider','model','reasoning','processingTier')}}
 subjects=framing_subjects(project['settings'],shot,qc,project=project)
 if subjects:
  context['framingGuidance']={
   'subjects':[people.get(s,{}).get('name') or s for s in subjects],
   'instruction':('Keep the scene non-explicit. Place the affected person near the camera, with the lower edge through '
                  'their upper torso, while the other focal people remain visible in the middle distance. '
                  'Name the visible evidence of the required action and any existing restraint. '
                  'Keep each supporting person and prop separately identifiable. Use another non-explicit staging '
                  'if this angle would conceal a required action, limb, movement, or relationship.')}
 return context

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
