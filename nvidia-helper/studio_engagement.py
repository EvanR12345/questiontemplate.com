"""Deterministic subscribe reminders and a separate, editable closing card."""
import math
import random
import wave
from array import array
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from studio_data import digest
from studio_branding import font_path

DEFAULTS = {
    'popupEnabled': False, 'minMinutes': 10, 'maxMinutes': 15,
    'popupDuration': 5, 'popupText': 'Enjoying the story? Subscribe for the next part.',
    'dingEnabled': True, 'dingVolume': .12, 'position': 'bottom-right',
    'outroEnabled': False, 'outroDuration': 15,
    'outroText': 'Subscribe for more fantasy stories, and tell us your favorite moment in the comments.',
    'nextPartTeaser': '', 'outroAudioPath': '', 'outroAudioSignature': '',
    'splitEnabled': False, 'partMinutes': 120,
    'splitMode': 'duration', 'chaptersPerPart': 1,
    'exportDestination': 'youtube', 'patreonOutroEnabled': False, 'patreonPopupEnabled': False,
}

def settings(p):
    s = validate(p['settings'].get('engagement', {}))
    # Destination rules apply to an export, never to the narration masters.
    if s['exportDestination'] == 'patreon':
        s['outroEnabled'] = s['patreonOutroEnabled']
        s['popupEnabled'] = s['patreonPopupEnabled']
    return s

def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Video engagement settings must be an object.')
    s = DEFAULTS | value
    for k in ('popupEnabled', 'dingEnabled', 'outroEnabled', 'splitEnabled', 'patreonOutroEnabled', 'patreonPopupEnabled'):
        if not isinstance(s[k], bool): raise ValueError(f'{k} must be on or off.')
    if s['exportDestination'] not in ('youtube', 'patreon'):
        raise ValueError('Choose YouTube or Patreon for this export.')
    if s['splitMode'] not in ('duration', 'chapters'):
        raise ValueError('Choose duration or chapter boundaries for parts.')
    if isinstance(s['chaptersPerPart'], bool) or not isinstance(s['chaptersPerPart'], int) or s['chaptersPerPart'] < 1:
        raise ValueError('Chapters per part must be a positive whole number.')
    for k, lo, hi in [('minMinutes', 1, 180), ('maxMinutes', 1, 180),
                      ('popupDuration', 2, 15), ('dingVolume', 0, .3),
                      ('outroDuration', 5, 60), ('partMinutes', 1, 1440)]:
        v = s[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not lo <= v <= hi:
            raise ValueError(f'{k} must be between {lo} and {hi}.')
    if s['minMinutes'] > s['maxMinutes']: raise ValueError('Minimum reminder interval exceeds maximum.')
    if s['position'] not in ('bottom-right', 'bottom-left'): raise ValueError('Choose a bottom corner for reminders.')
    for k, limit in [('popupText', 160), ('outroText', 800), ('nextPartTeaser', 800)]:
        if not isinstance(s[k], str) or len(s[k]) > limit:
            raise ValueError(f'{k} exceeds {limit} characters.')
    if (s['patreonOutroEnabled'] if s['exportDestination']=='patreon' else s['outroEnabled']) and not s['outroText'].strip():
        raise ValueError('Enter the spoken outro text.')
    name = s['outroAudioPath']
    if not isinstance(name, str) or (name and (Path(name).is_absolute() or '..' in Path(name).parts or ':' in name)):
        raise ValueError('Outro audio must be a project asset.')
    return s

def spoken_text(s):
    return '\n\n'.join(x.strip() for x in (s['outroText'], s['nextPartTeaser']) if x.strip())

def audio_signature(p):
    s = settings(p)
    return digest({'version': 1, 'text': spoken_text(s),
                   'voice': {k: p['settings'].get(k) for k in ('voice', 'speed', 'narrationDelivery')}})

def schedule(p, duration, namespace='full'):
    s = settings(p)
    if not s['popupEnabled']: return []
    rng = random.Random(digest({'project': p['id'], 'namespace': namespace,
        'intervals': [s['minMinutes'], s['maxMinutes'], s['popupDuration']]}))
    events = []; t = 0
    while True:
        t += rng.uniform(s['minMinutes'] * 60, s['maxMinutes'] * 60)
        if t + s['popupDuration'] >= duration - 3: break
        events.append({'start': round(t, 3), 'end': round(t + s['popupDuration'], 3)})
    return events

def card(p, target, outro=False):
    s = settings(p); v = p['settings']['video']
    width = v['width'] if outro else round(v['width'] * .38)
    height = v['height'] if outro else round(v['height'] * .17)
    image = Image.new('RGBA', (width, height), (13, 18, 28, 255) if outro else (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if not outro: draw.rounded_rectangle((0, 0, width-1, height-1), radius=round(height*.16), fill=(13,18,28,235))
    fp = font_path(); size = max(12, round(height * (.06 if outro else .18)))
    text = 'SUBSCRIBE • COMMENT\n\n' + s['nextPartTeaser'] if outro else s['popupText']
    while True:
        font = ImageFont.truetype(str(fp), size) if fp else ImageFont.load_default(size=size)
        lines = []
        for paragraph in text.splitlines():
            line=''
            for word in paragraph.split():
                # Split an overlong word too, so a user-entered URL cannot spill.
                pieces=[''];
                for char in word:
                    if draw.textlength(pieces[-1]+char,font=font)>width*.86:pieces.append('')
                    pieces[-1]+=char
                for piece in pieces:
                    proposed = (line + ' ' + piece).strip()
                    if draw.textlength(proposed, font=font) > width * .86 and line:
                        lines.append(line); line = piece
                    else: line = proposed
            lines.append(line)
        if len(lines)*round(size*1.5)<=height*.86 or size<=8:break
        size-=1
    line_h = round(size*1.5); y = max(5, (height-line_h*len(lines))//2)
    for line in lines:
        draw.text(((width-draw.textlength(line,font=font))/2, y), line, font=font, fill=(246,246,248))
        y += line_h
    image.save(target, 'PNG')

def ding(target, volume=.12):
    """Quiet two-tone bell with attack/release; never synthesized as speech."""
    rate = 24000; duration = .6; samples = array('h')
    for i in range(round(rate*duration)):
        t = i/rate; envelope = min(1,t/.008)*math.exp(-t*8)*min(1,(duration-t)/.06)
        x = (math.sin(2*math.pi*880*t) + .35*math.sin(2*math.pi*1320*t))/1.35
        samples.append(round(32767*volume*envelope*x))
    if __import__('sys').byteorder != 'little': samples.byteswap()
    with wave.open(str(target), 'wb') as wav:
        wav.setparams((1,2,rate,0,'NONE','not compressed')); wav.writeframes(samples.tobytes())
