"""Opt-in grounded prompt guidance and honest per-generation measurements."""
import math

SCENE_GUIDANCE = (
    ' Preserve every supplied story-critical fact explicitly: known gender of each visible person, '
    'people count when the narration establishes it, each person\'s role and position, and who holds each prop. '
    'Do not turn a stated woman into an unspecified adult. Keep one simultaneous moment and distinguish '
    'a single held weapon from historical inventory. Do not duplicate people or weapons. '
    'Do not guess unknown traits, counts or a free hand when the source does not establish them. '
    'Keep expressive illustrated staging; small framing or pose differences are acceptable.'
    ' Describe drawable poses and relative positions in natural language; keep prop ownership explicit. '
    'Describe visible light and its effect on the scene, without invented story details.'
)
PROMPT_GUIDANCE = (
    ' Focused prompts are enabled. The supplied grounded draft is retained by the application. '
    'Return only a short, useful visual-direction supplement for composition, expression and lighting. '
    'Do not repeat the draft, identity boilerplate, style or exclusions. Never omit, contradict or replace '
    'explicit gender, established people count, prop ownership or current appearance. '
    'Use no fixed word quota and no filler. Unknown facts stay unknown; never invent new events.'
    ' Use natural-language relationships and concrete visible details: where subjects stand or sit, '
    'who holds what, and where light comes from. Name the visible effect of light when useful. '
    'Avoid vague praise such as beautiful or masterpiece. Prioritize requested details without '
    'repeating or dropping supplied story facts. Reference roles and image positions are supplied '
    'by the application; keep their meanings intact.'
)

def focused(project, shot=None):
    return project['settings'].get('focusedPrompts') is True and not (shot or {}).get('manual', {}).get('prompt')

def finite_positive(value):
    return not isinstance(value, bool) and isinstance(value, (int,float)) and math.isfinite(value) and value >= 0

def execution_evidence(entry, graph):
    messages = entry.get('status',{}).get('messages',[])
    stamps = {}; cached = set(); observed_cache = False
    for item in messages:
        if not isinstance(item,(list,tuple)) or len(item)!=2 or not isinstance(item[1],dict): continue
        event,data = item
        if event in ('execution_start','execution_success'): stamps[event] = data.get('timestamp')
        if event == 'execution_cached':
            observed_cache = True; cached.update(str(n) for n in data.get('nodes',[]))
    start,end = stamps.get('execution_start'),stamps.get('execution_success')
    seconds = (end-start)/1000 if finite_positive(start) and finite_positive(end) and end>=start else None
    text = {str(n) for n,v in graph.items() if v.get('class_type') == 'CLIPTextEncode'}
    sample = {str(n) for n,v in graph.items() if 'Sampler' in v.get('class_type','') or v.get('class_type') == 'VAEDecode'}
    return {'serverExecutionSeconds':seconds,
            'actualTextEncoderCacheHit':bool(text & cached) if observed_cache and text else None,
            'actualSamplerOrDecodeCacheHit':bool(sample & cached) if observed_cache and sample else None,
            'cacheEvidencePresent':observed_cache, 'cachedNodes':sorted(cached),
            'textEncoderNodes':sorted(text), 'cacheMeaning':'Any named CLIPTextEncode node hit; unknown if no cache event or matching nodes.',
            'timingSource':'ComfyUI history execution_start to execution_success; milliseconds converted to seconds'}

def generation_measurements(project, prompt, result):
    timings = result.get('providerTimings',{})
    client = timings.get('totalClientSeconds')
    if not finite_positive(client): client = None
    rate = project['settings'].get('cloudWindow',{}).get('gpuHourlyUSD')
    if result.get('provider') != 'comfyui' or not finite_positive(rate) or not rate: rate = None
    evidence = result.get('serverExecutionEvidence',{})
    return {'version':1,'promptWords':len(prompt.split()),'promptCharacters':len(prompt),
            'countScope':'Complete submitted prompt including reference-role prefix; words split on whitespace',
            'rawTokenizerTokens':None,'effectiveEncoderTokens':None,
            'tokenCountNote':'Unknown for this generation; no word-to-token guess or assumed encoder limit',
            'clientIntervalSeconds':client,'serverExecutionSeconds':evidence.get('serverExecutionSeconds'),
            'actualTextEncoderCacheHit':evidence.get('actualTextEncoderCacheHit'),
            'actualSamplerOrDecodeCacheHit':evidence.get('actualSamplerOrDecodeCacheHit'),
            'recordedRentalHourlyUSD':rate,
            'estimatedImageIntervalRentalUSD':client*rate/3600 if client is not None and rate is not None else None,
            'actualIndividualInvoiceUSD':None,'sharedOverheadIncluded':False,
            'costNote':'Elapsed client interval times the configured actual rental rate, when available. Excludes shared startup, warm-up, idle and shutdown. Not an itemized invoice; server time is a subset, not an extra charge.'}
