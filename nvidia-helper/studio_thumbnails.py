"""Local composition of existing story art; no paid generation or video changes."""
from pathlib import Path
import uuid
from PIL import Image, ImageDraw, ImageFont, ImageOps
from studio_branding import font_path
from studio_data import digest

def make(store, pid, options):
    p=store.load(pid)
    source=str(options.get('source',''))
    title=str(options.get('title',p['name'])).strip()
    part=str(options.get('part','')).strip()
    if len(title)>180 or len(part)>24:
        raise ValueError('Use a title up to 180 characters and a part label up to 24.')
    path=store.asset(pid,source)
    if path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp'):
        raise ValueError('Choose a saved story image for the thumbnail.')
    with Image.open(path) as img:
        img.load(); canvas=ImageOps.fit(img.convert('RGB'),(1280,720),method=Image.Resampling.LANCZOS)
    overlay=Image.new('RGBA',canvas.size)
    draw=ImageDraw.Draw(overlay)
    for y in range(720):
        opacity=round(190*max(0,(y-220)/500))
        draw.line((0,y,1280,y),fill=(0,0,0,opacity))
    canvas=Image.alpha_composite(canvas.convert('RGBA'),overlay); draw=ImageDraw.Draw(canvas)
    fp=font_path()
    def font(size): return ImageFont.truetype(str(fp),size) if fp else ImageFont.load_default(size=size)
    if part:
        badge='PART '+part if not part.lower().startswith('part') else part.upper()
        f=font(38); box=draw.textbbox((0,0),badge,font=f)
        draw.rounded_rectangle((32,32,68+box[2],102),radius=12,fill=(241,199,64))
        draw.text((50,44),badge,font=f,fill=(18,22,32),stroke_width=0)
    size=76
    while True:
        f=font(size); lines=[]; line=''
        for word in title.split():
            candidate=(line+' '+word).strip()
            if draw.textlength(candidate,font=f)>1160 and line:
                lines.append(line); line=word
            else: line=candidate
        if line: lines.append(line)
        if len(lines)<=3 and all(draw.textlength(x,font=f)<=1160 for x in lines) or size<=28: break
        size-=4
    y=680-len(lines)*round(size*1.18)
    for line in lines:
        draw.text((48,y),line,font=f,fill='white',stroke_width=3,stroke_fill=(10,14,22)); y+=round(size*1.18)
    identity=digest({'source':source,'mtime':path.stat().st_mtime_ns,'title':title,'part':part,'version':1})
    folder=store.folder(pid)/'thumbnails'; folder.mkdir(exist_ok=True)
    target=folder/f'thumbnail-{identity[:20]}.jpg'
    temporary=target.with_name(target.stem+'.writing.'+uuid.uuid4().hex+'.jpg')
    try:
        canvas.convert('RGB').save(temporary,'JPEG',quality=92,optimize=True)
        if temporary.stat().st_size>2_000_000:
            canvas.convert('RGB').save(temporary,'JPEG',quality=82,optimize=True)
        temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
    asset={'path':target.relative_to(store.folder(pid)).as_posix(),'source':source,'title':title,'part':part,'width':1280,'height':720}
    def save(q):
        if not any(x['path']==asset['path'] for x in q.setdefault('thumbnails',[])): q['thumbnails'].append(asset)
    store.mutate(pid,save)
    return asset
