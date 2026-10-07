"""Historical estimates for the existing serial production queue.

The observed range is not a confidence interval. Unknown stages and jobs beyond
all matching observations must remain unknown rather than count as zero work.
"""
import json
import math


def timing_profile(kind, payload):
    if kind != 'image':
        return kind
    shot = payload.get('shotSnapshot', {})
    settings = shot.get('generationSettings', {})
    return json.dumps([kind, shot.get('imageProvider'), shot.get('imageModel'), shot.get('workflow'),
        settings.get('width'), settings.get('height'), settings.get('steps'),
        payload.get('operation', 'generate'), payload.get('qcTimingProfile', 'legacy'),
        bool(payload.get('deferQC'))])


def remaining_time(pending, samples, now):
    if not pending:
        return {'etaSeconds': 0, 'etaRangeSeconds': [0, 0], 'etaStatus': 'IDLE'}
    estimates, lower, upper = [], [], []
    unknown = False
    overrun = False
    for job in pending:
        history = [float(value) for value in samples.get(job['timingProfile'], [])
                   if isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) and value > 0]
        if not history or job['kind'] == 'produce-story':
            unknown = True
            continue
        if job['status'] == 'RUNNING':
            if not job.get('started'):
                unknown = True
                continue
            elapsed = max(0, now - job['started'])
            history = [value - elapsed for value in history if value > elapsed]
            if not history:
                overrun = True
                continue
        estimates.append(sum(history)/len(history))
        lower.append(min(history))
        upper.append(max(history))
    if unknown or overrun:
        return {'etaSeconds': None, 'etaRangeSeconds': None,
                'etaStatus': 'LONGER_THAN_HISTORY' if overrun else 'INSUFFICIENT_HISTORY'}
    return {'etaSeconds': max(1, round(sum(estimates))),
            'etaRangeSeconds': [max(1, math.floor(sum(lower))), max(1, math.ceil(sum(upper)))],
            'etaStatus': 'ESTIMATED'}
