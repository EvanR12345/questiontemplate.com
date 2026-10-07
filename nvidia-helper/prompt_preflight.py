"""Deterministic checks of supplied shot facts; no story rewriting or model calls."""
import math
import re


def check_shot(project, shot):
    issues = []
    def add(code, severity, message):
        issues.append({'code': code, 'severity': severity, 'message': message})
    chapter = next((c for c in project['chapters'] if c['id'] == shot.get('chapterId')), None)
    people = {c['id']: c for c in project.get('characters', [])}
    if chapter:
        people.update({c['id']: c for c in chapter.get('people', [])})
    else:
        add('UNKNOWN_CHAPTER', 'error', 'Shot chapter is absent from the project.')
    prompt = str(shot.get('prompt') or '').strip()
    if not prompt:
        add('EMPTY_PROMPT', 'error', 'The image prompt is empty.')
    start, end = shot.get('start'), shot.get('end')
    if (not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (start, end))
            or end <= start or start < 0):
        add('INVALID_TIMING', 'error', 'Shot timing must be finite, nonnegative and have positive duration.')
    ids = [c.get('id') for c in shot.get('characters', [])]
    if len(ids) != len(set(ids)):
        add('DUPLICATE_CHARACTER', 'error', 'The same identity occupies more than one character slot.')
    for selected in shot.get('characters', []):
        person = people.get(selected.get('id'))
        if not person:
            add('UNKNOWN_CHARACTER', 'error', 'A selected character is absent from the project/chapter cast.')
            continue
        if selected.get('type') == 'main' and not person.get('references'):
            add('MISSING_IDENTITY_REFERENCE', 'warning', 'Main character ' + person['name'] + ' has no identity reference yet.')
        state = selected.get('appearanceState', {})
        context = ' '.join(str(shot.get(k, '')) for k in ('action', 'pose', 'narrationSegment'))
        for prop in ('gun', 'sword', 'knife', 'hammer', 'bomb'):
            # Flag only a named action with canonical held state. Inventory by
            # itself is not evidence to put a weapon into the current picture.
            if (re.search(r'\b' + prop + r's?\b', context, re.I)
                    and re.search(r'\b(?:holding|held|wielding|aiming|firing)\b|\b(?:left|right)[_ ]hand\b', str(state.get(prop, '')), re.I)
                    and not re.search(r'\b' + prop + r's?\b', prompt, re.I)):
                add('VISIBLE_PROP_MISSING', 'warning', person['name'] + "'s visible " + prop + ' is absent from the prompt.')
    return {'version': 1, 'passed': not any(i['severity'] == 'error' for i in issues), 'issues': issues}


def require_shot(project, shot):
    result = check_shot(project, shot)
    if not result['passed']:
        raise ValueError('Image preflight: ' + '; '.join(i['message'] for i in result['issues'] if i['severity'] == 'error'))
    return result
