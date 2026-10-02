"""Persistent production jobs: audio, directing, shot generation, QC and renders."""

import base64, copy, io, json, os, re, secrets, sqlite3, threading, time, traceback, wave
from pathlib import Path
from studio_data import *
from director_provider import LocalQwenDirector
from openai_director import OpenAIDirector
from image_provider import (
    ExistingImageProvider,
    NativeFluxProvider,
    ComfyImageProvider,
    format_prompt,
    select_references,
    data_url,
)
from studio_render import VideoRenderer


class JobCancelled(Exception):
    pass


class AudioYield(Exception):
    pass


class StudioService:
    def __init__(
        self,
        root,
        audio,
        image_factory,
        gpu_lock,
        before_audio,
        before_image,
        legacy_queue,
    ):
        self.store = ProjectStore(root)
        self.audio = audio
        self.gpu_lock = gpu_lock
        self.before_audio = before_audio
        self.before_image = before_image
        self.legacy_queue = legacy_queue
        self.config_path = Path(__file__).resolve().parent / "studio-config.json"
        self.config = (
            json.loads(self.config_path.read_text(encoding="utf-8"))
            if self.config_path.exists()
            else {}
        )
        self.director = LocalQwenDirector(self.config, self.store.root)
        self.providers = {
            "existing": ExistingImageProvider(image_factory),
            "native-flux": NativeFluxProvider(self.config, self.store.root),
            "comfyui": ComfyImageProvider(self.config),
        }
        self.renderer = VideoRenderer(self.store, self.config)
        self.cv = threading.Condition(threading.RLock())
        self.current = None
        self.cancel = False
        self.yield_requested = False
        self.closed = False
        self.db = sqlite3.connect(
            self.store.root / "jobs.sqlite3", check_same_thread=False
        )
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,project TEXT,chapter TEXT,shot TEXT,kind TEXT,payload TEXT,status TEXT,message TEXT,priority INTEGER,created REAL,started REAL,seconds REAL,attempt INTEGER,seed INTEGER)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT)"
        )
        recovered = self.db.execute(
            "UPDATE jobs SET status='QUEUED',message='Helper restarted. Resume restarts this item using saved assets and seed.' WHERE status='RUNNING'"
        ).rowcount
        row = self.db.execute(
            "SELECT value FROM settings WHERE key='paused'"
        ).fetchone()
        self.paused = bool(recovered or row and row[0] == "1")
        self.db.commit()
        self.sync_queue_states()
        self.thread = threading.Thread(
            target=self.worker, daemon=True, name="story-production"
        )
        self.thread.start()

    def sync_queue_states(self):
        """Restore persistent item states without touching completed image assets."""
        with self.cv:
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT project,chapter,shot,status FROM jobs ORDER BY created"
                )
            ]
        latest = {(r["project"], r["chapter"], r["shot"]): r for r in rows}
        byproject = {}
        for r in latest.values():
            if r["chapter"]:
                byproject.setdefault(r["project"], []).append(r)
        for pid, items in byproject.items():

            def update(p):
                affected = set()
                for item in items:
                    try:
                        c = get_chapter(p, item["chapter"])
                    except StopIteration:
                        continue
                    state = item["status"]
                    affected.add(c["id"])
                    if item["shot"]:
                        try:
                            s = get_shot(p, c["id"], item["shot"])
                        except StopIteration:
                            continue
                        if state != "COMPLETE":
                            s["status"] = (
                                "PAUSED"
                                if state == "QUEUED" and self.paused
                                else "GENERATING" if state == "RUNNING" else state
                            )
                    elif state != "COMPLETE":
                        c["status"] = (
                            "PAUSED"
                            if state == "QUEUED" and self.paused
                            else "ANALYZING" if state == "RUNNING" else state
                        )
                for cid in affected:
                    c = get_chapter(p, cid)
                    active = [
                        r
                        for r in items
                        if r["chapter"] == cid and r["status"] in ("QUEUED", "RUNNING")
                    ]
                    if active:
                        c["status"] = (
                            "PAUSED"
                            if self.paused
                            else (
                                "GENERATING"
                                if any(r["status"] == "RUNNING" for r in active)
                                else "QUEUED"
                            )
                        )
                    elif c.get("scenes"):
                        shots = [s for sc in c["scenes"] for s in sc["shots"]]
                        if shots and all(
                            s.get("imagePath") and s["status"] in ("COMPLETE", "PASSED")
                            for s in shots
                        ):
                            c["status"] = "COMPLETE"

            try:
                self.store.mutate(pid, update)
            except FileNotFoundError:
                pass

    def unload_models(self):
        self.director.stop()
        self.providers["native-flux"].unload()

    def health(self):
        import shutil, subprocess

        hardware = {"gpu": self.audio.gpu, "vramGB": 4, "ramGB": 8}
        try:
            values = (
                subprocess.check_output(
                    [
                        "nvidia-smi",
                        "--query-gpu=memory.total,memory.free",
                        "--format=csv,noheader,nounits",
                    ],
                    text=True,
                )
                .splitlines()[0]
                .split(",")
            )
            hardware.update(
                vramGB=round(float(values[0]) / 1024, 1),
                freeVramGB=round(float(values[1]) / 1024, 2),
            )
        except Exception:
            pass
        try:
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
                    (n, ctypes.c_ulonglong)
                    for n in (
                        "totalPhys",
                        "availPhys",
                        "totalPage",
                        "availPage",
                        "totalVirtual",
                        "availVirtual",
                        "availExtended",
                    )
                ]

            mem = MEMORYSTATUSEX()
            mem.length = ctypes.sizeof(mem)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            hardware.update(
                ramGB=round(mem.totalPhys / 2**30, 1),
                freeRamGB=round(mem.availPhys / 2**30, 2),
            )
        except Exception:
            pass
        hardware["diskFreeGB"] = round(
            shutil.disk_usage(self.store.root).free / 2**30, 1
        )
        return {
            "studioProtocol": 1,
            "hardware": hardware,
            "director": self.director.healthCheck(),
            "directorProviders": {
                "local-qwen": LocalQwenDirector(self.config, self.store.root).healthCheck(),
                "openai-luna": OpenAIDirector(self.config, self.store.root).healthCheck(),
            },
            "providers": {
                k: v.healthCheck()
                | {
                    "capabilities": v.getCapabilities(),
                    "recommended": v.getRecommendedSettings(),
                }
                for k, v in self.providers.items()
            },
            "renderer": {"installed": Path(self.config.get("ffmpeg", "")).is_file()},
            "outputFolder": str(self.store.root),
            "queue": self.snapshot(),
        }

    def configure(self, data):
        allowed = {
            "directorExecutable",
            "directorModel",
            "directorProjector",
            "directorGpuLayers",
            "directorDevice",
            "directorEndpoint",
            "imageExecutable",
            "imageBackend",
            "fluxModel",
            "fluxEncoder",
            "fluxVae",
            "comfyEndpoint",
            "comfyWorkflow",
            "ffmpeg",
            "font",
            "openaiApiKey",
        }
        if not isinstance(data, dict) or any(k not in allowed for k in data):
            raise ValueError("Unknown helper configuration option.")
        with self.cv:
            if self.current:
                raise ValueError(
                    "Pause and cancel the current operation before changing runtime paths."
                )
            self.unload_models()
            data = dict(data)
            if "openaiApiKey" in data:
                key = str(data.pop("openaiApiKey")).strip()
                if key and (not key.startswith("sk-") or len(key) < 20):
                    raise ValueError("Enter a valid OpenAI API key.")
                path = self.config_path.with_name(".studio-secrets.json")
                path.write_text(json.dumps({"openaiApiKey": key}), encoding="utf-8")
                self.config["openaiKeyFile"] = str(path)
            self.config.update(data)
            self.config_path.write_text(
                json.dumps(self.config, indent=2), encoding="utf-8"
            )
            self.director.config = self.config
            self.providers["native-flux"].config = self.config
            self.providers["comfyui"] = ComfyImageProvider(self.config)
            self.renderer.config = self.config
        return self.health()

    def snapshot(self):
        with self.cv:
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,project,chapter,shot,kind,status,message,priority,created,started,seconds,attempt,seed FROM jobs WHERE status IN ('QUEUED','RUNNING') OR id IN (SELECT id FROM jobs ORDER BY created DESC LIMIT 1100) ORDER BY created DESC"
                )
            ]
            counts = {
                s: 0 for s in ("QUEUED", "RUNNING", "COMPLETE", "FAILED", "CANCELLED")
            }
            project_counts = {}
            for group in self.db.execute(
                "SELECT project,kind,status,count(*) AS total FROM jobs GROUP BY project,kind,status"
            ):
                counts[group["status"]] += group["total"]
                project = project_counts.setdefault(
                    group["project"], {"all": {}, "image": {}}
                )
                project["all"][group["status"]] = (
                    project["all"].get(group["status"], 0) + group["total"]
                )
                if group["kind"] == "image":
                    project["image"][group["status"]] = group["total"]
            averages = {}
            for kind in {j["kind"] for j in rows}:
                durations = [
                    r["seconds"]
                    for r in rows
                    if r["kind"] == kind
                    and r["status"] == "COMPLETE"
                    and r["seconds"] > 0
                ][:30]
                if durations:
                    averages[kind] = sum(durations) / len(durations)
            pending = [r for r in rows if r["status"] in ("QUEUED", "RUNNING")]
            estimate = sum(
                max(
                    0,
                    averages.get(r["kind"], 0)
                    - (
                        time.time() - r["started"]
                        if r["status"] == "RUNNING" and r["started"]
                        else 0
                    ),
                )
                for r in pending
            )
            return {
                "paused": self.paused,
                "current": self.current,
                "counts": counts,
                "projectCounts": project_counts,
                "etaSeconds": (
                    round(estimate)
                    if all(
                        r["kind"] in averages and r["kind"] != "produce-story"
                        for r in pending
                    )
                    else None
                ),
                "averageSecondsByKind": averages,
                "jobs": rows,
                "outputFolder": str(self.store.root),
            }

    def enqueue(self, pid, chapter, kind, shots=None, options=None):
        p = self.store.load(pid)
        options = options or {}
        ch = get_chapter(p, chapter) if chapter else None
        if kind not in (
            "produce-story",
            "analyze",
            "scene-plan",
            "narration",
            "image",
            "qc",
            "character-reference",
            "render-chapter",
            "render-full",
            "intro-audio",
            "intro-image",
            "intro-render",
        ):
            raise ValueError("Unknown studio operation.")
        if kind == "produce-story":
            if not any(c["sourceText"].strip() for c in p["chapters"]):
                raise ValueError(
                    "Paste at least one chapter before generating the full video."
                )
            chapter = None
            ch = None
        with self.cv:
            active_run = self.db.execute(
                "SELECT id,kind FROM jobs WHERE project=? AND status IN ('QUEUED','RUNNING')",
                (pid,),
            ).fetchall()
            if kind == "produce-story" and active_run:
                if all(j["kind"] == "produce-story" for j in active_run):
                    return self.snapshot()
                raise ValueError(
                    "Finish or cancel this project's existing jobs before starting its full video."
                )
            if kind != "produce-story" and any(
                j["kind"] == "produce-story" for j in active_run
            ):
                raise ValueError(
                    "The full-video run is processing this project. Pause/cancel it before submitting separate jobs."
                )
        ids = shots if kind in ("image", "qc") else [None]
        if not ids or len(ids) > 1000:
            raise ValueError("Submit 1–1000 shots.")
        planned = []
        for sid in ids:
            shot = get_shot(p, chapter, sid) if sid else None
            if shot:
                if any(
                    c["type"] == "main" and not c.get("accepted")
                    for c in ch["people"]
                    if any(s["id"] == c["id"] for s in shot["characters"])
                ):
                    raise ValueError(
                        "Confirm detected main characters in Characters, or classify them as supporting/temporary, before generating their shots."
                    )
                scene = next(s for s in ch["scenes"] if s["id"] == shot["sceneId"])
                if (
                    p["settings"]["appearanceHandling"] != "Automatic"
                    and scene.get("appearanceChanges")
                    and not scene.get("appearanceChangesReviewed")
                ):
                    raise ValueError(
                        "Review and accept this scene’s planned appearance changes before generating images."
                    )
                provider = self.provider(
                    shot.get("imageProvider") or p["settings"]["image"]["provider"]
                )
                settings = provider.validateSettings(
                    shot["generationSettings"] | {"model": shot["imageModel"]}
                )
                shot["generationSettings"] = settings
                seed = settings["seed"]
            else:
                seed = None
            planned.append(
                (
                    uid("job-"),
                    sid,
                    seed,
                    options | ({"shotSnapshot": copy.deepcopy(shot)} if shot else {}),
                )
            )
        with self.cv:
            count = self.db.execute(
                "SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')"
            ).fetchone()[0]
            if count + len(ids) > 1000:
                raise ValueError(
                    "The production queue has room for 1000 unfinished jobs."
                )
            for id, sid, seed, payload in planned:
                exists = self.db.execute(
                    "SELECT id FROM jobs WHERE project=? AND chapter=? AND kind=? AND shot IS ? AND status IN ('QUEUED','RUNNING')",
                    (pid, chapter, kind, sid),
                ).fetchone()
                if exists:
                    continue
                self.db.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        id,
                        pid,
                        chapter,
                        sid,
                        kind,
                        json.dumps(payload),
                        "QUEUED",
                        "Waiting",
                        int(options.get("priority", 0)),
                        time.time(),
                        None,
                        0,
                        0,
                        seed,
                    ),
                )
            self.db.commit()
            self.cv.notify_all()
        if kind == "image":

            def mark(latest):
                c = get_chapter(latest, chapter)
                c["status"] = "QUEUED"
                for _, sid, seed, _ in planned:
                    s = get_shot(latest, chapter, sid)
                    s["generationSettings"]["seed"] = seed
                    s["status"] = "QUEUED"

            self.store.mutate(pid, mark)
        self.sync_queue_states()
        return self.snapshot()

    def provider(self, id):
        if id not in self.providers:
            raise ValueError("Unknown image provider: " + str(id))
        return self.providers[id]

    def select_director(self, project):
        selected = project["settings"]["director"].get("provider", "local-qwen")
        if selected not in ("local-qwen", "openai-luna"):
            raise ValueError("Unknown director provider. Select local Qwen or Luna.")
        if selected == "openai-luna" and not isinstance(self.director, OpenAIDirector):
            self.director.stop()
            self.director = OpenAIDirector(self.config, self.store.root)
        elif selected == "local-qwen" and isinstance(self.director, OpenAIDirector):
            self.director.stop()
            self.director = LocalQwenDirector(self.config, self.store.root)
        self.director.reasoning = project["settings"]["director"].get("reasoning", "Balanced")

    def control(self, action, job=None):
        with self.cv:
            if action == "pause":
                self.paused = True
            elif action == "resume":
                self.paused = False
                self.yield_requested = False
            elif action == "cancel-current":
                self.cancel = True
            elif action == "cancel-all":
                self.cancel = True
                self.paused = True
                self.db.execute(
                    "UPDATE jobs SET status='CANCELLED',message='Cancelled; completed assets retained' WHERE status='QUEUED'"
                )
            elif action == "retry":
                available = (
                    1000
                    - self.db.execute(
                        "SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')"
                    ).fetchone()[0]
                )
                self.db.execute(
                    "UPDATE jobs SET status='QUEUED',message='Retrying with saved seed' WHERE id IN (SELECT id FROM jobs WHERE status IN ('FAILED','CANCELLED')"
                    + (" AND id=?" if job else "")
                    + " ORDER BY priority DESC,created LIMIT ?)",
                    (job, available) if job else (available,),
                )
                self.paused = False
            elif action == "priority":
                self.db.execute(
                    "UPDATE jobs SET priority=100 WHERE id=? AND status=?",
                    (job, "QUEUED"),
                )
            elif action == "yield-audio":
                self.yield_requested = True
            else:
                raise ValueError("Unknown production queue control.")
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES('paused',?)",
                ("1" if self.paused else "0",),
            )
            self.db.commit()
            self.cv.notify_all()
        self.sync_queue_states()
        return self.snapshot()

    def gate(self, message="", step=0, total=0, wait_paused=True):
        with self.cv:
            if self.closed or self.cancel:
                raise JobCancelled()
            if self.yield_requested:
                raise AudioYield()
            if message and self.current:
                self.db.execute(
                    "UPDATE jobs SET message=? WHERE id=?", (message, self.current)
                )
                self.db.commit()
            while self.paused and wait_paused:
                if self.closed or self.cancel:
                    raise JobCancelled()
                if self.yield_requested:
                    raise AudioYield()
                self.cv.wait(0.25)

    def checkpoint(self, step, total, message):
        self.gate(
            f"{message} · {step}/{total}" if total else message,
            step,
            total,
            wait_paused=False,
        )

    def status(self, pid, chid, value):
        if chid:
            self.store.mutate(pid, lambda p: get_chapter(p, chid).update(status=value))

    def worker(self):
        while True:
            with self.cv:
                while not self.closed:
                    job = self.db.execute(
                        "SELECT * FROM jobs WHERE status='QUEUED' ORDER BY priority DESC,created LIMIT 1"
                    ).fetchone()
                    if job and not self.paused:
                        break
                    self.cv.wait(0.5)
                if self.closed:
                    return
                job = dict(job)
                self.current = job["id"]
                self.cancel = False
                self.yield_requested = False
                self.db.execute(
                    "UPDATE jobs SET status='RUNNING',started=?,attempt=attempt+1 WHERE id=?",
                    (time.time(), job["id"]),
                )
                self.db.commit()
            started = time.time()
            state = "COMPLETE"
            message = "Complete"
            try:
                self.legacy_queue.control("yield-audio")
                self.legacy_queue.control("pause")
                while not self.gpu_lock.acquire(timeout=0.25):
                    self.gate("Waiting for GPU helper")
                try:
                    p = self.store.load(job["project"])
                    options = json.loads(job["payload"])
                    if job["kind"] == "produce-story":
                        self.produce_story(p["id"], options)
                    elif job["kind"] in (
                        "analyze",
                        "scene-plan",
                        "narration",
                        "intro-audio",
                    ):
                        self.unload_models()
                        self.before_audio()
                        if job["kind"] == "intro-audio":
                            self.intro_audio(p)
                        else:
                            ch = get_chapter(p, job["chapter"])
                            self.narration(p, ch)
                            if job["kind"] == "narration":
                                self.status(
                                    p["id"],
                                    ch["id"],
                                    (
                                        "READY_FOR_IMAGES"
                                        if ch["scenes"]
                                        else "NOT_ANALYZED"
                                    ),
                                )
                            if job["kind"] in ("analyze", "scene-plan"):
                                self.before_image()
                                self.providers["existing"].unload()
                                self.analyze(
                                    self.store.load(p["id"]), job["chapter"], options
                                )
                    elif job["kind"] == "image":
                        self.generate(
                            p, job["chapter"], job["shot"], job["seed"], options
                        )
                    elif job["kind"] == "qc":
                        self.inspect_shot(p, job["chapter"], job["shot"])
                    elif job["kind"] == "intro-image":
                        self.intro_image(p)
                    elif job["kind"] == "character-reference":
                        self.character_reference(p, options)
                    else:
                        self.unload_models()
                        self.providers["existing"].unload()
                        if job["kind"] == "intro-render":
                            result = self.renderer.intro(p, self.gate)
                            self.store.mutate(
                                p["id"],
                                lambda q: q["intro"].update(videoPath=result.name),
                            )
                        elif job["kind"] == "render-full":
                            result = self.renderer.full(p, self.gate)

                            def full_saved(q):
                                if (
                                    q.get("render", {}).get("path")
                                    and q["render"]["path"] != result["path"]
                                ):
                                    q.setdefault("renderHistory", []).append(
                                        q["render"]
                                    )
                                q.update(render=result, renderStale=False)

                            self.store.mutate(p["id"], full_saved)
                        else:
                            ch = get_chapter(p, job["chapter"])
                            result = self.renderer.chapter_export(p, ch, self.gate)

                            def chapter_saved(q):
                                c = get_chapter(q, ch["id"])
                                if (
                                    c.get("render", {}).get("path")
                                    and c["render"]["path"] != result["path"]
                                ):
                                    c.setdefault("renderHistory", []).append(
                                        c["render"]
                                    )
                                c.update(
                                    render=result, status="COMPLETE", renderStale=False
                                )
                                q["renderStale"] = True

                            self.store.mutate(p["id"], chapter_saved)
                finally:
                    self.gpu_lock.release()
            except AudioYield:
                state = "QUEUED"
                message = "Paused to free models for Audio"
                self.paused = True
            except JobCancelled:
                state = "CANCELLED"
                message = "Cancelled; completed assets preserved"
                self.status(job["project"], job["chapter"], "CANCELLED")
            except Exception as error:
                state = "FAILED"
                message = type(error).__name__ + ": " + str(error)[:1800]
                traceback.print_exc()

                def fail(p):
                    if job["chapter"]:
                        ch = get_chapter(p, job["chapter"])
                        ch["status"] = "FAILED"
                        ch["errors"].append(
                            {"time": time.time(), "job": job["id"], "message": message}
                        )
                        if job["shot"]:
                            s = get_shot(p, job["chapter"], job["shot"])
                            s["status"] = "FAILED"
                            s["generationError"] = message

                try:
                    self.store.mutate(job["project"], fail)
                except Exception:
                    pass
            finally:
                if job["kind"] == "produce-story":

                    def run_finished(p):
                        run = p.setdefault("production", {})
                        run.update(status=state, message=message, updated=time.time())
                        if state in ("FAILED", "CANCELLED", "QUEUED") and run.get(
                            "chapterId"
                        ):
                            chapter = next(
                                (
                                    c
                                    for c in p["chapters"]
                                    if c["id"] == run["chapterId"]
                                ),
                                None,
                            )
                            if chapter:
                                chapter.update(
                                    status="PAUSED" if state == "QUEUED" else state
                                )

                    self.store.mutate(job["project"], run_finished)
                if state in ("CANCELLED", "QUEUED", "FAILED"):
                    self.unload_models()
                with self.cv:
                    self.db.execute(
                        "UPDATE jobs SET status=?,message=?,seconds=? WHERE id=?",
                        (state, message, time.time() - started, job["id"]),
                    )
                    self.db.commit()
                    self.current = None
                    self.cancel = False
                    self.cv.notify_all()
                self.sync_queue_states()

    def production_progress(self, pid, stage, **details):
        """Persist the current stage alongside assets, including between restarts."""

        def update(p):
            run = p.setdefault("production", {})
            run.update(status="RUNNING", stage=stage, updated=time.time(), **details)

        self.store.mutate(pid, update)
        self.gate(stage)

    def record_timing(self, pid, stage, seconds, details=None):
        def save(p):
            production = p.setdefault("production", {})
            timings = production.setdefault("timings", [])
            timings.append(
                {
                    "stage": stage,
                    "seconds": round(seconds, 3),
                    "finished": time.time(),
                    **(details or {}),
                }
            )

        self.store.mutate(pid, save)

    def measured_stage(self, pid, stage, callback, *args, **details):
        began = time.monotonic()
        try:
            result = callback(*args)
        except Exception:
            self.record_timing(
                pid, stage, time.monotonic() - began, details | {"status": "FAILED"}
            )
            raise
        self.record_timing(
            pid, stage, time.monotonic() - began, details | {"status": "COMPLETE"}
        )
        return result

    def produce_story(self, pid, options):
        """One durable queue job; reuse completed stages and stop safely on errors."""
        p = self.store.load(pid)
        chapters = [c["id"] for c in p["chapters"] if c["sourceText"].strip()]
        self.production_progress(
            pid,
            "Preparing full story",
            totalChapters=len(chapters),
            completedChapters=0,
            completedImages=0,
            totalImages=0,
            chapterId=None,
            shotId=None,
        )
        for index, chid in enumerate(chapters):
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            self.production_progress(
                pid,
                f"Chapter {index+1} / {len(chapters)} · narration",
                chapterId=chid,
                chapterNumber=ch["number"],
                completedChapters=index,
                completedImages=0,
                totalImages=0,
                shotId=None,
            )
            previous_audio = ch.get("audio", {}).get("signature")
            self.unload_models()
            self.before_audio()
            self.measured_stage(
                pid,
                "Narration",
                self.narration,
                p,
                ch,
                chapter=ch["number"],
                reused=bool(previous_audio),
            )
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            state, _ = state_before(p, chid)
            participating = {
                cast["id"]
                for scene in ch["scenes"]
                for shot in scene["shots"]
                for cast in shot["characters"]
            }

            def relevant_state(value):
                return {
                    "characters": {
                        k: v
                        for k, v in value.get("characters", {}).items()
                        if k in participating
                    },
                    "environment": value.get("environment", {}),
                }

            needs_analysis = (
                not ch["scenes"]
                or ch.get("handoff", {}).get("sourceSignature")
                != digest(ch["sourceText"])
                or previous_audio != ch["audio"].get("signature")
                or digest(relevant_state(ch.get("inputState", {})))
                != digest(relevant_state(state))
                or ch.get("continuityNeedsReview", False)
            )
            if needs_analysis:
                self.production_progress(
                    pid, f"Chapter {index+1} / {len(chapters)} · AI director"
                )
                self.before_image()
                self.providers["existing"].unload()
                self.measured_stage(
                    pid,
                    "AI directing",
                    self.analyze,
                    p,
                    chid,
                    {"managedPipeline": True},
                    chapter=ch["number"],
                )
                p = self.store.load(pid)
                ch = get_chapter(p, chid)
                if ch.get("proposedPlan"):
                    raise ValueError(
                        f"{ch['name']}: review the proposed analysis before continuing. Existing manual edits were preserved."
                    )
                self.store.mutate(
                    pid,
                    lambda q: get_chapter(q, chid).update(continuityNeedsReview=False),
                )
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            if ch.get("proposedPlan"):
                raise ValueError(
                    f"{ch['name']}: apply or dismiss its proposed plan before generating the full video."
                )

            self.measured_stage(
                pid,
                "Identity binding review",
                self.repair_identity_bindings,
                pid,
                chid,
                chapter=ch["number"],
            )

            # This explicit unattended action accepts detected main characters;
            # supporting/background people remain in their chapter.
            def confirm_people(latest):
                chapter = get_chapter(latest, chid)
                for person in chapter["people"]:
                    if person["type"] == "main" and not person.get("removed"):
                        person["accepted"] = True
                        if not any(
                            c["id"] == person["id"] for c in latest["characters"]
                        ):
                            latest["characters"].append(copy.deepcopy(person))
                chapter["inputState"] = state_before(latest, chid)[0]

            self.store.mutate(pid, confirm_people)
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            if p["settings"]["appearanceHandling"] != "Automatic" and any(
                s.get("appearanceChanges") and not s.get("appearanceChangesReviewed")
                for s in ch["scenes"]
            ):
                raise ValueError(
                    f"{ch['name']}: your appearance-change settings require review. Accept the changes and retry this run."
                )
            active_characters = {
                c["id"]
                for sc in ch["scenes"]
                for shot in sc["shots"]
                for c in shot["characters"]
                if c["type"] == "main"
            }
            reference_provider = self.provider(p["settings"]["image"]["provider"])
            if reference_provider.getCapabilities().get("maxReferenceImages", 0):
                for person in p["characters"]:
                    if person["id"] in active_characters and not person["references"]:
                        self.production_progress(
                            pid,
                            f"Chapter {index+1} / {len(chapters)} · reference for {person['name']}",
                        )
                        self.measured_stage(
                            pid,
                            "Character reference",
                            self.character_reference,
                            self.store.load(pid),
                            {"characterId": person["id"], "referenceKind": "face"},
                            chapter=ch["number"],
                            character=person["name"],
                        )

            def prepare_images(latest):
                chapter = get_chapter(latest, chid)
                for scene in chapter["scenes"]:
                    for shot in scene["shots"]:
                        provider = self.provider(shot["imageProvider"])
                        settings = shot["generationSettings"]
                        has_refs = any(
                            c["references"]
                            and any(s["id"] == c["id"] for s in shot["characters"])
                            for c in latest["characters"]
                        )
                        if (
                            provider.id == "native-flux"
                            and has_refs
                            and not shot.get("manual", {}).get("generationSettings")
                        ):
                            settings["width"] = settings["height"] = 384
                        validated = provider.validateSettings(
                            settings | {"model": shot["imageModel"]}
                        )
                        shot["generationSettings"] = validated

            self.store.mutate(pid, prepare_images)
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            shots = [s for scene in ch["scenes"] for s in scene["shots"]]
            for shot_index, shot in enumerate(shots):
                self.production_progress(
                    pid,
                    f"Chapter {index+1} / {len(chapters)} · image {shot_index+1} / {len(shots)}",
                    shotId=shot["id"],
                    imageNumber=shot_index + 1,
                    totalImages=len(shots),
                    completedImages=shot_index,
                )
                complete = (
                    shot.get("imagePath")
                    and shot["status"] in ("COMPLETE", "PASSED")
                    and not shot.get("generationStale")
                    and self.store.asset(pid, shot["imagePath"]).is_file()
                )
                if not complete:
                    try:
                        self.measured_stage(
                            pid,
                            "Image + quality checks",
                            self.generate,
                            self.store.load(pid),
                            chid,
                            shot["id"],
                            shot["generationSettings"]["seed"],
                            {},
                            chapter=ch["number"],
                            shot=shot["id"],
                        )
                    except (JobCancelled, AudioYield):
                        raise
                    except Exception as error:

                        def failed_image(latest):
                            item = get_shot(latest, chid, shot["id"])
                            item.update(status="FAILED", generationError=str(error))

                        self.store.mutate(pid, failed_image)
                        raise
                self.production_progress(
                    pid,
                    f"Chapter {index+1} / {len(chapters)} · saved image {shot_index+1} / {len(shots)}",
                    completedImages=shot_index + 1,
                )
            self.production_progress(
                pid,
                f"Chapter {index+1} / {len(chapters)} · render chapter",
                shotId=None,
            )
            self.unload_models()
            self.providers["existing"].unload()
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            result = self.measured_stage(
                pid,
                "Render chapter",
                self.renderer.chapter,
                p,
                ch,
                self.gate,
                chapter=ch["number"],
            )

            def chapter_saved(latest):
                chapter = get_chapter(latest, chid)
                if (
                    chapter.get("render", {}).get("path")
                    and chapter["render"]["path"] != result["path"]
                ):
                    chapter.setdefault("renderHistory", []).append(chapter["render"])
                chapter.update(render=result, renderStale=False, status="COMPLETE")

            self.store.mutate(pid, chapter_saved)

        p = self.store.load(pid)
        if (
            p["intro"]["enabled"]
            and p["intro"].get("voiceText", "").strip()
            and not p["intro"].get("audioPath")
        ):
            self.production_progress(
                pid, "Preparing separate intro audio", chapterId=None, shotId=None
            )
            self.before_audio()
            self.intro_audio(p)
        p = self.store.load(pid)
        if (
            p["intro"]["enabled"]
            and p["intro"].get("visualPrompt", "").strip()
            and not p["intro"].get("visualPath")
        ):
            self.production_progress(
                pid, "Generating intro visual", chapterId=None, shotId=None
            )
            self.intro_image(p)
        self.production_progress(
            pid,
            "Rendering full story",
            chapterId=None,
            shotId=None,
            completedChapters=len(chapters),
        )
        self.unload_models()
        self.providers["existing"].unload()
        result = self.measured_stage(
            pid,
            "Assemble full video",
            self.renderer.full,
            self.store.load(pid),
            self.gate,
        )

        def saved(latest):
            if (
                latest.get("render", {}).get("path")
                and latest["render"]["path"] != result["path"]
            ):
                latest.setdefault("renderHistory", []).append(latest["render"])
            latest.update(render=result, renderStale=False)
            latest["production"].update(
                status="COMPLETE",
                stage="Full video ready",
                videoPath=result["path"],
                completedChapters=len(chapters),
            )

        self.store.mutate(pid, saved)

    def narration(self, p, ch):
        text = (
            ch["cleanNarrationText"]
            if ch.get("narrationMode") == "manual"
            else clean_narration(
                ch["sourceText"], ch["name"], ch.get("includeChapterLabel", False)
            )
        )
        if not text.strip():
            raise ValueError("Paste chapter narration first.")
        signature = digest(
            {
                "text": text,
                "voice": p["settings"]["voice"],
                "speed": p["settings"]["speed"],
            }
        )
        if (
            ch.get("audio", {}).get("signature") == signature
            and self.store.asset(p["id"], ch["audio"]["path"]).exists()
        ):
            return
        self.status(p["id"], ch["id"], "AUDIO_GENERATING")
        folder = self.store.folder(p["id"]) / ch["id"]
        folder.mkdir(exist_ok=True)
        self.store.mutate(
            p["id"], lambda q: get_chapter(q, ch["id"]).update(cleanNarrationText=text)
        )
        result = self.create_audio(
            text,
            p["settings"]["voice"],
            p["settings"]["speed"],
            folder / f"chapter-{ch['number']:03d}-{signature[:12]}.wav",
        )
        result.update(
            signature=signature,
            textDigest=digest(text),
            voice=p["settings"]["voice"],
            speed=p["settings"]["speed"],
            path=str(
                Path(result["path"]).relative_to(self.store.folder(p["id"]))
            ).replace("\\", "/"),
            downloadName=f"chapter-{ch['number']:03d}.wav",
        )

        def audio_saved(q):
            get_chapter(q, ch["id"]).update(
                audio=result, status="DIRECTING", renderStale=True
            )
            q["renderStale"] = True

        self.store.mutate(p["id"], audio_saved)

    def create_audio(self, text, voice, speed, target):
        import numpy as np
        from kokoro import KPipeline

        if voice not in (
            "am_michael",
            "am_fenrir",
            "am_puck",
            "af_heart",
            "af_bella",
            "af_nicole",
            "bm_george",
            "bf_emma",
        ):
            raise ValueError("Unsupported voice.")
        if not 0.5 <= speed <= 2:
            raise ValueError("Voice speed must be 0.5–2.")
        # Existing KModel is shared; KPipeline here only prepares phonemes.
        pipeline = KPipeline(
            lang_code=voice[0], repo_id="hexgrad/Kokoro-82M", model=False
        )
        self.audio.prepare(voice)
        sections = sentences(text)
        offset = 0
        timings = []
        tmp = target.with_suffix(".partial.wav")
        with wave.open(str(tmp), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            for index, sentence in enumerate(sections):
                self.gate(f"Narration sentence {index+1}/{len(sections)}")
                begin = offset
                for result in pipeline(sentence.replace("*", "")):
                    phonemes = "".join(
                        c for c in result.phonemes if c in self.audio.model.vocab
                    )
                    if not phonemes.strip():
                        raise ValueError(
                            "Narration contains an unpronounceable sentence. Edit narration preview."
                        )
                    # Small inference sections are streamed into ONE WAV; no truncation
                    # and no separate sentence audio assets are created.
                    remaining = phonemes
                    while remaining:
                        self.gate(f"Narration sentence {index+1}/{len(sections)}")
                        boundary = (
                            remaining.rfind(" ", 0, 251)
                            if len(remaining) > 250
                            else len(remaining)
                        )
                        if boundary <= 0:
                            boundary = min(250, len(remaining))
                        section = remaining[:boundary].strip()
                        remaining = remaining[boundary:].lstrip()
                        if not section:
                            continue
                        raw = self.audio.synthesize(section, voice, speed)
                        pcm = np.frombuffer(raw, dtype="<f4")
                        wav.writeframes(
                            (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
                        )
                        offset += len(pcm) / 24000
                timings.append(
                    {
                        "index": index,
                        "text": sentence,
                        "start": begin,
                        "end": offset,
                        "timingSource": "synthesized-sentence-duration",
                    }
                )
        if offset <= 0:
            raise RuntimeError("No narration audio was produced.")
        tmp.replace(target)
        paragraphs = []
        cursor = 0
        for paragraph in re.split(r"\n\s*\n", text):
            count = len(sentences(paragraph))
            if count and cursor + count <= len(timings):
                paragraphs.append(
                    {
                        "index": len(paragraphs),
                        "text": paragraph,
                        "start": timings[cursor]["start"],
                        "end": timings[cursor + count - 1]["end"],
                    }
                )
                cursor += count
        return {
            "path": str(target),
            "duration": offset,
            "sampleRate": 24000,
            "sentences": timings,
            "paragraphs": paragraphs,
            "wordTimingAvailable": False,
            "timingSource": "exact sentence synthesis boundaries",
            "created": time.time(),
        }

    def intro_audio(self, p):
        text = p["intro"].get("voiceText", "").strip()
        if not text:
            raise ValueError(
                "Enter explicit intro voice text first. Chapter labels are never inserted."
            )
        result = self.create_audio(
            text,
            p["settings"]["voice"],
            p["settings"]["speed"],
            self.store.folder(p["id"]) / "intro.wav",
        )
        self.store.mutate(
            p["id"],
            lambda q: q["intro"].update(
                audioPath="intro.wav", audioDuration=result["duration"]
            ),
        )

    def intro_image(self, p):
        self.unload_models()
        self.before_image()
        provider = self.provider(p["settings"]["image"]["provider"])
        if provider.id != "existing":
            self.providers["existing"].unload()
        settings = provider.validateSettings(p["settings"]["image"])
        prompt = p["intro"].get("visualPrompt") or (
            "Atmospheric opening image for "
            + p["name"]
            + ", "
            + p["settings"]["style"]
            + ". Coherent environment, cinematic lighting. No letters or captions."
        )
        result = provider.generateImage(
            {
                "prompt": prompt,
                "negativePrompt": (
                    "text, watermark"
                    if provider.getCapabilities()["supportsNegativePrompt"]
                    else ""
                ),
                "referenceImages": [],
                "settings": settings,
            },
            self.checkpoint,
        )
        name = "intro-" + uid() + ".png"
        path = self.store.folder(p["id"]) / name
        result.pop("pil").save(path, "PNG")
        self.store.mutate(
            p["id"],
            lambda q: q["intro"].update(
                visualPath=name,
                imageMetadata=result | {"settings": settings, "prompt": prompt},
            ),
        )

    def repair_identity_bindings(self, pid, chid):
        """Repair ungrounded early main-character assignments before image costs.

        Keep the saved scene plan and seeds. Only affected direction and inherited
        appearance state change; user edits require review instead of overwrite.
        """
        from director_provider import obj, arr, short_text

        p = self.store.load(pid)
        self.select_director(p)
        ch = get_chapter(p, chid)
        introductions = first_verified_appearances(p, ch)
        original_scenes = digest(ch["scenes"])
        original_people = digest(ch["people"])
        shots = [s for scene in ch["scenes"] for s in scene["shots"]]
        signature = digest(
            {
                "source": ch["sourceText"],
                "shots": [s["id"] for s in shots],
                "introductions": introductions,
                "bindingVersion": 3,
            }
        )
        if ch.get("identityBindingSignature") == signature:
            return
        affected = [
            s
            for s in shots
            if any(
                c["id"] in introductions and s["endSentence"] < introductions[c["id"]]
                for c in s["characters"]
            )
        ]
        unsupported_changes = [
            e
            for s in shots
            for e in s.get("intentionalAppearanceChanges", [])
            if not supported_appearance_change(e)
        ]
        sound_only = {
            s["id"]
            for s in shots
            if introductions
            and s["endSentence"] < max(introductions.values())
            and unidentified_gunshot(s["narrationSegment"])
            and not s.get("unknownIdentityFraming")
        }
        if not affected and not unsupported_changes and not sound_only:
            self.store.mutate(
                pid,
                lambda latest: get_chapter(latest, chid).update(
                    identityBindingSignature=signature
                ),
            )
            return
        if any(
            s.get("manual")
            for s in affected + [s for s in shots if s["id"] in sound_only]
        ):
            raise ValueError(
                "Opening character identities need review; manual shot edits were preserved."
            )
        temporary = character(
            "Unidentified opening person",
            "Identity is unconfirmed. Use only this shot's stated appearance; do not give this person the recurring protagonist's face.",
            "temporary",
        )
        temporary["id"] = (
            "person-"
            + digest(
                {"project": pid, "chapter": chid, "role": "unidentified-opening-person"}
            )[:16]
        )
        if not any(c["id"] == temporary["id"] for c in ch["people"]):
            ch["people"].append(temporary)
        people = {c["id"]: c for c in p["characters"] + ch["people"]}
        corrections = {}
        for begin in range(0, len(affected), 4):
            batch = affected[begin : begin + 4]
            excluded = {
                c["id"]
                for s in batch
                for c in s["characters"]
                if c["id"] in introductions
                and s["endSentence"] < introductions[c["id"]]
            }
            allowed = [c for c in people.values() if c["id"] not in excluded]
            item = obj(
                {
                    "characters": arr(
                        {"type": "string", "enum": [c["id"] for c in allowed]}
                    )
                    | {"maxItems": 5},
                    "action": short_text(400),
                    "composition": short_text(300),
                    "expression": short_text(120),
                    "pose": short_text(180),
                }
            )
            result = self.director.call(
                "Source-faithfulness repair. These opening shots incorrectly assigned an unidentified person to a protagonist introduced later. Do NOT use excluded main characters or their faces. A shot victim and a later calm armed protagonist are different roles unless narration explicitly identifies them as the same. For a heard gunshot with no identified shooter, depict an insert or reaction without inventing a visible known shooter. Use the temporary unidentified person for anonymous foreground people. Preserve exact story actions; no extra deaths, rescues or events. Return one corrected direction for each supplied shot key; character selections use allowed IDs only.",
                {
                    "shots": {
                        f"shot{i}": {
                            "narration": s["narrationSegment"],
                            "wrongAction": s["action"],
                            "location": s["location"],
                        }
                        for i, s in enumerate(batch)
                    },
                    "allowedPeople": [
                        {k: c.get(k, "") for k in ("id", "name", "type", "description")}
                        for c in allowed
                    ],
                    "excludedMainCharacters": [
                        {
                            "id": cid,
                            "name": people[cid]["name"],
                            "laterIdentityEvidence": people[cid].get("evidence", ""),
                        }
                        for cid in excluded
                    ],
                },
                obj({f"shot{i}": item for i in range(len(batch))}),
                self.gate,
            )
            for i, s in enumerate(batch):
                corrections[s["id"]] = result[f"shot{i}"]
        state = copy.deepcopy(ch.get("inputState", state_before(p, chid)[0]))
        for person in ch["people"]:
            state.setdefault("characters", {}).setdefault(
                person["id"], copy.deepcopy(person["defaultAppearance"])
            )
        notes = copy.deepcopy(ch["analysis"].get("identityBindingRepairs", []))
        for scene in ch["scenes"]:
            scene["appearanceChanges"] = []
            for shot in scene["shots"]:
                old_appearance = copy.deepcopy(shot["characters"])
                changes = [
                    e
                    for e in shot.get("intentionalAppearanceChanges", [])
                    if supported_appearance_change(e)
                    if not (
                        e["characterId"] in introductions
                        and shot["endSentence"] < introductions[e["characterId"]]
                    )
                ]
                shot["intentionalAppearanceChanges"] = changes
                scene["appearanceChanges"] += changes
                state["environment"] = copy.deepcopy(shot.get("continuity", {}))
                state = apply_changes(state, changes, ch["number"], scene["id"])
                if shot["id"] in corrections:
                    update = corrections[shot["id"]]
                    notes.append(
                        {
                            "shotId": shot["id"],
                            "originalAction": shot["action"],
                            "correctedAction": update["action"],
                            "reason": "Main character identity was not established in this opening narration",
                        }
                    )
                    shot.update(
                        {k: update[k] for k in ("action", "expression", "pose")}
                    )
                    shot["camera"]["composition"] = update["composition"]
                    shot["characters"] = [
                        {"id": cid, "type": people[cid]["type"], "appearanceState": {}}
                        for cid in update["characters"]
                    ]
                if shot["id"] in sound_only:
                    notes.append(
                        {
                            "shotId": shot["id"],
                            "originalAction": shot["action"],
                            "reason": "Narration hears a gunshot but does not identify a visible shooter; keep the unknown face outside the frame",
                        }
                    )
                    shot.update(
                        action="A tight cinematic insert of a gun muzzle firing with a bright muzzle flash. The shooter's face and body remain entirely outside the frame. Blurred party lights in the background.",
                        expression="",
                        pose="",
                        characters=[],
                        unknownIdentityFraming=True,
                    )
                    shot["camera"].update(
                        shot="insert shot",
                        composition="Gun barrel fills the frame; no visible face or body",
                    )
                for selected in shot["characters"]:
                    selected["appearanceState"] = copy.deepcopy(
                        state["characters"].get(selected["id"], {})
                    )
                if (
                    shot["id"] in corrections
                    or shot["id"] in sound_only
                    or old_appearance != shot["characters"]
                ):
                    if shot.get("manual"):
                        raise ValueError(
                            "Inherited appearance needs review; manual shot fields were preserved."
                        )
                    shot["prompt"], shot["negativePrompt"] = format_prompt(
                        p, shot, self.provider(shot["imageProvider"])
                    )
                    shot["generationStale"] = bool(shot.get("imagePath"))
                    shot["status"] = "READY_FOR_IMAGES"
            scene["characters"] = list(
                {
                    c["id"]: {"id": c["id"], "type": c["type"]}
                    for s in scene["shots"]
                    for c in s["characters"]
                }.values()
            )
        ch["handoff"]["state"] = state
        ch["analysis"]["identityBindingRepairs"] = notes
        ch["analysis"].setdefault("rejectedAppearanceChanges", []).extend(
            unsupported_changes
        )
        ch["identityBindingSignature"] = signature
        ch["renderStale"] = True
        ch["status"] = "READY_FOR_IMAGES"
        for person in ch["people"]:
            person["currentAppearance"] = copy.deepcopy(
                state["characters"].get(person["id"], {})
            )
            person["appearanceHistory"] = [
                e
                for e in state.get("appearanceHistory", [])
                if e["characterId"] == person["id"]
            ]

        def save(latest):
            target = get_chapter(latest, chid)
            if target["sourceText"] != ch["sourceText"] or [
                s["id"] for sc in target["scenes"] for s in sc["shots"]
            ] != [s["id"] for s in shots]:
                raise ValueError(
                    "Chapter changed during identity review; newer work was preserved."
                )
            if (
                digest(target["scenes"]) != original_scenes
                or digest(target["people"]) != original_people
            ):
                raise ValueError(
                    "Manual edits arrived during identity review; newer work was preserved."
                )
            previous = copy.deepcopy(target.get("handoff", {}))
            target.update(
                {
                    k: ch[k]
                    for k in (
                        "people",
                        "scenes",
                        "handoff",
                        "analysis",
                        "identityBindingSignature",
                        "renderStale",
                        "status",
                    )
                }
            )
            warn_dependents(latest, target, previous)
            following = latest["chapters"][latest["chapters"].index(target) + 1 :]
            if not any(c.get("handoff", {}).get("sourceSignature") for c in following):
                latest["continuity"] = state
                for person in latest["characters"]:
                    if person["id"] in introductions:
                        person["currentAppearance"] = copy.deepcopy(
                            state["characters"].get(person["id"], {})
                        )

        self.store.mutate(pid, save)

    def character_reference(self, p, options):
        self.unload_models()
        self.before_image()
        provider = self.provider(p["settings"]["image"]["provider"])
        if provider.id != "existing":
            self.providers["existing"].unload()
        person = next(c for c in p["characters"] if c["id"] == options["characterId"])
        settings = provider.validateSettings(p["settings"]["image"])
        prompt = f"A clear single-character {options.get('referenceKind','face')} reference portrait of {person['name']}. {person['description']}. Permanent identity: {json.dumps(person['permanentIdentity'])}. Appearance: {json.dumps(person['defaultAppearance'])}. {p['settings']['style']}. Neutral plain background, no other people, no text."
        refs = [
            data_url(self.store.asset(p["id"], r["path"]))
            for r in person["references"][
                : provider.getCapabilities().get("maxReferenceImages", 0)
            ]
        ]
        if refs and provider.id == "native-flux":
            settings.update(width=384, height=384)
        result = provider.generateImage(
            {
                "prompt": prompt,
                "negativePrompt": (
                    "text, watermark, extra people"
                    if provider.getCapabilities()["supportsNegativePrompt"]
                    else ""
                ),
                "referenceImages": refs,
                "settings": settings,
            },
            self.checkpoint,
        )
        name = "references/" + uid() + ".png"
        path = self.store.asset(p["id"], name)
        path.parent.mkdir(exist_ok=True)
        result.pop("pil").save(path, "PNG")

        def save(latest):
            next(c for c in latest["characters"] if c["id"] == person["id"])[
                "references"
            ].append(
                {
                    "path": name,
                    "kind": options.get("referenceKind", "face"),
                    "metadata": result | {"prompt": prompt, "settings": settings},
                }
            )

        self.store.mutate(p["id"], save)

    def analyze(self, p, chid, options):
        self.select_director(p)
        self.director.timing_callback = (
            lambda stage, seconds, details: self.record_timing(
                p["id"],
                stage,
                seconds,
                details | {"chapter": get_chapter(p, chid)["number"], "detail": True},
            )
        )
        self.director.reasoning = p["settings"]["director"].get("reasoning", "Balanced")
        ch = get_chapter(p, chid)
        self.status(p["id"], chid, "DIRECTING")
        timings = ch["audio"]["sentences"]
        state, memory = state_before(p, chid)
        original = copy.deepcopy(ch.get("handoff", {}))
        scenes = []
        people = []
        analyses = []
        locations = []
        passes = []
        selected_scene = next(
            (s for s in ch["scenes"] if s["id"] == options.get("sceneId")), None
        )
        if selected_scene:
            timings = [
                t
                for t in timings
                if selected_scene["startSentence"]
                <= t["index"]
                <= selected_scene["endSentence"]
            ]
            people = copy.deepcopy(ch["people"])
            for old_scene in ch["scenes"]:
                if old_scene["id"] == selected_scene["id"]:
                    break
                state = apply_changes(
                    state,
                    old_scene.get("appearanceChanges", []),
                    ch["number"],
                    old_scene["id"],
                )
        known_people = [
            {
                "id": c["id"],
                "name": c["name"],
                "type": "main",
                "description": c["description"],
                "aliases": c.get("aliases", []),
                "identity": c["permanentIdentity"],
            }
            for c in p["characters"]
        ]
        for casting_text in text_groups(ch["sourceText"]):
            casting = self.director.resolvePeople(
                {
                    "chapterText": casting_text,
                    "knownPeople": known_people
                    + [
                        {
                            "id": c["id"],
                            "name": c["name"],
                            "type": c["type"],
                            "description": c["description"],
                            "aliases": c.get("aliases", []),
                        }
                        for c in people
                    ],
                    "previousChapterSummary": memory.get("previousChapterSummary", ""),
                },
                self.gate,
            )
            passes.append({"pass": "casting-supervisor", "output": casting})
            for detected in casting["people"]:
                matching = next(
                    (
                        c
                        for c in p["characters"] + people
                        if c["id"] == detected["id"]
                        or c["name"].casefold() == detected["name"].casefold()
                        and not re.match(
                            r"^(unknown|unnamed|stranger)\b", c["name"], re.I
                        )
                    ),
                    None,
                )
                if matching:
                    continue
                person = character(
                    detected["name"], detected["description"], detected["type"]
                )
                person.update(
                    id="person-"
                    + digest(
                        {
                            "project": p["id"],
                            "chapter": chid,
                            "name": detected["name"].strip().casefold(),
                            "role": detected["description"],
                        }
                    )[:16],
                    accepted=False,
                    evidence=detected["evidence"],
                    aliases=detected["aliases"],
                )
                if detected["type"] == "main":
                    # Casting decides identities; a separate small pass extracts
                    # the protagonist's bible without filling dozens of fields
                    # for every bystander or unnamed member of a crowd.
                    from director_provider import obj, short_text, IDENTITY, APPEARANCE

                    quote = detected["evidence"].strip(" \"'")
                    evidence = (
                        next(
                            (
                                s
                                for s in sentences(casting_text)
                                if quote[:50].casefold() in s.casefold()
                            ),
                            "",
                        )
                        if len(quote) >= 50
                        else ""
                    )
                    profile = self.director.call(
                        "Character bible. Extract ONLY identity traits explicitly stated in sourceEvidence for this person. Include beard/facial hair in face. Unknown fields MUST be empty strings, never 'unknown'. Clothing is NOT permanent identity. Held weapons and tools are NOT permanent accessories. Do not borrow any other person's clothing. Return permanentIdentity, defaultAppearance, gender, approximateAge. Do not infer later events.",
                        {"name": detected["name"], "sourceEvidence": evidence},
                        obj(
                            {
                                "permanentIdentity": obj(
                                    {k: short_text(140) for k in IDENTITY["properties"]}
                                ),
                                "defaultAppearance": obj(
                                    {
                                        k: short_text(140)
                                        for k in APPEARANCE["properties"]
                                    }
                                ),
                                "gender": short_text(40),
                                "approximateAge": short_text(40),
                            }
                        ),
                        self.gate,
                    )
                    for section in ("permanentIdentity", "defaultAppearance"):
                        profile[section] = {
                            k: (
                                ""
                                if str(v).strip().casefold()
                                in ("unknown", "none", "not stated", "unspecified")
                                else v
                            )
                            for k, v in profile[section].items()
                        }
                    person.update(profile)
                    if evidence:
                        person["description"] = (
                            "; ".join(
                                v for v in profile["permanentIdentity"].values() if v
                            )
                            or evidence
                        )
                    passes.append(
                        {
                            "pass": "character-bible",
                            "person": person["id"],
                            "output": profile,
                        }
                    )
                if any(c["id"] == person["id"] for c in people):
                    continue
                people.append(person)
                state.setdefault("characters", {})[person["id"]] = copy.deepcopy(
                    person["defaultAppearance"]
                )
        chapter_cast = known_people + [
            {
                "id": c["id"],
                "name": c["name"],
                "type": c["type"],
                "description": c["description"],
                "aliases": c.get("aliases", []),
            }
            for c in people
        ]
        # Bounded chunks follow sentence boundaries; the director chooses scenes in each.
        groups = []
        current = []
        size = 0
        for item in timings:
            if current and (size + len(item["text"]) > 2500 or len(current) >= 12):
                groups.append(current)
                current = []
                size = 0
            current.append(item)
            size += len(item["text"])
        if current:
            groups.append(current)
        provider = self.provider(p["settings"]["image"]["provider"])
        health = provider.healthCheck()
        if not health["installed"]:
            raise ValueError(
                "Selected image workflow is unavailable. Choose an installed model before analyzing."
            )
        for group_index, group in enumerate(groups):
            base = group[0]["index"]
            text = [
                {"index": i, "text": t["text"], "start": t["start"], "end": t["end"]}
                for i, t in enumerate(group)
            ]
            canonical = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "description": c["description"],
                    "identity": c["permanentIdentity"],
                }
                for c in p["characters"]
            ]
            compact_state = {k: v for k, v in state.items() if k != "appearanceHistory"}
            context = {
                "sentences": text,
                "knownMainCharacters": canonical,
                "priorState": compact_state,
                "storyMemory": memory,
                "layoutMode": p["settings"]["layoutMode"],
                "customTargets": p["settings"]["customLayout"],
                "chapterCast": chapter_cast,
            }
            self.gate(f"Analyzing group {group_index+1}/{len(groups)}")
            analysis = self.director.analyzeStory(context, self.gate)
            analyses.append(analysis)
            passes.append(
                {"pass": "story-analyst", "group": group_index, "output": analysis}
            )
            align_evidence(analysis["changes"], text)
            align_evidence(analysis.get("environmentChanges", []), text)
            invalid = [
                e
                for e in analysis["changes"]
                if not 0 <= e["sentence"] < len(group)
                or e["reason"].casefold() not in group[e["sentence"]]["text"].casefold()
            ]
            if invalid:
                from director_provider import obj, arr, CHANGE

                repaired = self.director.call(
                    "Repair state-change evidence. Preserve changes supported by narration. Every reason must be an exact verbatim substring of its source sentence. Return ONLY supported changes; use specific fields (outfit, hairStyle, injury, key).",
                    {
                        "sentences": text,
                        "changes": analysis["changes"],
                        "people": canonical,
                    },
                    obj({"changes": arr(CHANGE)}),
                    self.gate,
                )
                analysis["changes"] = align_evidence(repaired["changes"], text)
                if any(
                    not 0 <= e["sentence"] < len(group)
                    or e["reason"].casefold()
                    not in group[e["sentence"]]["text"].casefold()
                    for e in analysis["changes"]
                ):
                    raise ValueError(
                        "Director state changes lack exact story evidence after repair. Review chapter text and retry; prior continuity was retained."
                    )
            known = {c["id"]: c for c in p["characters"]}
            byname = {c["name"].casefold(): c["id"] for c in p["characters"]}
            mapping = {}
            for detected in analysis["people"]:
                match = byname.get(detected["name"].casefold())
                existing = next(
                    (
                        x
                        for x in people
                        if x["name"].casefold() == detected["name"].casefold()
                    ),
                    None,
                )
                # Stable provisional IDs make subsequent director contexts and
                # their validated cache entries reusable after a failed pass.
                id = match or (
                    existing["id"]
                    if existing
                    else "person-"
                    + digest(
                        {
                            "project": p["id"],
                            "chapter": chid,
                            "name": detected["name"].strip().casefold(),
                        }
                    )[:16]
                )
                mapping[detected["id"]] = id
                if not existing and not match:
                    person = character(
                        detected["name"], detected["description"], detected["type"]
                    )
                    person.update(
                        id=id,
                        accepted=False,
                        evidence=detected["evidence"],
                        permanentIdentity=detected.get(
                            "permanentIdentity", person["permanentIdentity"]
                        ),
                        defaultAppearance=detected.get(
                            "defaultAppearance", person["defaultAppearance"]
                        ),
                        gender=detected.get("gender", ""),
                        approximateAge=detected.get("approximateAge", ""),
                    )
                    people.append(person)
                    state.setdefault("characters", {})[id] = copy.deepcopy(
                        person["defaultAppearance"]
                    )
                if match:
                    person = known[match]
                    for field, value in detected.get("permanentIdentity", {}).items():
                        if value and not person["permanentIdentity"].get(field):
                            person["permanentIdentity"][field] = value
            cast = [
                {
                    "id": c["id"],
                    "name": c["name"],
                    "type": "main",
                    "description": c["description"],
                    "aliases": c.get("aliases", []),
                }
                for c in p["characters"]
            ] + [
                {
                    "id": x["id"],
                    "name": x["name"],
                    "type": x["type"],
                    "description": x["description"],
                    "aliases": x.get("aliases", []),
                }
                for x in people
            ]
            for loc in analysis["locations"]:
                analysis_location(p, loc, locations)
            plan = self.director.planChapter(
                context | {"analysis": analysis, "people": cast}, self.gate
            )
            passes.append(
                {"pass": "chapter-director", "group": group_index, "output": plan}
            )
            ranges = plan["scenes"]
            try:
                self.validate_ranges(ranges, len(group), "scene")
            except ValueError as error:
                from director_provider import obj, arr, SCENE

                plan = self.director.call(
                    "Repair scene boundaries. Cover every supplied sentence exactly once, in order. Start with sentence 0. Keep source story unchanged.",
                    {"sentences": text, "plan": plan, "error": str(error)},
                    obj({"scenes": arr(SCENE)}),
                    self.gate,
                )
                ranges = plan["scenes"]
                self.validate_ranges(ranges, len(group), "scene")
            change_sentences = sorted({e["sentence"] for e in analysis["changes"]})
            scene_context = {
                "sentences": text,
                "scenes": [r | {"sceneIndex": i} for i, r in enumerate(ranges)],
                "people": cast,
                "currentState": compact_state,
                "storyBeats": analysis["beats"],
                "requiredVisualChangeBoundaries": change_sentences,
                "instruction": "Start a shot at each supplied visual-change sentence. Depict that moment explicitly: object handover/pickup, injury, clothing change. Do not bury these moments inside a shot about an earlier action.",
            }
            detail = self.director.planScenes(scene_context, self.gate)
            passes.append(
                {"pass": "scene-director", "group": group_index, "output": detail}
            )
            details = detail["shots"]
            for attempt in range(3):
                try:
                    for s in details:
                        matches = [
                            i
                            for i, r in enumerate(ranges)
                            if r["startSentence"]
                            <= s["startSentence"]
                            <= s["endSentence"]
                            <= r["endSentence"]
                        ]
                        if len(matches) == 1:
                            s["sceneIndex"] = matches[0]
                    normalized_details = []
                    for si, rs in enumerate(ranges):
                        items = self.trim_shot_overlaps(
                            [s for s in details if s["sceneIndex"] == si]
                        )
                        self.validate_shot_ranges(items, rs)
                        normalized_details.extend(items)
                    details = normalized_details
                    break
                except ValueError as error:
                    if attempt == 2:
                        raise
                    from director_provider import obj, arr, SHOT

                    if attempt == 1:
                        repaired = []
                        for si, rs in enumerate(ranges):
                            items = [s for s in details if s["sceneIndex"] == si]
                            try:
                                self.validate_shot_ranges(items, rs)
                            except ValueError as scene_error:
                                schema_shot = obj(
                                    SHOT["properties"]
                                    | {
                                        "sceneIndex": {"type": "integer", "enum": [si]},
                                        "startSentence": {
                                            "type": "integer",
                                            "minimum": rs["startSentence"],
                                            "maximum": rs["endSentence"],
                                        },
                                        "endSentence": {
                                            "type": "integer",
                                            "minimum": rs["startSentence"],
                                            "maximum": rs["endSentence"],
                                        },
                                    }
                                )
                                items = self.director.call(
                                    "Repair ONLY this scene. Keep its narration exactly once in order, with no overlaps. The first shot starts at startSentence and the last ends at endSentence. Use the supplied inclusive indices. No invented people or actions.",
                                    {
                                        "scene": rs,
                                        "sceneIndex": si,
                                        "sentences": text[
                                            rs["startSentence"] : rs["endSentence"] + 1
                                        ],
                                        "people": cast,
                                        "invalidShots": items,
                                        "error": str(scene_error),
                                    },
                                    obj({"shots": arr(schema_shot)}),
                                    self.gate,
                                )["shots"]
                            repaired.extend(items)
                        details = repaired
                        continue
                    detail = self.director.call(
                        "Repair shot coverage. sceneIndex is explicitly supplied, ZERO based. Every scene needs shots. Cover each scene sentence range exactly once. One visible moment per shot; no sequential montage.",
                        scene_context | {"invalidPlan": detail, "error": str(error)},
                        obj({"shots": arr(SHOT)}),
                        self.gate,
                    )
                    details = detail["shots"]
            # A small director can miss a required visual-change boundary. The
            # application retains validated chronology and asks the AI to direct
            # the resulting moments, rather than accepting an omitted story beat.
            expanded = []
            needs_direction = []
            for ds in details:
                points = (
                    [ds["startSentence"]]
                    + [
                        n
                        for n in change_sentences
                        if ds["startSentence"] < n <= ds["endSentence"]
                    ]
                    + [ds["endSentence"] + 1]
                )
                for a, b in zip(points, points[1:]):
                    item = copy.deepcopy(ds)
                    item.update(startSentence=a, endSentence=b - 1)
                    expanded.append(item)
                    if len(points) > 2:
                        needs_direction.append(item)
            if needs_direction:
                from director_provider import obj, STR

                fields = obj(
                    {
                        k: STR
                        for k in (
                            "action",
                            "expression",
                            "pose",
                            "lighting",
                            "motion",
                            "transition",
                        )
                    }
                )
                for begin in range(0, len(needs_direction), 4):
                    batch = needs_direction[begin : begin + 4]
                    slots = {
                        f"shot{i}": {
                            "narration": " ".join(
                                t["text"]
                                for t in group[
                                    s["startSentence"] : s["endSentence"] + 1
                                ]
                            ),
                            "people": cast,
                            "previousAction": s["action"],
                        }
                        for i, s in enumerate(batch)
                    }
                    repaired = self.director.call(
                        "Direct each supplied story moment separately. Required shot keys and narration intervals are fixed by story evidence. Describe one visible action from its own narration, with the correct people. Do not reuse an earlier shot action or invent events.",
                        slots,
                        obj({k: fields for k in slots}),
                        self.gate,
                    )
                    for i, s in enumerate(batch):
                        s.update(repaired[f"shot{i}"])
                passes.append(
                    {
                        "pass": "story-beat-boundary-repair",
                        "count": len(needs_direction),
                    }
                )
            details = expanded
            cameras = {}
            if p["settings"]["generationMode"] != "QUICK":
                result = self.director.planLayout(
                    {
                        "shots": details,
                        "style": p["settings"]["style"],
                        "mode": p["settings"]["layoutMode"],
                    },
                    self.gate,
                )
                cameras = {x["shotIndex"]: x for x in result["cameras"]}
                passes.append(
                    {"pass": "cinematographer", "group": group_index, "output": result}
                )
            workflow = self.director.selectImageWorkflow(
                {
                    "available": [
                        {
                            "provider": provider.id,
                            "models": health["models"],
                            "workflows": health["workflow"],
                            "capabilities": provider.getCapabilities(),
                        }
                    ],
                    "selected": p["settings"]["image"],
                    "mainCharacterReferences": sum(
                        bool(c["references"]) for c in p["characters"]
                    ),
                },
                self.gate,
            )
            if (
                workflow["provider"] != provider.id
                or workflow["model"] not in health["models"]
                or workflow["workflow"] not in health["workflow"]
            ):
                raise ValueError(
                    "Director selected an unavailable image model/workflow. Review settings and retry."
                )
            passes.append(
                {
                    "pass": "image-workflow-planner",
                    "group": group_index,
                    "output": workflow,
                }
            )
            continuity = self.director.checkContinuity(
                {
                    "knownState": compact_state,
                    "people": cast,
                    "objects": analysis["objects"],
                    "changes": analysis["changes"],
                    "sentences": text,
                    "shots": (
                        details if p["settings"]["generationMode"] != "QUICK" else []
                    ),
                },
                self.gate,
            )
            passes.append(
                {
                    "pass": "continuity-supervisor",
                    "group": group_index,
                    "output": continuity,
                }
            )
            object_changes = align_evidence(
                grounded_object_changes(
                    continuity["objectChanges"], analysis["objects"], cast
                ),
                text,
            )
            if any(
                not 0 <= e["sentence"] < len(group)
                or not e["reason"]
                or e["reason"].casefold() not in group[e["sentence"]]["text"].casefold()
                or e["field"] == "accessories"
                for e in object_changes
            ):
                from director_provider import obj, arr, CHANGE

                object_changes = self.director.call(
                    "Repair object continuity only. Keep every supported object position/possession fact. Use specific field key/phone/sword, never accessories. Quote the exact source sentence in reason. Zero based indices.",
                    {
                        "sentences": text,
                        "people": cast,
                        "invalidChanges": object_changes,
                    },
                    obj({"changes": arr(CHANGE)}),
                    self.gate,
                )["changes"]
                if any(
                    not 0 <= e["sentence"] < len(group)
                    or not e["reason"]
                    or e["reason"].casefold()
                    not in group[e["sentence"]]["text"].casefold()
                    or e["field"] == "accessories"
                    for e in object_changes
                ):
                    raise ValueError(
                        "Object continuity lacks exact narration evidence. Existing chapter plan was retained."
                    )
            # Object events must never replace a watch, necklace or other clothing accessory.
            analysis["changes"] = [
                normalize_object_change(e, analysis["objects"], cast)
                for e in analysis["changes"]
            ]
            analysis["changes"] = sorted(
                analysis["changes"] + object_changes, key=lambda e: e["sentence"]
            )
            known_ids = {x["id"] for x in cast}
            cursor = 0
            for si, rs in enumerate(ranges):
                id = uid("scene-")
                a, b = rs["startSentence"], rs["endSentence"]
                location = next(
                    (
                        l
                        for l in p["locations"] + locations
                        if l["name"].casefold() == rs["location"].casefold()
                    ),
                    None,
                )
                scene = {
                    "id": id,
                    "chapterId": chid,
                    "start": group[a]["start"],
                    "end": group[b]["end"],
                    "startSentence": base + a,
                    "endSentence": base + b,
                    "purpose": rs["purpose"],
                    "mood": rs["mood"],
                    "locationId": location["id"] if location else None,
                    "location": rs["location"],
                    "characters": [],
                    "appearanceChanges": [],
                    "shots": [],
                    "pacingReason": rs["pacingReason"],
                    "origin": "AI",
                }
                for ds in [s for s in details if s["sceneIndex"] == si]:
                    sa, sb = ds["startSentence"], ds["endSentence"]
                    changes = []
                    state.setdefault("environment", {}).update(
                        location=rs["location"], locationId=scene["locationId"]
                    )
                    for e in analysis.get("environmentChanges", []):
                        if (
                            sa <= e["sentence"] <= sb
                            and e["reason"].casefold()
                            in group[e["sentence"]]["text"].casefold()
                        ):
                            state["environment"][e["field"]] = e["value"]
                    for e in analysis["changes"]:
                        if sa <= e["sentence"] <= sb:
                            cid = mapping.get(e["characterId"], e["characterId"])
                            # Only evidence present in this narration can mutate persistent facts.
                            if (
                                cid in known_ids
                                and e["reason"]
                                and e["reason"].casefold()
                                in " ".join(
                                    x["text"] for x in group[sa : sb + 1]
                                ).casefold()
                            ):
                                changes.append(
                                    {
                                        "characterId": cid,
                                        "type": e["field"],
                                        "to": {e["field"]: e["value"]},
                                        "reason": e["reason"],
                                    }
                                )
                    state = apply_changes(state, changes, ch["number"], id)
                    scene["appearanceChanges"] += changes
                    selected = []
                    for cid in ds["characters"]:
                        cid = mapping.get(cid, cid)
                        if cid not in known_ids:
                            raise ValueError(
                                "Director invented an unknown character ID. Retry this chapter analysis."
                            )
                        person = next(x for x in cast if x["id"] == cid)
                        selected.append(
                            {
                                "id": cid,
                                "type": person["type"],
                                "appearanceState": copy.deepcopy(
                                    state.get("characters", {}).get(cid, {})
                                ),
                            }
                        )
                    cam = cameras.get(
                        cursor,
                        {
                            "shot": "medium",
                            "angle": "eye level",
                            "composition": ds["action"],
                        },
                    )
                    cursor += 1
                    shot_settings = copy.deepcopy(p["settings"]["image"])
                    if provider.id == "native-flux" and any(
                        c["references"] and any(s["id"] == c["id"] for s in selected)
                        for c in p["characters"]
                    ):
                        shot_settings.update(width=384, height=384)
                    settings = provider.validateSettings(shot_settings)
                    shot = {
                        "id": uid("shot-"),
                        "sceneId": id,
                        "chapterId": chid,
                        "start": group[sa]["start"],
                        "end": group[sb]["end"],
                        "duration": group[sb]["end"] - group[sa]["start"],
                        "startSentence": base + sa,
                        "endSentence": base + sb,
                        "narrationSegment": " ".join(
                            x["text"] for x in group[sa : sb + 1]
                        ),
                        "characters": selected,
                        "referenceImages": [],
                        "location": rs["location"],
                        "locationId": scene["locationId"],
                        "action": ds["action"],
                        "expression": ds["expression"],
                        "pose": ds["pose"],
                        "camera": {k: cam[k] for k in ("shot", "angle", "composition")},
                        "lighting": ds["lighting"],
                        "visualStyle": p["settings"]["style"],
                        "continuity": copy.deepcopy(state.get("environment", {})),
                        "intentionalAppearanceChanges": changes,
                        "imageProvider": provider.id,
                        "imageModel": settings["model"],
                        "workflow": workflow["workflow"],
                        "generationSettings": settings,
                        "prompt": "",
                        "negativePrompt": "",
                        "imagePath": "",
                        "sourceImagePath": "",
                        "qc": {"status": "PENDING"},
                        "status": "READY_FOR_IMAGES",
                        "retryCount": 0,
                        "generationError": "",
                        "planningRepairs": ds.get("timingRepair", {}),
                        "alternateDirections": ds.get("alternateDirections", []),
                        "history": [],
                        "origin": "AI",
                        "manual": {},
                        "motion": (
                            ds["motion"]
                            if ds["motion"]
                            in (
                                "static",
                                "slow zoom in",
                                "slow zoom out",
                                "pan left",
                                "pan right",
                                "pan up",
                                "pan down",
                            )
                            else "static"
                        ),
                        "transition": (
                            ds["transition"]
                            if ds["transition"] in ("cut", "crossfade")
                            else "cut"
                        ),
                    }
                    # Include this chapter's temporary cast when building model-specific prompts.
                    prompt_project = copy.deepcopy(p)
                    get_chapter(prompt_project, chid)["people"] = people
                    shot["prompt"], shot["negativePrompt"] = format_prompt(
                        prompt_project, shot, provider
                    )
                    scene["shots"].append(shot)
                scene["characters"] = list(
                    {
                        x["id"]: {"id": x["id"], "type": x["type"]}
                        for s in scene["shots"]
                        for x in s["characters"]
                    }.values()
                )
                scenes.append(scene)
            memory = {
                "previousChapterSummary": analysis["summary"],
                "majorEvents": (
                    memory.get("majorEvents", [])
                    + [
                        {
                            "chapter": ch["number"],
                            "group": group_index,
                            "summary": analysis["summary"],
                        }
                    ]
                )[-12:],
                "objects": list(
                    dict.fromkeys(memory.get("objects", []) + analysis["objects"])
                )[-32:],
                "goals": analysis["goals"],
                "unresolved": list(
                    dict.fromkeys(memory.get("unresolved", []) + analysis["unresolved"])
                )[-24:],
                "locationsVisited": list(
                    dict.fromkeys(
                        memory.get("locationsVisited", [])
                        + [l["name"] for l in analysis["locations"]]
                    )
                )[-24:],
                "relationships": {
                    c["id"]: c.get("relationships", [])
                    for c in p["characters"]
                    if c.get("relationships")
                },
            }
            # Summaries remain bounded, canonical state does not grow with full story text.
        if p["settings"]["generationMode"] != "QUICK":
            planned_shots = [s for scene in scenes for s in scene["shots"]]
            for begin in range(0, len(planned_shots), 4):
                group = planned_shots[begin : begin + 4]
                output = self.director.writeImagePrompt(
                    {
                        "model": p["settings"]["image"]["model"],
                        "promptFormat": provider.getCapabilities()["promptFormat"],
                        "shots": [
                            {
                                "shotIndex": i,
                                "narration": s["narrationSegment"],
                                "draftPrompt": s["prompt"],
                            }
                            for i, s in enumerate(group)
                        ],
                        "instruction": "Refine the supplied drafts without deleting identity/current appearance constraints or adding any events. Keep each prompt concise. For SD tags, use fewer than 220 words. For natural-language models use complete sentences.",
                    },
                    self.gate,
                )
                passes.append(
                    {
                        "pass": "image-prompt-engineer",
                        "shots": [s["id"] for s in group],
                        "output": output,
                    }
                )
                for item in output["prompts"]:
                    if 0 <= item["shotIndex"] < len(group):
                        s = group[item["shotIndex"]]
                        s["directorPrompt"] = item["prompt"]
                        # The deterministic model adapter keeps canonical identity/state
                        # constraints intact; the director's refinement remains editable.
                        if provider.id != "existing":
                            # Creative refinement supplements, rather than replaces, the
                            # application-owned identity and current-state constraints.
                            s["prompt"] = (
                                s["prompt"] + " Visual direction: " + item["prompt"]
                            )
        self.director.stop()
        if selected_scene:
            index = ch["scenes"].index(selected_scene)
            scenes = (
                copy.deepcopy(ch["scenes"][:index])
                + scenes
                + copy.deepcopy(ch["scenes"][index + 1 :])
            )
            state = copy.deepcopy(original.get("state", state))
            memory = copy.deepcopy(original.get("memory", memory))

        def finish(latest):
            chapter = get_chapter(latest, chid)
            if chapter.get("scenes"):
                chapter["history"].append(
                    {
                        "time": time.time(),
                        "scenes": copy.deepcopy(chapter["scenes"]),
                        "analysis": chapter.get("analysis", {}),
                        "audio": chapter.get("audio", {}),
                    }
                )
            manual_shots = [
                s
                for scene in chapter["scenes"]
                for s in scene["shots"]
                if s.get("manual")
            ] + [s for s in chapter["scenes"] if s.get("manual")]
            if manual_shots and not options.get("replaceManual", False):
                chapter["proposedPlan"] = {
                    "scenes": scenes,
                    "people": people,
                    "analyses": analyses,
                }
                chapter["status"] = "READY_FOR_IMAGES"
                chapter["errors"].append(
                    {
                        "message": "New analysis is saved as proposedPlan. Existing manual shots were preserved. Apply it explicitly in the chapter editor."
                    }
                )
                return
            chapter.update(
                people=people,
                scenes=scenes,
                analysis={
                    "groups": analyses,
                    "passes": passes,
                    "providerRecommendation": workflow,
                },
                handoff={
                    "state": state,
                    "memory": memory,
                    "sourceSignature": digest(ch["sourceText"]),
                },
                inputState=state_before(latest, chid)[0],
                status="READY_FOR_IMAGES",
                renderStale=True,
                timeline=[
                    {
                        "shotId": s["id"],
                        "start": s["start"],
                        "end": s["end"],
                        "motion": s["motion"],
                        "transition": s["transition"],
                    }
                    for scene in scenes
                    for s in scene["shots"]
                ],
            )
            latest["renderStale"] = True
            latest["locations"] += locations
            latest["continuity"] = state
            latest["storyMemory"] = memory
            warn_dependents(latest, chapter, original)
            for c in latest["characters"]:
                enriched = next(
                    (person for person in p["characters"] if person["id"] == c["id"]), c
                )
                for field, value in enriched["permanentIdentity"].items():
                    if value and not c["permanentIdentity"].get(field):
                        c["permanentIdentity"][field] = value
                c["currentAppearance"] = state.get("characters", {}).get(
                    c["id"], c.get("defaultAppearance", {})
                )
                c["appearanceHistory"] = [
                    e
                    for e in state.get("appearanceHistory", [])
                    if e["characterId"] == c["id"]
                ]

        self.store.mutate(p["id"], finish)
        saved = self.store.load(p["id"])
        if not get_chapter(saved, chid).get("proposedPlan"):
            self.repair_identity_bindings(p["id"], chid)
        if p["settings"]["autoContinue"] and not options.get("managedPipeline"):
            latest = self.store.load(p["id"])
            c = get_chapter(latest, chid)
            needs_review = latest["settings"][
                "appearanceHandling"
            ] != "Automatic" and any(
                s["appearanceChanges"] and not s.get("appearanceChangesReviewed")
                for s in c["scenes"]
            )
            if not c.get("proposedPlan") and not needs_review:
                self.enqueue(
                    p["id"],
                    chid,
                    "image",
                    [s["id"] for scene in c["scenes"] for s in scene["shots"]],
                )

    def validate_ranges(self, items, count, label):
        if not items:
            raise ValueError("Director returned no " + label + "s.")
        cursor = 0
        for item in items:
            a, b = item["startSentence"], item["endSentence"]
            if a != cursor or b < a or b >= count:
                raise ValueError(
                    "Director scene boundaries do not cover narration in order. Retry analysis; no plan was overwritten."
                )
            cursor = b + 1
        if cursor != count:
            raise ValueError("Director omitted narration sentences. Retry analysis.")

    def validate_shot_ranges(self, items, scene, change_sentences=()):
        if not items:
            raise ValueError("Director returned a scene without shots.")
        cursor = scene["startSentence"]
        for item in items:
            if (
                item["startSentence"] != cursor
                or item["endSentence"] < cursor
                or item["endSentence"] > scene["endSentence"]
            ):
                raise ValueError(
                    f"Shot must start at sentence {cursor} and end between {cursor} and {scene['endSentence']}; received {item['startSentence']}..{item['endSentence']}."
                )
            if any(
                item["startSentence"] < n <= item["endSentence"]
                for n in change_sentences
            ):
                raise ValueError(
                    "A visual state change was buried inside an earlier shot. Start a separate shot at the requiredVisualChangeBoundary."
                )
            cursor = item["endSentence"] + 1
        if cursor != scene["endSentence"] + 1:
            raise ValueError("Director shots do not cover the scene narration.")

    def trim_shot_overlaps(self, items):
        # The director can propose several camera views for one sentence range.
        # Assign those views consecutive sentences, whose real TTS timings are
        # already known; do not duplicate narration or divide audio evenly.
        import itertools

        normalized = []
        for (start, end), views in itertools.groupby(
            items, key=lambda s: (s["startSentence"], s["endSentence"])
        ):
            views = list(views)
            length = end - start + 1
            if len(views) > 1 and length > 0:
                count = min(len(views), length)
                for index, view in enumerate(views[:count]):
                    view["startSentence"] = start + length * index // count
                    view["endSentence"] = start + length * (index + 1) // count - 1
                    view["timingRepair"] = {
                        "originalStartSentence": start,
                        "originalEndSentence": end,
                        "reason": "Assigned overlapping camera views consecutive narration sentences",
                    }
                if len(views) > count:
                    views[count - 1]["alternateDirections"] = [
                        dict(v) for v in views[count:]
                    ]
                normalized.extend(views[:count])
            else:
                normalized.extend(views)
        items = normalized
        for previous, current in zip(items, items[1:]):
            if (
                previous["startSentence"]
                < current["startSentence"]
                <= previous["endSentence"]
            ):
                previous["timingRepair"] = {
                    "originalEndSentence": previous["endSentence"],
                    "reason": "Trimmed overlap at the next director-selected shot boundary",
                }
                previous["endSentence"] = current["startSentence"] - 1
        return items

    def generate(self, p, chid, sid, seed, options):
        self.director.stop()
        self.before_image()
        ch = get_chapter(p, chid)
        shot = options.get("shotSnapshot") or get_shot(p, chid, sid)
        provider = self.provider(shot["imageProvider"])
        if provider.id != "native-flux":
            self.providers["native-flux"].unload()
        if provider.id != "existing":
            self.providers["existing"].unload()
        self.status(p["id"], chid, "GENERATING")
        settings = shot["generationSettings"] | {
            "model": shot["imageModel"],
            "seed": seed,
        }
        references, ref_metadata = select_references(p, shot, self.store, provider)
        if provider.id == "native-flux" and options.get("operation") == "edit":
            references = references[:1]
            ref_metadata = ref_metadata[:1]
        request = {
            "prompt": shot["prompt"],
            "negativePrompt": shot["negativePrompt"],
            "referenceImages": references,
            "settings": settings,
            "operation": options.get("operation", "generate"),
        }
        if provider.id == "native-flux" and references:
            offset = 1 if request["operation"] == "edit" else 0
            reference_notes = []
            for index, ref in enumerate(ref_metadata, 1 + offset):
                if ref.get("characterId"):
                    person = next(
                        c
                        for c in p["characters"] + ch["people"]
                        if c["id"] == ref["characterId"]
                    )
                    appearance = next(
                        c for c in shot["characters"] if c["id"] == person["id"]
                    )["appearanceState"]
                    reference_notes.append(
                        f'Use image {index} as the identity reference for {person["name"]}. Preserve face and permanent traits. Current appearance overrides reference clothing: {json.dumps(appearance)}.'
                    )
                elif ref.get("locationId"):
                    reference_notes.append(
                        f"Use image {index} for the location architecture and palette."
                    )
            request["prompt"] = " ".join(reference_notes) + " " + request["prompt"]
        if request["operation"] in ("edit", "inpaint"):
            source = shot.get("sourceImagePath") or shot.get("imagePath")
            if not source:
                raise ValueError(
                    "Image repair needs a generated or uploaded source image."
                )
            request["sourceImage"] = data_url(self.store.asset(p["id"], source))
            request["mask"] = options.get("mask")
        retries = int(p["settings"]["maxImageRetries"])
        qc = {"status": "UNREVIEWED", "pass": None}
        attempts = []
        fallback_used = False
        actual_workflow = shot["workflow"]
        max_attempts = (
            retries + 1 + (1 if p["settings"]["image"].get("fallbackEnabled") else 0)
        )
        for attempt in range(max_attempts):
            self.gate(f'Generating {ch["name"]} · shot {sid} · attempt {attempt+1}')
            try:
                result = provider.generateImage(request, self.checkpoint)
            except (JobCancelled, AudioYield):
                raise
            except Exception as e:
                attempts.append(
                    {
                        "attempt": attempt,
                        "error": str(e),
                        "seed": seed,
                        "provider": provider.id,
                        "settings": settings,
                        "time": time.time(),
                    }
                )
                fallback = p["settings"]["image"].get("fallback")
                if (
                    not fallback_used
                    and p["settings"]["image"].get("fallbackEnabled")
                    and fallback
                    and provider.id != fallback["provider"]
                ):
                    fallback_used = True
                    provider.unload()
                    provider = self.provider(fallback["provider"])
                    settings = (
                        provider.getRecommendedSettings() | fallback | {"seed": seed}
                    )
                    request["settings"] = settings
                    prompt, negative = format_prompt(p, shot, provider)
                    request.update(
                        prompt=prompt,
                        negativePrompt=negative,
                        referenceImages=references[
                            : provider.getCapabilities().get("maxReferenceImages", 0)
                        ],
                    )
                    actual_workflow = fallback.get("workflow") or (
                        "inpaint"
                        if request["operation"] in ("edit", "inpaint")
                        else "ip-adapter" if references else "text-to-image"
                    )
                elif attempt >= retries + int(fallback_used):
                    raise
                continue
            folder = self.store.folder(p["id"]) / chid
            folder.mkdir(exist_ok=True)
            filename = f"{sid}-{uid()}-a{attempt}.png"
            path = folder / filename
            tmp = path.with_suffix(".partial.png")
            result.pop("pil").save(tmp, "PNG")
            tmp.replace(path)
            relative = str(path.relative_to(self.store.folder(p["id"]))).replace(
                "\\", "/"
            )
            metadata = {
                "provider": provider.id,
                "model": settings["model"],
                "workflow": actual_workflow,
                "sampler": settings.get("sampler"),
                "scheduler": settings.get("scheduler"),
                "seed": seed,
                "resolution": [settings["width"], settings["height"]],
                "loras": settings.get("loras", []),
                "controlnets": settings.get("controlnets", []),
                "ipAdapter": settings.get("ipAdapter", {}),
                "denoisingStrength": settings.get("denoisingStrength"),
                "referenceImages": ref_metadata,
                "sourceImagePath": shot.get("sourceImagePath"),
                "prompt": request["prompt"],
                "negativePrompt": request["negativePrompt"],
                "settings": settings,
                "result": result,
                "time": time.time(),
                "attempt": attempt,
                "intentionalAppearanceChanges": shot["intentionalAppearanceChanges"],
                "fallbackUsed": fallback_used,
            }
            attempts.append(metadata)

            # Save before any QC request so a cancel or crash cannot erase the PNG.
            def saved(latest):
                s = get_shot(latest, chid, sid)
                if s.get("imagePath"):
                    s["history"].append(
                        {
                            "imagePath": s["imagePath"],
                            "metadata": s.get("imageMetadata", {}),
                            "qc": s.get("qc", {}),
                        }
                    )
                s.update(
                    imagePath=relative,
                    imageMetadata=metadata,
                    referenceImages=ref_metadata,
                    retryCount=attempt,
                    status="QC",
                    generationError="",
                )
                s["generationStale"] = (
                    s["prompt"] != shot["prompt"]
                    or s["imageModel"] != settings["model"]
                )
                get_chapter(latest, chid)["renderStale"] = True
                latest["renderStale"] = True

            self.store.mutate(p["id"], saved)
            path.with_suffix(".json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            if (
                p["settings"]["visionQC"]
                or p["settings"]["generationMode"] == "MAX QUALITY"
            ):
                provider.unload()
                self.providers["existing"].unload()
                self.status(p["id"], chid, "QC")
                qc = self.visual_check(p, shot, path)
                qc["status"] = (
                    "PASSED"
                    if qc.get("pass") is True
                    else "FAILED" if qc.get("pass") is False else "UNREVIEWED"
                )
            if (
                qc.get("pass") is False
                and p["settings"]["automaticRepair"]
                and attempt < retries
            ):
                self.status(p["id"], chid, "REPAIRING")
                request["prompt"] = qc.get("repairPrompt") or shot["prompt"]
                if (
                    provider.getCapabilities()["supportsImageEditing"]
                    and provider.id != "existing"
                ):
                    request.update(operation="edit", sourceImage=data_url(path))
                if request.get("operation") == "edit":
                    shot["sourceImagePath"] = relative
                continue
            break

        def complete(latest):
            s = get_shot(latest, chid, sid)
            s.update(
                qc=qc,
                status=(
                    "PASSED"
                    if qc.get("pass") is True
                    else "FAILED" if qc.get("pass") is False else "COMPLETE"
                ),
                retryHistory=attempts,
            )
            chapter = get_chapter(latest, chid)
            allshots = [x for sc in chapter["scenes"] for x in sc["shots"]]
            chapter["status"] = (
                "COMPLETE"
                if all(
                    x.get("imagePath") and x["status"] in ("PASSED", "COMPLETE")
                    for x in allshots
                )
                else "READY_FOR_IMAGES"
            )

        self.store.mutate(p["id"], complete)
        if qc.get("pass") is False:
            raise RuntimeError(
                "Visual QC still found problems after the retry limit. The last image was saved for manual review."
            )

    def visual_check(self, p, shot, path):
        self.select_director(p)
        references, _ = select_references(
            p, shot, self.store, self.provider(shot["imageProvider"])
        )
        expected = {
            k: shot.get(k)
            for k in (
                "characters",
                "camera",
                "location",
                "action",
                "pose",
                "expression",
                "lighting",
                "continuity",
                "intentionalAppearanceChanges",
            )
        }
        expected["mainIdentities"] = [
            {"id": c["id"], "name": c["name"], "identity": c["permanentIdentity"]}
            for c in p["characters"]
            if any(s["id"] == c["id"] for s in shot["characters"])
        ]
        result = self.director.inspectGeneratedImage(
            {
                "expectedShot": expected,
                "strictness": p["settings"]["continuityStrictness"],
                "intentionalChanges": shot["intentionalAppearanceChanges"],
                "imageOrder": "Generated shot first, followed by available character identity references. Compare clothing to current state, not reference clothing.",
                "_images": [data_url(path), *references[:2]],
            },
            self.gate,
        )
        self.director.stop()
        return result

    def inspect_shot(self, p, chid, sid):
        shot = get_shot(p, chid, sid)
        if not shot.get("imagePath"):
            raise ValueError("Generate the image before visual review.")
        self.unload_models()
        self.before_image()
        self.providers["existing"].unload()
        self.status(p["id"], chid, "QC")
        qc = self.visual_check(p, shot, self.store.asset(p["id"], shot["imagePath"]))
        qc["status"] = (
            "PASSED"
            if qc.get("pass") is True
            else "FAILED" if qc.get("pass") is False else "UNREVIEWED"
        )
        self.store.mutate(
            p["id"],
            lambda q: get_shot(q, chid, sid).update(
                qc=qc,
                status=(
                    "PASSED"
                    if qc.get("pass") is True
                    else "FAILED" if qc.get("pass") is False else "COMPLETE"
                ),
            ),
        )

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        self.unload_models()
        self.thread.join(5)
        if not self.thread.is_alive():
            self.db.close()
