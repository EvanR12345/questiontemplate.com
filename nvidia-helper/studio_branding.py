"""Optional export watermark, rasterized locally without interpreting user text."""
import hashlib
import math
import re
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

DEFAULTS = {'enabled': False, 'type': 'text', 'text': 'Studio', 'imagePath': '',
            'position': 'bottom-right', 'widthPercent': 12, 'opacity': .65,
            'marginPercent': 2, 'color': '#FFFFFF'}
POSITIONS = ('top-left', 'top-right', 'bottom-left', 'bottom-right', 'center')

def settings(project):
    return DEFAULTS | project['settings'].get('watermark', {})

def validate(value):
    if not isinstance(value, dict):
        raise ValueError('Watermark settings must be an object.')
    w = DEFAULTS | value
    if not isinstance(w['enabled'], bool) or w['type'] not in ('text', 'image') or w['position'] not in POSITIONS:
        raise ValueError('Choose a valid watermark type and position.')
    for key, low, high in [('widthPercent', 1, 50), ('opacity', .05, 1), ('marginPercent', 0, 10)]:
        v = w[key]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not low <= v <= high:
            raise ValueError(f'Watermark {key} must be between {low} and {high}.')
    if not isinstance(w['text'], str) or len(w['text']) > 100 or any(ord(c) < 32 for c in w['text']):
        raise ValueError('Use one line of watermark text, up to 100 characters.')
    if not isinstance(w['color'], str) or not re.fullmatch(r'#[a-fA-F0-9]{6}', w['color']):
        raise ValueError('Choose a six-digit watermark color.')
    if not isinstance(w['imagePath'], str) or (w['imagePath'] and
            (Path(w['imagePath']).is_absolute() or '..' in Path(w['imagePath']).parts or ':' in w['imagePath'])):
        raise ValueError('Watermark images must be project assets.')
    if w['enabled'] and ((w['type'] == 'text' and not w['text'].strip()) or
                        (w['type'] == 'image' and not w['imagePath'])):
        raise ValueError('Enter watermark text or upload a logo before enabling it.')
    return w

def font_path():
    candidates = [Path('C:/Windows/Fonts/segoeui.ttf'), Path('C:/Windows/Fonts/arial.ttf'),
                  Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    return next((p for p in candidates if p.is_file()), None)

def identity(project, store):
    w = validate(settings(project))
    if not w['enabled']:
        return {'enabled': False}
    if w['type'] == 'image':
        path = store.asset(project['id'], w['imagePath'])
        if not path.is_file():
            raise ValueError('The watermark logo is missing. Upload it again or disable the watermark.')
        source = hashlib.sha256(path.read_bytes()).hexdigest()
    else:
        path = font_path()
        source = hashlib.sha256(path.read_bytes()).hexdigest() if path else 'Pillow-default'
    return {'watermarkVersion': 1, 'settings': w, 'sourceSHA256': source}

def bitmap(project, store, target):
    w = validate(settings(project)); video = project['settings']['video']
    if w['type'] == 'image':
        path = store.asset(project['id'], w['imagePath'])
        with Image.open(path) as src:
            if src.width * src.height > 16_000_000:
                raise ValueError('Use a watermark logo smaller than 16 megapixels.')
            image = src.convert('RGBA')
    else:
        path = font_path()
        font = ImageFont.truetype(str(path), 96) if path else ImageFont.load_default(size=96)
        bounds = font.getbbox(w['text'])
        image = Image.new('RGBA', (max(1, bounds[2]-bounds[0])+4, max(1, bounds[3]-bounds[1])+4))
        ImageDraw.Draw(image).text((2-bounds[0], 2-bounds[1]), w['text'], font=font, fill=w['color'])
    width = max(1, round(video['width'] * w['widthPercent'] / 100))
    max_height = max(1, round(video['height'] * .5))
    height = max(1, round(image.height * width / image.width))
    if height > max_height:
        width = max(1, round(width * max_height / height)); height = max_height
    image = image.resize((width, height), Image.Resampling.LANCZOS)
    image.putalpha(image.getchannel('A').point(lambda a: round(a * w['opacity'])))
    partial = target.with_suffix('.partial.png')
    image.save(partial, 'PNG'); partial.replace(target)
    return image.size

def coordinates(project):
    w = settings(project); v = project['settings']['video']
    mx = round(v['width'] * w['marginPercent'] / 100)
    my = round(v['height'] * w['marginPercent'] / 100)
    if w['position'] == 'center':
        return '(main_w-overlay_w)/2', '(main_h-overlay_h)/2'
    return (str(mx) if w['position'].endswith('left') else f'main_w-overlay_w-{mx}',
            str(my) if w['position'].startswith('top') else f'main_h-overlay_h-{my}')
