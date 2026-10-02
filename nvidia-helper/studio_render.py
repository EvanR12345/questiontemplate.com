"""Bounded-memory FFmpeg assembly with cached chapters and separate intro."""

import json, math, subprocess, time, wave
from pathlib import Path
from studio_data import digest


def effective_motion(shot, video):
    """Project motion adds movement to AI holds, preserving explicit shot edits."""
    motion = shot.get("motion", "static").lower()
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

    def executable(self):
        path = Path(self.config.get("ffmpeg", ""))
        if not path.is_file():
            raise RuntimeError(
                "FFmpeg is unavailable. Set its existing installation path in Advanced settings. No chapter assets were changed."
            )
        return str(path)

    def run(self, args, gate, log):
        # Cache names become visible only after FFmpeg succeeds. A cancelled
        # intro, WAV or concatenation must never be reused as a complete asset.
        destination = Path(args[-1])
        staging = destination.with_name(
            destination.stem + ".writing" + destination.suffix
        )
        staged_args = [*args[:-1], str(staging)]
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
            except Exception:
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
        staging.replace(destination)

    def encoding(self, p):
        v = p["settings"]["video"]
        if (
            v["width"] not in (640, 1280, 1920)
            or v["height"] not in (360, 720, 1080)
            or v["fps"] not in (24, 25, 30)
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
        x = "iw/2-(iw/zoom/2)"
        y = "ih/2-(ih/zoom/2)"
        if "zoom in" in motion:
            zoom = f"1+0.06*on/{interval}"
        elif "zoom out" in motion:
            zoom = f"1.06-0.06*on/{interval}"
        elif "pan left" in motion:
            zoom = "1.08"
            x = f"(iw-iw/zoom)*(1-on/{interval})"
        elif "pan right" in motion:
            zoom = "1.08"
            x = f"(iw-iw/zoom)*on/{interval}"
        elif "pan up" in motion:
            zoom = "1.08"
            y = f"(ih-ih/zoom)*(1-on/{interval})"
        elif "pan down" in motion:
            zoom = "1.08"
            y = f"(ih-ih/zoom)*on/{interval}"
        fitting = (
            f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,crop={w*2}:{h*2}"
            if v.get("imageFit") == "cover"
            else f"scale={w*2}:{h*2}:force_original_aspect_ratio=decrease,pad={w*2}:{h*2}:(ow-iw)/2:(oh-ih)/2:color=0x101720"
        )
        return f"{fitting},zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={v['fps']},setsar=1"

    def concat(self, files, target, gate, log, durations=None, video_only=False):
        listing = target.with_suffix(".concat.txt")
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
        signature = digest(
            {"rendererVersion": 3, "shots": shots, "audio": ch["audio"], "video": v}
        )
        old = ch.get("render", {}).get("narrationRender", ch.get("render", {}))
        if (
            old.get("signature") == signature
            and self.store.asset(p["id"], old["path"]).is_file()
        ):
            return old
        clips = []
        durations = []
        for index, s in enumerate(shots):
            gate(f"Rendering shot {index+1} / {len(shots)}")
            # Round absolute boundaries, avoiding accumulated per-shot rounding drift.
            frames = max(1, round(s["end"] * fps) - round(s["start"] * fps))
            duration = frames / fps
            name = "clip-" + digest({"rendererVersion": 3, "shot": s, "video": v})[:24] + ".mp4"
            clip = folder / name
            if not clip.exists():
                temporary = clip.with_suffix(".partial.mp4")
                self.run(
                    [
                        "-i",
                        str(self.store.asset(p["id"], s["imagePath"])),
                        "-vf",
                        self.motion(s, v, frames),
                        "-frames:v",
                        str(frames),
                        "-an",
                        *self.encoding(p),
                        str(temporary),
                    ],
                    gate,
                    folder / "render.log",
                )
                temporary.replace(clip)
            clips.append(clip)
            durations.append(duration)
        # Each incoming crossfade blends from the previous shot's final frame.
        # Only two clips are decoded at once, and every boundary keeps its own
        # transition. The fade occupies the incoming shot's first 250 ms, so
        # narration timing and total duration do not drift.
        assembled = []
        for index, clip in enumerate(clips):
            if index and shots[index].get("transition") == "crossfade":
                fade = min(0.25, durations[index] / 2)
                target = folder / f"fade-{signature[:12]}-{index}.mp4"
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
        visual = folder / f"visual-{signature[:12]}.mp4"
        if not visual.exists():
            self.concat(assembled, visual, gate, folder / "render.log")
        destination = folder / f"chapter-{ch['number']:03d}-{signature[:12]}.mp4"
        temp = destination.with_suffix(".partial.mp4")
        self.run(
            [
                "-i",
                str(visual),
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
            "duration": ch["audio"]["duration"],
            "created": time.time(),
            "downloadName": f"chapter-{ch['number']:03d}.mp4",
        }

    def chapter_export(self, p, ch, gate):
        base = self.chapter(p, ch, gate)
        if not p["intro"]["enabled"] or p["intro"]["placement"] != "every_chapter":
            return base
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
        intro = p["intro"]
        v = p["settings"]["video"]
        duration = float(intro["duration"])
        if not 10 <= duration <= 20:
            raise ValueError("Intro duration must be 10â€“20 seconds.")
        signature = digest(
            {
                "intro": {
                    k: intro.get(k)
                    for k in (
                        "duration",
                        "title",
                        "subtitle",
                        "visualPath",
                        "audioPath",
                        "motion",
                    )
                },
                "video": v,
            }
        )
        folder = self.store.folder(p["id"])
        destination = folder / f"intro-{signature[:12]}.mp4"
        if destination.exists():
            return destination
        title = folder / "intro-title.txt"
        title.write_text(
            intro.get("title", p["name"]) + "\n" + intro.get("subtitle", ""),
            encoding="utf-8",
        )
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
        inputs += (
            ["-i", str(self.store.asset(p["id"], intro["audioPath"]))]
            if intro.get("audioPath")
            else ["-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono"]
        )
        textpath = str(title).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
        font = (
            self.config.get("font", "C:/Windows/Fonts/arial.ttf")
            .replace("\\", "/")
            .replace(":", "\\:")
        )
        filters = f"scale={v['width']}:{v['height']}:force_original_aspect_ratio=increase,crop={v['width']}:{v['height']},drawtext=fontfile='{font}':textfile='{textpath}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2,fade=t=in:st=0:d=0.5,fade=t=out:st={duration-.5}:d=0.5"
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
            visual = self.intro(p, gate)
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

            def chapter_saved(latest):
                c = next(c for c in latest["chapters"] if c["id"] == ch["id"])
                if (
                    c.get("render", {}).get("path")
                    and c["render"]["path"] != result["path"]
                ):
                    c.setdefault("renderHistory", []).append(c["render"])
                c.update(render=result, renderStale=False)

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
        return {
            "path": target.name,
            "duration": duration,
            "signature": signature,
            "downloadName": p["name"] + ".mp4",
            "created": time.time(),
        }
