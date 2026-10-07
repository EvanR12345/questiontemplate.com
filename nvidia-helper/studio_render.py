"""Bounded-memory FFmpeg assembly with cached chapters and separate intro."""

import hashlib, json, math, os, shutil, subprocess, time, wave
from pathlib import Path
from studio_qc import decision as qc_decision
from studio_data import digest
from render_queue import render_unique_clips

SUPPORTED_MOTIONS = {'static','slow zoom in','slow zoom out','pan left','pan right','pan up','pan down'}

def effective_motion(shot, video):
    """Project motion adds movement to AI holds, preserving explicit shot edits."""
    motion = str(shot.get("motion", "static")).strip().lower()
    if motion not in SUPPORTED_MOTIONS:
        motion = 'static'
    mode = video.get("motionMode", "director")
    if mode == "static":
        return "static"
    if mode != "gentle" or shot.get("manual", {}).get("motion") or motion != "static":
        return motion
    duration = shot.get("end", 0) - shot.get("start", 0)
    camera = shot.get("camera", {}).get("shot", "").lower()
    if duration < 2 or "insert" in camera or "extreme close" in camera:
        return "static"
    if duration >= 8 and ("establishing" in camera or "extreme wide" in camera):
        return "pan right"
    if "close" in camera or "reaction" in camera:
        return "slow zoom out"
    return "slow zoom in"


