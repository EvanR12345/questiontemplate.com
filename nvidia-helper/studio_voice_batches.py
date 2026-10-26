"""Bounded whole-production narration reuse; manual chapter jobs stay isolated."""
import base64,copy,json,time
from pathlib import Path
from studio_voice_provider import KaggleVoice,KaggleQuotaExhausted,location,selected


def grouped_narration(service,project,chapter):
    settings=project['settings']
    if location(settings)!='kaggle':return None
    if service.voice_quota_fallback_active(settings):return None
    size=settings.get('voiceChapterBatchSize',2)
    if size==1:return None
    if type(size) is not int or not 1<=size<=4:raise ValueError('Choose 1–4 chapters per voice job.')
    # A manual regenerate-one action never silently submits another chapter.
    with service.cv:
        row=service.db.execute('SELECT kind FROM jobs WHERE id=?',(service.current,)).fetchone() if service.current else None
    if not row or row['kind'] not in ('produce-story','prepare-story'):return None
    active=[c for c in project['chapters'] if c.get('sourceText','').strip()]
    index=next(i for i,c in enumerate(active) if c['id']==chapter['id'])
    candidates=active[index//size*size:(index//size+1)*size]
    items=[]
    for ch in candidates:
        if service.saved_narration_ready(project,ch):continue
        signature=service.narration_signature(project,ch)
        folder=service.store.folder(project['id'])/ch['id'];folder.mkdir(exist_ok=True)
        items.append({'id':ch['id'],'signature':signature,'text':service.narration_text(project,ch),
            'target':str(folder/f"chapter-{ch['number']:03d}-{signature[:12]}.opus")})
    if not items or sum(len(i['text'].split()) for i in items)>4000:return None
    # Cloud-only completed group members are still completed work. Restore the
    # small receipt first, then only the audio/anchor it identifies. Asset restore
    # verifies each immutable cloud checksum before the provider can reuse it.
    root=service.store.folder(project['id'])
    for item in items:
        target=Path(item['target']);receipt=target.with_suffix('.voice-ready.json')
        service.store.asset(project['id'],receipt.relative_to(root).as_posix())
        if not receipt.is_file():continue
        value=json.loads(receipt.read_text(encoding='utf-8'))
        if value.get('inputSignature')!=item['signature']:continue
        service.store.asset(project['id'],target.relative_to(root).as_posix())
        anchor=value.get('result',{}).get('voiceAnchor')
        if anchor:
            # Older absolute receipt paths may belong to another helper. Only
            # the generated, content-addressed anchor filename is reusable.
            import re
            name=Path(anchor['path']).name
            if not re.fullmatch(r'voice-anchor-[a-f0-9]{20}\.(?:opus|wav)',name):
                raise ValueError('Invalid saved narrator reference.')
            restored=service.store.asset(project['id'],name)
            if not restored.is_file():raise ValueError('Saved narrator reference is unavailable; no duplicate voice job was submitted.')
            anchor['path']=str(restored)
            KaggleVoice._write(receipt,value)
    reference=None;engine=selected(settings);anchor=project.get('voiceAnchors',{}).get(engine)
    if anchor:
        raw=service.store.asset(project['id'],anchor['path']).read_bytes()
        if len(raw)>8*1024*1024:raise ValueError('Generated voice reference exceeds 8 MiB.')
        reference={'audio':base64.b64encode(raw).decode(),'text':anchor['text']}
    began=time.monotonic()
    try:
        results=KaggleVoice(service.config).generate_many(items,settings,service.gate,reference=reference)
    except KaggleQuotaExhausted:
        if not service.remember_voice_quota(settings):raise
        # Only the current complete chapter falls back; saved group members and
        # their receipts stay untouched. Subsequent chapters use the same route.
        return None
    # Keep real group measurements, and do not pretend the first chapter's wait
    # or the next cached chapter's near-zero time is isolated TTS throughput.
    service._last_voice_group={'chapters':len(items),'seconds':time.monotonic()-began,
        'characters':sum(len(i['text']) for i in items),'sharedTiming':True}
    result=results[next(i for i,item in enumerate(items) if item['id']==chapter['id'])]
    if result.get('voiceAnchor'):
        anchor=copy.deepcopy(result['voiceAnchor'])
        anchor['path']=Path(anchor['path']).relative_to(service.store.folder(project['id'])).as_posix()
        service.store.mutate(project['id'],lambda p:p.setdefault('voiceAnchors',{}).setdefault(engine,anchor))
        result['voiceAnchor']=anchor
    return result
