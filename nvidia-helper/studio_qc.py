"""Production decisions separate from preserved AI quality-review evidence."""
import copy
import hashlib
import math
import re
import time
from studio_data import digest, get_chapter, get_shot

POLICIES = ('practical', 'strict')
CHECK_LEVELS = ('off', 'sampled', 'practical', 'strict')

def check_level(settings):
    # Missing key preserves saved projects' existing MAX QUALITY behavior.
    if 'qcCheckLevel' in settings:
        return settings['qcCheckLevel']
    if settings.get('visionQC') or settings.get('generationMode') == 'MAX QUALITY':
        return settings.get('qcPolicy', 'practical')
    return 'off'

def should_check(project, shot):
    level = check_level(project['settings'])
    if level == 'off': return False
    if level != 'sampled': return True
    interval = project['settings'].get('qcSampleEvery', 5)
    intro = project.get('intro', {}).get('shots', [])
    groups = [intro] + [[s for sc in ch.get('scenes', []) for s in sc['shots']]
                       for ch in project['chapters']]
    if any(group and group[0].get('id') == shot.get('id') for group in groups): return True
    key = project['id'] + ':' + shot['id']
    return int(hashlib.sha256(key.encode()).hexdigest()[:16], 16) % interval == 0

def validate_checks(settings):
    if settings.get('qcCheckLevel', 'off') not in CHECK_LEVELS:
        raise ValueError('Choose Off, Sampled, Practical or Strict checks.')
    interval = settings.get('qcSampleEvery', 5)
    if isinstance(interval, bool) or not isinstance(interval, int) or not 2 <= interval <= 20:
        raise ValueError('Sample every 2â€“20 images.')

def policy(settings):
    level = settings.get('qcCheckLevel')
    return level if level in ('practical', 'strict') else settings.get('qcPolicy', 'practical')

def asset_hash(store, pid, name, context=None):
    """Memoize only inside one view request; later requests recheck changed bytes."""
    key = (pid, name)
    if context is not None and key in context['files']:
        return context['files'][key]
    path = store.asset(pid, name)
    value = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    if context is not None: context['files'][key] = value
    return value

def review_signature(project, shot, store, context=None):
    """Bind manual decisions to story/identity inputs as well as image bytes."""
    fields = ('prompt','negativePrompt','imageProvider','imageModel','workflow','characters',
              'camera','location','action','pose','expression','lighting','narrationSegment',
              'continuity','intentionalAppearanceChanges','generationSettings')
    if context is not None and shot['id'] in context['reviews']:
        return context['reviews'][shot['id']]
    is_intro = (shot['id'] in context['intro'] if context is not None else any(s.get('id')==shot.get('id') for s in project.get('intro',{}).get('shots',[])))
    chapter = None if is_intro else (context['chapters'][shot['chapterId']] if context is not None else get_chapter(project, shot['chapterId']))
    selected = {c['id'] for c in shot.get('characters', [])}
    people = [c for c in project['characters']+(chapter.get('people', []) if chapter else []) if c['id'] in selected]
    references = [r for c in people for r in c.get('references', [])]
    references += [r for loc in project.get('locations', []) if loc['id']==shot.get('locationId') for r in loc.get('references', [])]
    references += project.get('styleReferences', [])
    if shot.get('manual', {}).get('referenceImages'): references += shot.get('referenceImages', [])
    fingerprints = []
    for ref in references:
        if ref.get('path'):
            fingerprints.append({'path':ref['path'], 'sha256':asset_hash(store,project['id'],ref['path'],context)})
    source = ({k:project['intro'].get(k) for k in ('voiceText','sourceBrief','duration')} if is_intro else chapter.get('sourceText',''))
    value = digest({'shot':{k:shot.get(k) for k in fields}, 'source':source,
                   'people':[{k:c.get(k) for k in ('id','description','permanentIdentity','references')} for c in people],
                   'references':fingerprints, 'style':project['settings'].get('style'),
                   'constraints':project['settings'].get('imageVisualConstraints', project['settings'].get('visualConstraints',''))})
    if context is not None: context['reviews'][shot['id']] = value
    return value

def valid_acceptance(project, shot, store, context=None):
    override = shot.get('qcOverride', {})
    if (override.get('decision')!='accepted' or shot.get('generationStale')
            or not shot.get('imagePath') or override.get('imagePath')!=shot['imagePath']): return False
    actual = asset_hash(store,project['id'],shot['imagePath'],context)
    return (actual is not None and override.get('imageSHA256')==actual
            and override.get('reviewSpecSignature')==review_signature(project, shot, store,context))

def issue_levels(qc):
    """Structured new findings; narrow fallback for saved older review text."""
    issues = qc.get('issues', [])
    findings = qc.get('findings')
    if isinstance(findings,list) and len(findings)==len(issues) and all(
            isinstance(f,dict) and f.get('issue')==issue and f.get('severity') in ('advisory','major','uncertain')
            for f,issue in zip(findings,issues)):
        return [f['severity'] for f in findings]
    levels = []
    for issue in issues:
        text = str(issue).lower()
        if re.search(r'wrong (?:character|identity|weapon)|(?:bladed weapon|sword|knife).{0,60}(?:rather than|instead of).{0,30}(?:gun|pistol|revolver)',text):
            levels.append('major')
        elif re.search(r'fram(?:ing|ed)|close[- ]?up|composition is wider|looking down at|eyes fixed|eye contact|tiny|small detail|no knives (?:are )?visib|no knives visibly|no knives piercing|standing exposed|staying sheltered|gripping a gun with both hands',text):
            levels.append('advisory')
        else:
            levels.append('uncertain')
    return levels

