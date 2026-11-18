"""Bounded whole-production narration reuse; manual chapter jobs stay isolated."""
import base64,json,re,time
from pathlib import Path
from studio_voice_provider import KaggleVoice,KaggleQuotaExhausted,location,selected,project_anchor,anchor_identity


def chapter_membership(service,project,chapter,signature):
    member={'id':chapter['id'],'signature':signature}
    anchor=project_anchor(project)
    if anchor:
        previous=service.narration_signature(project|{'voiceAnchors':{}},chapter)
        member.update(anchor=anchor,baseSignature=previous,
            baseName=f"chapter-{chapter['number']:03d}-{previous[:12]}.opus")
    return member


def restore_chapter_receipts(store,pid,target,item):
    """Restore only the immutable assets for a matching completed request."""
    root=store.folder(pid)
    candidates=[(target,item['signature'],None)]
    if item.get('baseName'):
        candidates.append((target.with_name(item['baseName']),item['baseSignature'],tuple(item['anchor'])))
    for audio,signature,expected_anchor in candidates:
        receipt=store.asset(pid,audio.with_suffix('.voice-ready.json').relative_to(root).as_posix())
        if not receipt.is_file():continue
        value=json.loads(receipt.read_text(encoding='utf-8'))
        anchor=value.get('result',{}).get('voiceAnchor')
        if value.get('inputSignature')!=signature or (expected_anchor and anchor_identity(anchor)!=expected_anchor):
            continue
        restored_audio=store.asset(pid,audio.relative_to(root).as_posix())
        if not KaggleVoice._saved_chapter(restored_audio,signature):
            raise ValueError('Saved narration failed its checksum or is unavailable. Restore it before retrying.')
        if anchor:
            name=anchor_identity(anchor)[0]
            if not re.fullmatch(r'voice-anchor-[a-f0-9]{20}\.(?:opus|wav)',name):
                raise ValueError('Invalid saved narrator reference.')
            # Group anchors live at the project root. Older single-job anchors
            # can be alongside the chapter, with a path from another helper.
            for folder in (root,audio.parent):
                restored=store.asset(pid,(folder/name).relative_to(root).as_posix())
                if restored.is_file() and KaggleVoice._checksum(restored).startswith(restored.stem.removeprefix('voice-anchor-')):
                    break
            else:
                raise ValueError('Saved narrator reference is unavailable or damaged. Restore it before retrying.')
            if anchor['path']!=str(restored):
                anchor['path']=str(restored)
                KaggleVoice._write(receipt,value)
        return


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
        items.append({**chapter_membership(service,project,ch,signature),'text':service.narration_text(project,ch),
            'target':str(folder/f"chapter-{ch['number']:03d}-{signature[:12]}.opus")})
    if not items or sum(len(i['text'].split()) for i in items)>4000:return None
    for item in items:
        restore_chapter_receipts(service.store,project['id'],Path(item['target']),item)
    reference=None;engine=selected(settings);anchor=project.get('voiceAnchors',{}).get(engine) if engine in ('dia-1.6b','fish-s2-pro') else None
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
    return results[next(i for i,item in enumerate(items) if item['id']==chapter['id'])]
