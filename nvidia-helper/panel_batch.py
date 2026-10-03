"""Explicit economy storyboard canvas, with independently saved landscape shots.

This is an opt-in quality/cost tradeoff, not native model batching: four prompts
share one one-megapixel canvas. The full canvas, crop box, seed and workflow are
retained so users can diagnose or replay a panel and regenerate it individually.
"""
import copy

def panel_prompt(panels, reference_header='', style=''):
    if not 1 <= len(panels) <= 4:
        raise ValueError('Economy canvas needs one to four shots.')
    positions = ('TOP LEFT', 'TOP RIGHT', 'BOTTOM LEFT', 'BOTTOM RIGHT')
    lines = ['Create exactly FOUR independent full-color cinematic illustration frames in an exact 2 by 2 rectangular grid. '
             'Each quadrant is a separate complete landscape image. The grid fills the canvas edge to edge. '
             'No white margins, gutters, borders, panel outlines, captions, numbers, lettering or speech bubbles. '
             'Do not blend neighboring frames or combine their events. Draw all faces and environments as 2D manhwa art, never photography.']
    if reference_header:
        lines.append(reference_header)
    if style:
        lines.append('All four frames use this visual style: ' + style)
    for index in range(4):
        panel = panels[min(index, len(panels)-1)]
        lines.append(positions[index] + ' QUADRANT ONLY: ' + panel['prompt'])
    return '\n\n'.join(lines)

def split_panels(image, count):
    from PIL import ImageOps
    if not 1 <= count <= 4:
        raise ValueError('Invalid panel count.')
    if image.width < 1024 or image.height < 576:
        raise ValueError('Storyboard canvas is too small for usable landscape crops.')
    result = []
    for index in range(count):
        column, row = index % 2, index // 2
        x0, y0 = column * image.width // 2, row * image.height // 2
        x1, y1 = (column + 1) * image.width // 2, (row + 1) * image.height // 2
        # A narrow, declared crop removes model-drawn seams. The final crop is
        # 16:9 and contains no renderer padding; the untouched source is retained.
        inset = 8
        box = (x0+inset, y0+inset, x1-inset, y1-inset)
        crop = ImageOps.fit(image.crop(box).convert('RGB'), (640,360))
        result.append({'pil':crop, 'cropBox':list(box), 'panelIndex':index, 'resolution':[640,360]})
    return result

def validate_groups(groups, shots):
    ids = [s['id'] for s in shots]
    flattened = [sid for group in groups for sid in group]
    if any(not 1 <= len(group) <= 4 for group in groups) or len(flattened) != len(ids) or set(flattened) != set(ids):
        raise ValueError('Director economy groups must include every shot exactly once, in groups of one to four.')
    if len(set(flattened)) != len(flattened):
        raise ValueError('A shot appears in more than one economy group.')
    return copy.deepcopy(groups)
