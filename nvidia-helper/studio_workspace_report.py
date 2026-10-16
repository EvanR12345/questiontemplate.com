"""Read-only workspace checks and an honest retained-evidence production report."""
import math
import time


def reference_checks(store, project):
    checks = []
    shots = [s for c in project['chapters'] for scene in c.get('scenes', []) for s in scene.get('shots', [])]
    shots += project.get('intro', {}).get('shots', []) if project.get('intro', {}).get('enabled') else []
    used = {cast['id'] for shot in shots for cast in shot.get('characters', [])}
    for person in project.get('characters', []):
        if person['id'] not in used:
            continue
        refs = person.get('references', [])
        paths = [r if isinstance(r, str) else r.get('path') or r.get('imagePath') for r in refs]
        available = [path for path in paths if path and store.has_asset(project['id'], path)]
        checks.append({'name': 'reference-' + person['id'], 'ready': bool(available),
            'blocking': False, 'message': person['name'] + (': character reference available.' if available else
                ': no available identity reference. Add one in Characters for consistent images.')})
        if any(path and path not in available for path in paths):
            checks.append({'name': 'reference-file-' + person['id'], 'ready': False,
                'blocking': True, 'message': person['name'] + ': a selected reference file is missing. Restore it or remove the broken reference.'})
    return checks


def completion_report(service, pid):
    p = service.store.load(pid)
    trace = service.trace_report(pid)
    rows = trace.get('spans', [])
    jobs = [r for r in rows if r.get('kind') == 'job']
    closed = [r for r in jobs if r.get('finished') is not None]
    production = p.get('production', {})
    timings = production.get('timings', [])
    # Adapter detail receipts own charges; parent stage rollups must not count twice.
    receipts = [r for r in timings if r.get('detail') and r.get('provider') == 'openai-luna' and not r.get('reused')]
    valid = lambda n: type(n) in (int, float) and math.isfinite(n) and n >= 0
    known_api = sum(r['estimatedUSD'] for r in receipts if valid(r.get('estimatedUSD')))
    unknown = sum(bool(r.get('usagePending')) or not valid(r.get('estimatedUSD')) for r in receipts)
    intro_shots = p.get('intro', {}).get('shots', [])
    shots = [s for c in p['chapters'] for scene in c.get('scenes', []) for s in scene.get('shots', [])] + intro_shots
    from studio_qc import decision
    issues = []
    for c in p['chapters']:
        if c.get('timingNeedsReview') or c.get('continuityNeedsReview'):
            issues.append({'chapter': c['id'], 'label': c['name'] + ': timing or continuity needs review.'})
        for scene in c.get('scenes', []):
            for s in scene.get('shots', []):
                qc = s.get('qc', {})
                if s.get('generationError') or qc.get('pass') is False or qc.get('status') in ('UNREVIEWED', 'PENDING'):
                    assessment = decision(p, s, service.store)
                    if assessment.get('disposition') == 'accepted':continue
                    issues.append({'chapter': c['id'], 'shot': s['id'], 'label':
                        c['name'] + ': ' + (s.get('generationError') or '; '.join(qc.get('issues', [])) or qc.get('status', 'Review finding')),
                        'blocking': bool(assessment.get('blocking'))})
    for s in intro_shots:
        qc = s.get('qc', {})
        if s.get('generationError') or qc.get('pass') is False or qc.get('status') in ('UNREVIEWED', 'PENDING'):
            assessment = decision(p, s, service.store)
            if assessment.get('disposition') == 'accepted':continue
            issues.append({'intro': True, 'label': 'Intro: ' + (s.get('generationError') or
                '; '.join(qc.get('issues', [])) or qc.get('status', 'Review finding')),
                'blocking': bool(assessment.get('blocking'))})
    current = bool(p.get('render', {}).get('path')) and not p.get('renderStale')
    repairs = {}
    for s in shots:
        for version in [*s.get('history', []), {'imagePath': s.get('imagePath'), 'metadata': s.get('imageMetadata', {})}]:
            meta = version.get('metadata', {})
            if meta.get('repair') or meta.get('operation') in ('edit', 'inpaint', 'repair'):
                repairs[meta.get('imageSHA256') or version.get('imagePath') or str(meta.get('time'))] = True
    status = production.get('status', 'NOT_COMPLETE')
    if not current and status == 'COMPLETE':
        status = 'STALE_EXPORT' if p.get('render', {}).get('path') else 'NOT_COMPLETE'
    elif current and status in ('NOT_COMPLETE', 'COMPLETE', 'READY', 'PASSED'):
        status = 'COMPLETE'
    return {'project': pid, 'revision': p['revision'], 'generatedAt': time.time(),
        'status': status,
        'videoPath': p.get('render', {}).get('path'), 'videoCurrent': current,
        'duration': p.get('render', {}).get('duration'),
        'scope': 'All retained project attempts, including failures and previews; not a single-run invoice.',
        'time': {'coveredWorkSeconds': trace.get('summary', {}).get('coveredWallSeconds'),
            'observedCalendarSeconds': max(r['finished'] for r in closed) - min(r['started'] for r in closed) if closed else None,
            'unfinishedSpans': trace.get('summary', {}).get('unfinishedOrInterrupted', 0),
            'stages': trace.get('summary', {}).get('stages', {}),
            'note': 'Calendar span can include pauses and gaps between runs. Stage work overlaps; do not sum it as elapsed time.'},
        'cost': {'apiKnownEstimatedUSD': known_api if receipts else None,
                 'apiUnknownReceipts': unknown,
                 'gpuWindowEstimatedUSD': production.get('costs', {}).get('gpuWindowEstimatedUSD'),
                 'note': 'Retained API/GPU estimates only. Storage, external costs, missing receipts and unresolved requests may be absent.'},
        'counts': {'chapters': len(p['chapters']), 'images': len(shots),
                   'savedImages': sum(bool(s.get('imagePath')) for s in shots),
                   'repairAttempts': len(repairs),
                   'failedJobs': sum(r.get('status') == 'FAILED' for r in jobs)},
        'issues': issues, 'warnings': [w for w in p.get('warnings', []) if not w.get('resolved')]}