class VideoRenderer:
    def __init__(self, store, config):
        self.store = store
        self.config = config
        self._asset_digests = {}

    @staticmethod
    def available_memory_bytes():
        try:
            if os.name == 'nt':
                import ctypes
                class MemoryStatus(ctypes.Structure):
                    _fields_ = [('length',ctypes.c_ulong),('load',ctypes.c_ulong)] + [
                        (name,ctypes.c_ulonglong) for name in
                        ('total','available','pageTotal','pageAvailable','virtualTotal','virtualAvailable','extended')]
                status = MemoryStatus()
                status.length = ctypes.sizeof(status)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                    return status.available
            else:
                return os.sysconf('SC_AVPHYS_PAGES') * os.sysconf('SC_PAGE_SIZE')
        except (AttributeError,OSError,ValueError):
            pass
        return None

    def render_capacity(self, video, pending=0):
        maximum = self.config.get('renderWorkers',2)
        if isinstance(maximum,bool) or not isinstance(maximum,int) or not 1 <= maximum <= 3:
            raise ValueError('Choose one to three render workers.')
        maximum = min(maximum,max(1,(os.cpu_count() or 1)//4))
        available = self.available_memory_bytes()
        if available is None:
            return 1
        # Account for workers already using RAM, then keep headroom for Chrome
        # and the queue owner. Never kill a clip just because capacity shrank.
        slot = max(256*2**20, video['width']*video['height']*300)
        capacity = int((available + pending*slot - 512*2**20)//slot)
        return max(1,min(maximum,capacity))

    def asset_identity(self, project_id, name):
        """Hash the saved bytes once per file revision, not QC or prompt metadata."""
        path = self.store.asset(project_id, name)
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
        cached = self._asset_digests.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        with path.open('rb') as asset:
            checksum = hashlib.sha256()
            for chunk in iter(lambda: asset.read(1024 * 1024), b''):
                checksum.update(chunk)
            checksum = checksum.hexdigest()
        self._asset_digests[str(path)] = (stamp, checksum)
        return checksum

    def shot_identity(self, project, shot):
        video = project['settings']['video']
        identity = {
            # A clip's pixels depend on its frame count, not where it sits in
            # narration. Moving an unchanged shot must not encode it again.
            'frames': max(1, round(shot['end']*video['fps']) - round(shot['start']*video['fps'])),
            'image': self.asset_identity(project['id'], shot['imagePath']),
            'motion': effective_motion(shot, video),
            'zoomAmount': shot.get('motionSettings', {}).get('zoomAmount', video.get('zoomAmount', .06)),
        }
        if identity['motion'] != 'static':
            identity['subpixelMotionVersion'] = 1
        return identity

    @staticmethod
    def legacy_is_current(legacy, inputs):
        # Old cache keys did not hash asset bytes. An image or WAV replaced at
        # the same path must not inherit a render of its previous contents.
        return legacy.is_file() and legacy.stat().st_size > 0 and all(
            asset.stat().st_mtime_ns <= legacy.stat().st_mtime_ns for asset in inputs
        )

    @staticmethod
    def reuse_legacy_clip(legacy, current, inputs=()):
        """Keep existing renders usable without consuming another clip's disk space."""
        if current.exists() or not VideoRenderer.legacy_is_current(legacy, inputs):
            return
        try:
            os.link(legacy, current)
        except OSError:
            shutil.copy2(legacy, current)

    def executable(self):
        path = Path(self.config.get("ffmpeg", ""))
        if not path.is_file():
            raise RuntimeError(
                "FFmpeg is unavailable. Set its existing installation path in Advanced settings. No chapter assets were changed."
            )
        return str(path)

    def audio_preview(self, project_id, source, gate):
        """Cache a 128 kbps audition; retain its lossless synthesis master."""
        signature = digest({'audio':self.asset_identity(project_id, source),
                            'codec':'aac', 'bitrate':128000, 'version':1})
        destination = self.store.folder(project_id) / f'audition-{signature[:24]}.m4a'
        if not destination.is_file() or destination.stat().st_size == 0:
            self.run(['-i', str(self.store.asset(project_id, source)), '-vn',
                      '-c:a', 'aac', '-b:a', '128k', '-movflags', '+faststart', str(destination)],
                     gate, self.store.folder(project_id) / 'audition-export.log')
        return {'exportPath':destination.name, 'exportCodec':'AAC', 'exportBitrate':128000}

    def run(self, args, gate, log, multiple_outputs=False):
        # Cache names become visible only after FFmpeg succeeds. A cancelled
        # intro, WAV or concatenation must never be reused as a complete asset.
        destination = Path(args[-1])
        staging = destination.with_name(
            destination.stem + ".writing" + destination.suffix
        )
        staged_args = args if multiple_outputs else [*args[:-1], str(staging)]
        try:
            with Path(log).open("wb") as output:
                proc = subprocess.Popen(
                    [self.executable(), "-hide_banner", "-nostdin", "-y", *staged_args],
                    stdout=output,
                    stderr=output,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                try:
                    while proc.poll() is None:
                        gate("Rendering video")
                        time.sleep(0.15)
                except BaseException:
                    proc.terminate()
                    try:
                        proc.wait(5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
                    raise
            if proc.returncode:
                raise RuntimeError(
                    "FFmpeg rendering failed: "
                    + Path(log).read_text(errors="replace")[-1400:]
                )
            if not multiple_outputs:
                staging.replace(destination)
        finally:
            # This invocation owns its staging path. Keep completed clip caches
            # and previous selected videos; a failed partial cannot be resumed.
            if not multiple_outputs and staging.is_file():
                staging.unlink()

    @staticmethod
    def require_output_space(destination, media_bytes, audio_seconds=0):
        """Bound the next stream-copy assembly from actual completed inputs."""
        required = math.ceil(media_bytes * 1.05 + audio_seconds * 16000) + 64 * 2**20
        free = shutil.disk_usage(Path(destination).parent).free
        if free < required:
            raise RuntimeError(
                f'Not enough disk space to assemble this video: need about {required / 2**30:.2f} GB free; '
                f'{free / 2**30:.2f} GB available. Completed clips and audio are saved. Free space and retry the render.'
            )

    def encoding(self, p):
        v = p["settings"]["video"]
        if (
            v["width"] not in (640, 1280, 1920)
            or v["height"] not in (360, 720, 1080)
            or v["fps"] not in (24, 25, 30, 60)
        ):
            raise ValueError("Use a supported output size and frame rate.")
        return [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(int(v.get("crf", 21))),
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(v["fps"]),
            "-threads",
            "2",
        ]

    def motion(self, shot, v, frames):
        w, h = v["width"], v["height"]
        motion = effective_motion(shot, v)
        interval = max(1, frames - 1)
        zoom = "1"
        x = None
        y = None
        amount = float(shot.get('motionSettings', {}).get('zoomAmount', v.get('zoomAmount', .06)))
        if not math.isfinite(amount) or not 0 <= amount <= .35:
            raise ValueError('Image zoom must be between 0 and 35 percent.')
        progress = f"on/{interval}"
        if v.get('motionEasing') == 'smooth':
            progress = f"(3*pow(on/{interval},2)-2*pow(on/{interval},3))"
        if "zoom in" in motion:
            zoom = f"1+{amount:g}*{progress}"
        elif "zoom out" in motion:
            zoom = f"{1+amount:g}-{amount:g}*{progress}"
        elif "pan left" in motion:
            zoom = "1.08"
            x = f"(W-W/({zoom}))*(1-{progress})"
        elif "pan right" in motion:
            zoom = "1.08"
            x = f"(W-W/({zoom}))*{progress}"
        elif "pan up" in motion:
            zoom = "1.08"
            y = f"(H-H/({zoom}))*(1-{progress})"
        elif "pan down" in motion:
            zoom = "1.08"
            y = f"(H-H/({zoom}))*{progress}"
        fitting = (
            f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}"
            if v.get("imageFit") == "cover"
            else f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=0x101720"
        )
        repeat = f"zoompan=z=1:x=0:y=0:d={frames}:s={w}x{h}:fps={v['fps']}"
        if motion == 'static' or frames <= 1:
            return f'{fitting},{repeat},setsar=1'
        x = x or f'(W-W/({zoom}))/2'
        y = y or f'(H-H/({zoom}))/2'
        # Floating source corners avoid zoompan's integer crop-position jumps.
        corners = [x, y, f'({x})+W/({zoom})', y,
                   x, f'({y})+H/({zoom})', f'({x})+W/({zoom})', f'({y})+H/({zoom})']
        transform = ':'.join(f'{key}=\'{value}\'' for key, value in zip(
            ('x0','y0','x1','y1','x2','y2','x3','y3'), corners))
        return f'{fitting},{repeat},format=yuv444p,perspective={transform}:sense=source:eval=frame:interpolation=cubic,format=yuv420p,setsar=1'

    @staticmethod
    def concat_listing(files, listing, durations=None):
        lines = []
        for i, f in enumerate(files):
            lines.append(
                "file '"
                + str(f.resolve()).replace("\\", "/").replace("'", "'\\''")
                + "'"
            )
            if durations:
                lines.append("duration " + str(durations[i]))
        listing.write_text("\n".join(lines), encoding="utf-8")
        return listing

    def concat(self, files, target, gate, log, durations=None, video_only=False):
        listing = self.concat_listing(files,target.with_suffix('.concat.txt'),durations)
        self.run(
            [
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                *(["-map", "0:v:0", "-an"] if video_only else []),
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(target),
            ],
            gate,
            log,
        )

    def chapter(self, p, ch, gate):
        shots = [
            s for scene in ch["scenes"] for s in scene["shots"] if s.get("imagePath")
        ]
        if (
            len(shots) != sum(len(scene["shots"]) for scene in ch["scenes"])
            or not shots
        ):
            raise ValueError("Generate every shot before rendering this chapter.")
        if not ch.get("audio", {}).get("path"):
            raise ValueError("Generate chapter narration first.")
        for index,shot in enumerate(shots):
            if qc_decision(p,shot,self.store)['blocking']:
                raise ValueError(f'Shot {index+1} needs review. Accept this image or request a repair before rendering.')
        if any(s.get('qc', {}).get('status') == 'PENDING' for s in shots):
            raise ValueError('Finish pending image quality checks before rendering.')
        if ch["audio"].get("textDigest") and ch["audio"]["textDigest"] != digest(
            ch["cleanNarrationText"]
        ):
            raise ValueError(
                "Narration text changed. Regenerate narration and review shot timing before rendering; the previous video is retained."
            )
        cursor = 0
        for shot in shots:
            if abs(shot["start"] - cursor) > 0.08:
                raise ValueError(
                    "Timeline has a gap or overlap. Adjust shot boundaries before rendering."
                )
            cursor = shot["end"]
        if abs(cursor - ch["audio"]["duration"]) > 0.08:
            raise ValueError("Timeline must end at the chapter narration duration.")
        v = p["settings"]["video"]
        fps = v["fps"]
        folder = self.store.folder(p["id"]) / ch["id"]
        folder.mkdir(exist_ok=True)
        legacy_signature = digest(
            {"rendererVersion": 5, "shots": shots, "audio": ch["audio"], "video": v}
        )
        visual_shots = [self.shot_identity(p, shot) for shot in shots]
        previous_visual_shots = [{k:value for k,value in visual.items() if k != 'frames'} |
                                 {'start':shot['start'], 'end':shot['end']}
                                 for shot,visual in zip(shots,visual_shots)]
        previous_signature = digest({
            'rendererVersion': 6, 'shots': previous_visual_shots,
            'transitions': [shot.get('transition') for shot in shots],
            'audio': self.asset_identity(p['id'], ch['audio']['path']),
            'duration': ch['audio']['duration'], 'video': v,
        })
        signature = digest({
            'rendererVersion': 7, 'shots': visual_shots,
            'transitions': [shot.get('transition') for shot in shots],
            'audio': self.asset_identity(p['id'], ch['audio']['path']),
            'duration': ch['audio']['duration'], 'video': v,
        })
        old = ch.get("render", {}).get("narrationRender", ch.get("render", {}))
        old_path = self.store.asset(p['id'], old['path']) if old.get('path') else None
        assets = [self.store.asset(p['id'], s['imagePath']) for s in shots]
        assets.append(self.store.asset(p['id'], ch['audio']['path']))
        if (
            old_path and old_path.is_file() and old_path.stat().st_size > 0
            and (old.get('renderIdentity') in (signature, previous_signature) or
                 old.get('signature') == legacy_signature and all(s['motion'] == 'static' for s in visual_shots) and self.legacy_is_current(old_path, assets))
        ):
            return old | {'renderIdentity': signature, 'downloadName': f"chapter-{ch['number']:03d}.mp4"}
        clip_jobs = []
        unique_jobs = {}
        durations = []
        for index, s in enumerate(shots):
            gate(f"Preparing shot {index+1} / {len(shots)}")
            # Round absolute boundaries, avoiding accumulated per-shot rounding drift.
            frames = max(1, round(s["end"] * fps) - round(s["start"] * fps))
            duration = frames / fps
            name = "clip-" + digest({"rendererVersion": 7, "shot": visual_shots[index], "video": v})[:24] + ".mp4"
            clip = folder / name
            previous = folder / ('clip-' + digest({'rendererVersion':6, 'shot':previous_visual_shots[index], 'video':v})[:24] + '.mp4')
            self.reuse_legacy_clip(previous, clip, [assets[index]])
            legacy = folder / ('clip-' + digest({'rendererVersion': 5, 'shot': s, 'video': v})[:24] + '.mp4')
            if visual_shots[index]['motion'] == 'static':
                self.reuse_legacy_clip(legacy, clip, [assets[index]])
            job = {
                'clip':str(clip),
                'args':[
                        '-filter_threads','2',
                        "-i",
                        str(self.store.asset(p["id"], s["imagePath"])),
                        "-vf",
                        self.motion(s, v, frames),
                        "-frames:v",
                        str(frames),
                        "-an",
                        *self.encoding(p),
                        str(clip.with_suffix('.partial.mp4')),
                    ],
            }
            # Identical pixels at different timeline positions share one clip.
            # Use the first immutable plan even if identical source bytes have
            # different asset names; no two workers write the same destination.
            unique_jobs.setdefault(name,job)
            clip_jobs.append((name,unique_jobs[name]))
            durations.append(duration)
        def render_clip(job, worker_gate):
            clip = Path(job['clip'])
            if not clip.is_file() or clip.stat().st_size == 0:
                self.run(job['args'],worker_gate,clip.with_suffix('.log'))
                clip.with_suffix('.partial.mp4').replace(clip)
            return clip
        workers = self.config.get('renderWorkers',2)
        clips = render_unique_clips(clip_jobs,render_clip,gate,workers=workers,
                                   capacity=lambda pending:self.render_capacity(v,pending))
        # Each incoming crossfade blends from the previous shot's final frame.
        # Only two clips are decoded at once, and every boundary keeps its own
        # transition. The fade occupies the incoming shot's first 250 ms, so
        # narration timing and total duration do not drift.
        assembled = []
        for index, clip in enumerate(clips):
            if index and shots[index].get("transition") == "crossfade":
                fade = min(0.25, durations[index] / 2)
                boundary = digest({'version': 1, 'previous': clips[index-1].name, 'incoming': clip.name,
                                   'fade': fade, 'duration': durations[index], 'video': v})
                target = folder / f"fade-{boundary[:24]}.mp4"
                self.reuse_legacy_clip(folder / f"fade-{legacy_signature[:12]}-{index}.mp4", target, [clips[index-1], clip])
                if not target.exists():
                    temporary = target.with_suffix(".partial.mp4")
                    filters = f"[0:v]trim=end_frame=1,setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration={fade},trim=duration={fade},settb=AVTB,fps={fps}[a];[1:v]setpts=PTS-STARTPTS,settb=AVTB,fps={fps}[b];[a][b]xfade=transition=fade:duration={fade}:offset=0[v]"
                    self.run(
                        [
                            "-sseof",
                            str(-1 / fps - 0.002),
                            "-i",
                            str(clips[index - 1]),
                            "-i",
                            str(clip),
                            "-filter_complex_threads",
                            "1",
                            "-filter_complex",
                            filters,
                            "-map",
                            "[v]",
                            "-t",
                            str(durations[index]),
                            "-an",
                            *self.encoding(p),
                            str(temporary),
                        ],
                        gate,
                        folder / "render.log",
                    )
                    temporary.replace(target)
                assembled.append(target)
            else:
                assembled.append(clip)
        visual_identity = digest({'version': 1, 'clips': [clip.name for clip in assembled]})
        visual = folder / f"visual-{visual_identity[:24]}.mp4"
        destination = folder / f"chapter-{ch['number']:03d}-{signature[:12]}.mp4"
        temp = destination.with_suffix(".partial.mp4")
        # Reuse existing silent assemblies. For new chapters, concatenate and
        # attach narration in one stream-copy pass, avoiding a second full video.
        if visual.is_file() and visual.stat().st_size:
            visual_input = ['-i',str(visual)]
        else:
            listing = self.concat_listing(assembled,destination.with_suffix('.concat.txt'))
            visual_input = ['-f','concat','-safe','0','-i',str(listing)]
        self.require_output_space(destination,
            visual.stat().st_size if visual.is_file() and visual.stat().st_size else sum(c.stat().st_size for c in assembled),
            ch['audio']['duration'])
        self.run(
            [
                *visual_input,
                "-i",
                str(self.store.asset(p["id"], ch["audio"]["path"])),
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-af",
                "apad",
                "-t",
                str(ch["audio"]["duration"]),
                "-movflags",
                "+faststart",
                str(temp),
            ],
            gate,
            folder / "render.log",
        )
        temp.replace(destination)
        return {
            "path": str(destination.relative_to(self.store.folder(p["id"]))).replace(
                "\\", "/"
            ),
            "signature": signature,
            "renderIdentity": signature,
            "duration": ch["audio"]["duration"],
            "created": time.time(),
            "downloadName": f"chapter-{ch['number']:03d}.mp4",
        }

    def watermark_export(self, p, result, gate):
        from studio_branding import identity, bitmap, coordinates
        branding = identity(p,self.store)
        if not branding.get('enabled', True): return result
        source = self.store.asset(p['id'],result['path'])
        signature = digest({'branding':branding,'source':self.asset_identity(p['id'],result['path']),
                            'video':p['settings']['video']})
        target = self.store.folder(p['id']) / ('watermarked-'+signature+'.mp4')
        if not target.exists():
            logo = target.with_suffix('.png')
            bitmap(p,self.store,logo)
            x,y = coordinates(p)
            self.require_output_space(target,source.stat().st_size*2,result['duration'])
            self.run(['-i',str(source),'-i',str(logo),'-filter_complex_threads','1',
                      '-filter_complex',f'[0:v][1:v]overlay=x={x}:y={y}:eof_action=repeat:format=auto[v]',
                      '-map','[v]','-map','0:a?','-c:a','copy',*self.encoding(p),
                      '-movflags','+faststart',str(target)],gate,target.with_suffix('.log'))
        return result | {'path':target.name,'signature':signature,'renderIdentity':signature,
                         'watermarkIdentity':branding,'narrationRender':result}

    def chapter_export(self, p, ch, gate):
        base = self.chapter(p, ch, gate)
        if not p["intro"]["enabled"] or p["intro"]["placement"] != "every_chapter":
            return self.engagement_export(p,base,gate,namespace=ch['id'])
        single = p | {
            "chapters": [ch],
            "intro": p["intro"] | {"placement": "full_story_only"},
        }
        result = self.full(single, gate)
        return result | {
            "downloadName": base["downloadName"],
            "introDuration": p["intro"]["duration"],
            "narrationRender": base,
        }

    def intro(self, p, gate):
        path = self.raw_intro(p,gate)
        result = self.watermark_export(p,{'path':path.name,'duration':p['intro']['duration']},gate)
        return self.store.asset(p['id'],result['path'])

    def raw_intro(self, p, gate):
        intro = p["intro"]
        v = p["settings"]["video"]
        duration = float(intro["duration"])
        if not 10 <= duration <= 30:
            raise ValueError("Intro duration must be 10–30 seconds.")
        if intro.get('audioPath'):
            with wave.open(str(self.store.asset(p['id'],intro['audioPath'])), 'rb') as narration:
                spoken_duration = narration.getnframes()/narration.getframerate()
            if spoken_duration > duration + .02:
                raise ValueError(f'Intro narration lasts {spoken_duration:.1f}s, longer than the {duration:g}s intro. Shorten its text or regenerate at a faster speaking speed; narration will not be cut off.')
        signature = digest(
            {
                "introRendererVersion": 6,
                "intro": {
                    k: intro.get(k)
                    for k in (
                        "duration",
                        "title",
                        "subtitle",
                        "visualPath",
                        "audioPath",
                        "motion",
                        "showTitle",
                        "shots",
                    )
                },
                "video": v,
            }
        )
        folder = self.store.folder(p["id"])
        destination = folder / f"intro-{signature[:12]}.mp4"
        if destination.exists():
            return destination
        montage = None
        if intro.get('shots'):
            clips, lengths = [], []
            cursor = 0.0
            for index, shot in enumerate(intro['shots']):
                if shot.get('qc', {}).get('status') == 'PENDING':
                    raise ValueError('Finish pending intro image quality checks before rendering.')
                if qc_decision(p,shot,self.store)['blocking']:
                    raise ValueError(f'Intro shot {index+1} needs review. Accept it or request a repair before rendering.')
                start, end = float(shot['start']), float(shot['end'])
                if not all(math.isfinite(x) for x in (start, end)) or abs(start-cursor) > .02 or end <= start or end > duration+.02:
                    raise ValueError('Intro shots must cover its duration in order without gaps or overlaps.')
                image = self.store.asset(p['id'], shot.get('imagePath', ''))
                if not image.is_file():
                    raise ValueError(f'Generate intro shot {index+1} before rendering. No previous intro was replaced.')
                seconds = end-start
                clip = folder / ('intro-shot-' + digest({'version': 2, 'shot': shot, 'video': v})[:20] + '.mp4')
                if not clip.is_file():
                    visual = self.motion(shot | {'manual': {'motion': True}}, v | {'imageFit': 'cover'}, round(seconds*v['fps']))
                    self.run(['-loop', '1', '-i', str(image), '-vf', visual, '-an', '-t', str(seconds), *self.encoding(p), str(clip)], gate, folder / f'intro-shot-{index+1}.log')
                clips.append(clip)
                lengths.append(seconds)
                cursor = end
            if abs(cursor-duration) > .02:
                raise ValueError('Intro shots must reach the end of the intro narration timeline.')
            montage = folder / ('intro-montage-' + signature[:12] + '.mp4')
            if not montage.is_file():
                self.concat(clips, montage, gate, folder / 'intro-montage.log', lengths, video_only=True)
        title_filter = ''
        if intro.get('showTitle', False):
            title = folder / 'intro-title.txt'
            title.write_text(intro.get('title', '') + '\n' + intro.get('subtitle', ''), encoding='utf-8')
            textpath = str(title).replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
            font = self.config.get('font', 'C:/Windows/Fonts/arial.ttf').replace('\\', '/').replace(':', '\\:')
            title_filter = f",drawtext=fontfile='{font}':textfile='{textpath}':fontcolor=white:fontsize=48:borderw=2:bordercolor=black:box=1:boxcolor=black@0.60:boxborderw=18:x=(w-text_w)/2:y=(h-text_h)/2"
        inputs = (
            ["-loop", "1", "-i", str(self.store.asset(p["id"], intro["visualPath"]))]
            if intro.get("visualPath")
            else [
                "-f",
                "lavfi",
                "-i",
                f"color=c=0x171c23:s={v['width']}x{v['height']}:r={v['fps']}",
            ]
        )
        if montage:
            inputs = ['-i', str(montage)]
        inputs += (
            ["-i", str(self.store.asset(p["id"], intro["audioPath"]))]
            if intro.get("audioPath")
            else ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
        )
        visual = self.motion({'start':0, 'end':duration, 'motion':intro.get('motion','static'),
            'camera':{'shot':'wide'}, 'manual':{'motion':True}}, v | {'imageFit':'cover'}, round(duration*v['fps']))
        if montage:
            visual = f"fps={v['fps']},setsar=1"
        filters = visual + title_filter + f",fade=t=in:st=0:d=0.5,fade=t=out:st={duration-.5}:d=0.5"
        self.run(
            [
                *inputs,
                "-vf",
                filters,
                "-map",
                "0:v",
                "-map",
                "1:a",
                "-af",
                "apad",
                "-t",
                str(duration),
                *self.encoding(p),
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "24000",
                "-ac",
                "1",
                "-movflags",
                "+faststart",
                str(destination),
            ],
            gate,
            folder / "intro-render.log",
        )
        return destination

    def full(self, p, gate):
        files = []
        audios = []
        durations = []
        duration = 0

        def add_intro():
            nonlocal duration
            visual = self.raw_intro(p, gate)
            files.append(visual)
            durations.append(p["intro"]["duration"])
            duration += p["intro"]["duration"]
            audio = visual.with_suffix(".wav")
            if not audio.exists():
                inputs = (
                    ["-i", str(self.store.asset(p["id"], p["intro"]["audioPath"]))]
                    if p["intro"].get("audioPath")
                    else ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
                )
                self.run(
                    [
                        *inputs,
                        "-af",
                        "apad",
                        "-t",
                        str(p["intro"]["duration"]),
                        "-c:a",
                        "pcm_s16le",
                        "-ar",
                        "24000",
                        "-ac",
                        "1",
                        str(audio),
                    ],
                    gate,
                    self.store.folder(p["id"]) / "intro-audio-render.log",
                )
            audios.append(audio)

        if p["intro"]["enabled"] and p["intro"]["placement"] == "full_story_only":
            add_intro()
        for ch in p["chapters"]:
            if not ch["sourceText"].strip():
                continue
            result = self.chapter(p, ch, gate)

            chapter_result = self.watermark_export(p,result,gate)
            def chapter_saved(latest):
                c = next(c for c in latest["chapters"] if c["id"] == ch["id"])
                if (
                    c.get("render", {}).get("path")
                    and c["render"]["path"] != chapter_result["path"]
                ):
                    c.setdefault("renderHistory", []).append(c["render"])
                c.update(render=chapter_result, renderStale=bool(p["intro"]["enabled"] and p["intro"]["placement"] == "every_chapter"))

            self.store.mutate(p["id"], chapter_saved)
            if p["intro"]["enabled"] and p["intro"]["placement"] == "every_chapter":
                add_intro()
            files.append(self.store.asset(p["id"], result["path"]))
            audios.append(self.store.asset(p["id"], ch["audio"]["path"]))
            durations.append(result["duration"])
            duration += result["duration"]
        if not files:
            raise ValueError("Add and generate a chapter first.")
        signature = digest(
            {
                "fullRendererVersion": 2,
                "files": [
                    str(f.relative_to(self.store.folder(p["id"]))) for f in files
                ],
                "video": p["settings"]["video"],
            }
        )
        target = self.store.folder(p["id"]) / f"story-{signature[:12]}.mp4"
        if not target.exists():
            # Join original WAV narration once. Copying separate chapter AAC tracks
            # can accumulate encoder padding at chapter boundaries.
            visual = target.with_name(target.stem + "-visual.mp4")
            log = self.store.folder(p["id"]) / "full-render.log"
            if not visual.exists():
                self.concat(files, visual, gate, log, durations, video_only=True)
            listing = target.with_suffix(".audio.txt")
            listing.write_text(
                "\n".join(
                    "file '"
                    + str(f.resolve()).replace("\\", "/").replace("'", "'\\''")
                    + "'"
                    for f in audios
                ),
                encoding="utf-8",
            )
            temporary = target.with_suffix(".partial.mp4")
            self.run(
                [
                    "-i",
                    str(visual),
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(listing),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-af",
                    "apad",
                    "-t",
                    str(duration),
                    "-movflags",
                    "+faststart",
                    str(temporary),
                ],
                gate,
                log,
            )
            temporary.replace(target)
        return self.engagement_export(p,{
            "path": target.name,
            "duration": duration,
            "signature": signature,
            "downloadName": p["name"] + ".mp4",
            "created": time.time(),
        },gate)

    def engagement_export(self, p, result, gate, namespace='full'):
        from studio_engagement import settings, schedule, card, ding, audio_signature
        s=settings(p)
        if s['outroEnabled'] and callable(getattr(self,'prepare_outro',None)):
            self.prepare_outro(p); s=settings(p)
        events=schedule(p,result['duration'],namespace)
        if not events and not s['outroEnabled']:
            final=self.watermark_export(p,result,gate)
            if s['splitEnabled']:final=final|{'parts':self.split_export(p,final,gate,s['partMinutes'])}
            return final
        from studio_branding import identity as branding_identity, bitmap, coordinates
        branding=branding_identity(p,self.store)
        brand=branding.get('enabled',True) and result.get('watermarkIdentity')!=branding
        folder=self.store.folder(p['id']); source=self.store.asset(p['id'],result['path'])
        identity={'version':2,'source':self.asset_identity(p['id'],result['path']),
                  'settings':s,'events':events,'video':p['settings']['video'],'namespace':namespace,'branding':branding}
        if s['outroEnabled']:
            if not s['outroAudioPath'] or s['outroAudioSignature']!=audio_signature(p):
                raise ValueError('Prepare the updated outro narration before rendering. Chapter audio is unchanged.')
            audio=self.store.asset(p['id'],s['outroAudioPath'])
            identity['outroAudio']=self.asset_identity(p['id'],s['outroAudioPath'])
        signature=digest(identity); duration=result['duration']
        decorated=source
        if events:
            decorated=folder/f'reminders-{signature[:20]}.mp4'
            if not decorated.exists():
                logo=decorated.with_suffix('.png'); card(p,logo)
                inputs=['-i',str(source),'-i',str(logo)]
                enabled='+'.join(f'between(t,{e["start"]},{e["end"]})' for e in events)
                x='main_w-overlay_w-24' if s['position']=='bottom-right' else '24'
                filters=[f"[0:v][1:v]overlay=x={x}:y=main_h-overlay_h-24:eof_action=repeat:enable='{enabled}'[{ 'reminded' if brand else 'v' }]"]
                next_input=2
                if brand:
                    mark=decorated.with_name(decorated.stem+'-watermark.png');bitmap(p,self.store,mark);bx,by=coordinates(p)
                    inputs+=['-i',str(mark)];next_input+=1
                    filters.append(f'[reminded][2:v]overlay=x={bx}:y={by}:eof_action=repeat:format=auto[v]')
                audio_map='0:a:0'
                if s['dingEnabled'] and s['dingVolume']>0:
                    bell=decorated.with_suffix('.wav'); ding(bell,s['dingVolume']); inputs+=['-i',str(bell)]
                    filters.append(f'[{next_input}:a]asplit='+str(len(events))+''.join(f'[bell{i}]' for i in range(len(events))))
                    segments=[]; cursor=0
                    for i,e in enumerate(events):
                        gap=max(0,e['start']-cursor)
                        filters += [f'anullsrc=r=24000:cl=mono:d={gap}[sil{i}]',
                                    f'[bell{i}]atrim=duration=0.6,asetpts=PTS-STARTPTS[tone{i}]']
                        segments += [f'[sil{i}]',f'[tone{i}]']; cursor=e['start']+.6
                    filters.append(f'anullsrc=r=24000:cl=mono:d={max(0,duration-cursor)}[tail]')
                    segments.append('[tail]')
                    filters.append(''.join(segments)+f'concat=n={len(segments)}:v=0:a=1[chimes]')
                    filters.append('[0:a][chimes]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:level=0[a]')
                    audio_map='[a]'
                graph=decorated.with_suffix('.filters.txt'); graph.write_text(';'.join(filters),encoding='utf-8')
                self.require_output_space(decorated,source.stat().st_size*2,duration)
                self.run([*inputs,'-filter_complex_threads','1','-filter_complex_script',str(graph),
                    '-map','[v]','-map',audio_map,*self.encoding(p),'-c:a','aac','-b:a','128k',
                    '-t',str(duration),'-movflags','+faststart',str(decorated)],gate,decorated.with_suffix('.log'))
        elif brand:
            decorated=self.store.asset(p['id'],self.watermark_export(p,result,gate)['path'])
        target=decorated
        if s['outroEnabled']:
            outro=folder/f'outro-{signature[:20]}.mp4'
            with wave.open(str(audio),'rb') as wav: spoken=wav.getnframes()/wav.getframerate()
            outro_duration=max(s['outroDuration'],spoken+.5)
            if not outro.exists():
                visual=outro.with_suffix('.png'); card(p,visual,outro=True)
                inputs=['-loop','1','-i',str(visual),'-i',str(audio)]
                if branding.get('enabled',True):
                    mark=outro.with_name(outro.stem+'-watermark.png');bitmap(p,self.store,mark);bx,by=coordinates(p)
                    inputs+=['-i',str(mark),'-filter_complex_threads','1','-filter_complex',f'[0:v][2:v]overlay=x={bx}:y={by}:eof_action=repeat:format=auto[v]', '-map','[v]','-map','1:a:0']
                self.run([*inputs,'-af','apad',
                    '-t',str(outro_duration),*self.encoding(p),'-c:a','aac','-b:a','128k',
                    '-ar','24000','-ac','1','-movflags','+faststart',str(outro)],gate,outro.with_suffix('.log'))
            target=folder/f'complete-{signature[:20]}.mp4'
            if not target.exists(): self.concat([decorated,outro],target,gate,target.with_suffix('.log'),[duration,outro_duration])
            duration+=outro_duration
        final=result | {'path':target.name,'duration':duration,'signature':signature,
                         'engagementEvents':events,'engagementIdentity':identity,'narrationRender':result,'watermarkIdentity':branding}
        if s['splitEnabled']:
            final['parts']=self.split_export(p,final,gate,s['partMinutes'])
        return final

    def split_export(self,p,result,gate,minutes):
        """Keyframe-aware stream-copy parts: no new image/director/audio calls."""
        source=self.store.asset(p['id'],result['path']); length=float(minutes)*60
        signature=digest({'source':self.asset_identity(p['id'],result['path']),'minutes':minutes,'version':1})
        folder=self.store.folder(p['id'])/('parts-'+signature[:16]); folder.mkdir(exist_ok=True)
        receipt=folder/'parts.json'
        if receipt.exists():
            saved=json.loads(receipt.read_text(encoding='utf-8'))
            if all(self.store.asset(p['id'],x['path']).is_file() for x in saved): return saved
        # FFmpeg segment muxer chooses existing keyframes near the requested time.
        # Report measured boundaries, not an exact two-hour promise.
        staging=folder/'working'; staging.mkdir(exist_ok=True)
        pattern=staging/'part-%03d.mp4'; listing=staging/'segments.csv'
        self.require_output_space(folder/'parts.json',source.stat().st_size)
        self.run(['-i',str(source),'-map','0','-c','copy','-f','segment','-segment_time',str(length),
            '-reset_timestamps','1','-segment_list',str(listing),'-segment_list_type','csv',str(pattern)],gate,folder/'split.log',multiple_outputs=True)
        import csv
        parts=[]
        for i,row in enumerate(csv.reader(listing.read_text(encoding='utf-8').splitlines()),1):
            file=folder/Path(row[0]).name
            (staging/file.name).replace(file)
            parts.append({'number':i,'path':file.relative_to(self.store.folder(p['id'])).as_posix(),
                'start':float(row[1]),'end':float(row[2]),'duration':float(row[2])-float(row[1]),
                'downloadName':f'{p["name"]}-part-{i:02d}.mp4','keyframeAligned':True})
        temp=receipt.with_suffix('.tmp'); temp.write_text(json.dumps(parts),encoding='utf-8'); temp.replace(receipt)
        return parts