def decision(project, shot, store, context=None):
    qc = shot.get('qc', {})
    mode = policy(project['settings'])
    if qc.get('status')=='PENDING': return {'disposition':'pending','blocking':True,'policy':mode}
    if valid_acceptance(project, shot, store,context): return {'disposition':'accepted','blocking':False,'policy':mode}
    if qc.get('pass') is True: return {'disposition':'passed','blocking':False,'policy':mode}
    if qc.get('pass') is not False:
        # Preserve prior semantics for deliberately disabled/unavailable vision.
        return {'disposition':'unreviewed','blocking':False,'policy':mode}
    levels = issue_levels(qc)
    if mode=='strict':
        return {'disposition':'review','blocking':qc.get('action')!='review','policy':mode,'issueLevels':levels}
    minor = bool(levels) and all(level=='advisory' for level in levels)
    return {'disposition':'advisory' if minor else 'review','blocking':not minor,'policy':mode,'issueLevels':levels}

def production_status(project, shot, store):
    assessment = decision(project, shot, store)
    if assessment['blocking']: return 'FAILED' if assessment['disposition']!='pending' else 'QC'
    return 'PASSED' if shot.get('qc', {}).get('pass') is True else 'COMPLETE'

def automatic_repairs(settings):
    return settings.get('qcCheckLevel') != 'off' and policy(settings)=='strict' and bool(settings.get('automaticRepair')) and repair_budget_reason(settings) is None

def repair_budget_reason(settings):
    """Unknown combined overhead never authorizes spending to find it out.

    A future measured estimate must cover all configured retries, rechecks and
    incremental rental. Existing API/GPU absolute caps still govern dispatch.
    """
    estimate = settings.get('qcCostEstimate', {})
    if not isinstance(estimate,dict) or not all(estimate.get(k) is True for k in (
            'baselineControlled','includesAPI','includesIncrementalRental','includesRechecks')):
        return 'Repair cost is unverified: a comparable generation baseline plus QC, rechecks and incremental rental is required before spending.'
    baseline = estimate.get('baselineGenerationUSD')
    upper = estimate.get('combinedQCAndReplacementUpperUSD')
    limit = settings.get('qcOverheadLimitPercent', 10)
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v)
           for v in (baseline,upper,limit)) or baseline<=0 or upper<0 or not 0<=limit<=10:
        return 'Repair cost estimate or overhead limit is invalid.'
    covered = estimate.get('maxImageRetriesCovered')
    if isinstance(covered,bool) or not isinstance(covered,int) or covered<int(settings.get('maxImageRetries',2)):
        return 'Repair estimate does not cover all configured replacements and rechecks.'
    if upper>baseline*limit/100:
        return 'Combined QC and replacement estimate exceeds the 10% cost ceiling.'
    return None

def project_view(store, pid):
    project = store.load(pid)
    context = {'files':{},'reviews':{},'chapters':{c['id']:c for c in project['chapters']},'intro':{s['id'] for s in project.get('intro',{}).get('shots',[])}}
    for chapter in project['chapters']:
        for scene in chapter['scenes']:
            for shot in scene['shots']:
                shot['qcDecision'] = decision(project,shot,store,context)
                if shot.get('imagePath'):shot['qcReviewSignature']=review_signature(project,shot,store,context)
    for shot in project.get('intro',{}).get('shots',[]):
        shot['qcDecision'] = decision(project,shot,store,context)
        if shot.get('imagePath'):shot['qcReviewSignature']=review_signature(project,shot,store,context)
    reason=repair_budget_reason(project['settings'])
    project['qcRepairBudget']={'allowed':reason is None,'reason':reason,'overheadLimitPercent':project['settings'].get('qcOverheadLimitPercent',10)}
    return project

def accept_image(store, pid, chid, sid, options):
    """Explicit current-image acceptance without replacing any AI finding."""
    def apply(project):
        shot = next((s for s in project.get('intro',{}).get('shots',[]) if s['id']==sid), None)
        if shot is None: shot = get_shot(project,chid,sid)
        if not shot.get('imagePath') or options.get('imagePath')!=shot['imagePath']:
            raise ValueError('The selected image changed. Refresh and review the current image before accepting it.')
        path = store.asset(pid,shot['imagePath'])
        if not path.is_file(): raise ValueError('The selected image is missing.')
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if options.get('imageSHA256') and options['imageSHA256']!=actual:
            raise ValueError('The image bytes changed. Refresh and review the current image before accepting it.')
        if shot.get('generationStale'): raise ValueError('Shot inputs changed. Review or regenerate the current image before accepting it.')
        signature = review_signature(project,shot,store)
        if options.get('reviewSpecSignature')!=signature:
            raise ValueError('Shot or reference inputs changed. Refresh and review the current image before accepting it.')
        if shot.get('qcOverride'):shot.setdefault('qcOverrideHistory', []).append(copy.deepcopy(shot['qcOverride']))
        shot['qcOverride'] = {'decision':'accepted','reviewer':'USER','reviewedAt':time.time(),
            'imagePath':shot['imagePath'],'imageSHA256':actual,'reviewSpecSignature':signature,
            'reason':str(options.get('reason') or 'User accepts this image for production')[:1000]}
        # QC can still be pending: retain that status until its actual result saves.
        if shot.get('qc',{}).get('status')!='PENDING':shot['status']=production_status(project,shot,store)
        shot['generationError']=''
        if not any(s.get('id')==sid for s in project.get('intro',{}).get('shots',[])):
            get_chapter(project,chid)['renderStale']=True
        project['renderStale']=True
    store.mutate(pid,apply)
    return project_view(store,pid)
