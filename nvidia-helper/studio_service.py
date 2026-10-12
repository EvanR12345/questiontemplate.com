"""Persistent production jobs: audio, directing, shot generation, QC and renders."""

import base64, copy, hashlib, io, json, math, os, re, secrets, sqlite3, threading, time, traceback, wave
from pathlib import Path
from studio_data import *
from director_provider import LocalQwenDirector
from openai_director import OpenAIDirector
from image_provider import (
    ExistingImageProvider,
    NativeFluxProvider,
    ComfyImageProvider,
    RemoteGenerationUncertain,
    format_prompt,
    select_references,
    reference_prompt,
    visible_appearance,
    data_url,
)
from studio_render import VideoRenderer
from studio_gpu_render import create_renderer
from studio_qc import decision as qc_decision, production_status, automatic_repairs, repair_budget_reason, check_level, should_check, policy as qc_policy, review_settings_signature, review_settings_stale
from prompt_quality import generation_measurements
from story_grounding import apply_environment_event, set_environment_location, visual_environment, require_owners
from production_trace import ProductionTrace
from prompt_preflight import require_shot
from queue_estimates import remaining_time, timing_profile
from studio_performance import PerformanceStore,profile_for,observation_units,signature as performance_signature,host_execution_gaps,host_interruption_details
from narration_audio import VERSION as AUDIO_DELIVERY_VERSION, FLOW_VERSION, audio_segments, effect_pcm, create_connected_audio, speech_pcm


class JobCancelled(Exception):
    pass


class AudioYield(Exception):
    pass


class CloudBudgetPaused(ValueError):
    """Preserve the current seed and waiting jobs when rented time runs out."""


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
        self.trace = ProductionTrace(self.store.root / 'production-trace.sqlite3')
        self.performance = PerformanceStore(self.store.root / 'performance.sqlite3')
        self.pause_epoch = 0
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
        self.renderer = create_renderer(self.store, self.config)
        self.renderer.prepare_outro = self.prepare_outro
        from studio_storage import R2Archive
        storage_config = self.config_path.with_name('.storage-secrets.json')
        self.storage = R2Archive(self.store, storage_config if storage_config.exists() else self.config_path.with_name('.r2-secrets.json'))
        from studio_publisher import CloudPublisher
        self.publisher=CloudPublisher(self.config_path.with_name('.publisher-secrets.json'))
        self.cv = threading.Condition(threading.RLock())
        self.current = None
        self.cancel = False
        self.yield_requested = False
        self.closed = False
        from studio_execution import ExecutionJournal
        self.execution_journal = ExecutionJournal(self.store.root / 'executions.sqlite3')
        self.active_executions = {}
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

    @staticmethod
    def append_queue_timing(project, job):
        """Record direct jobs once per attempt; parent pipelines measure their stages."""
        stages = {'image':'Image generation', 'qc':'Visual QC', 'qc-intro':'Intro visual QC',
                  'intro-image':'Intro images', 'character-reference':'Character reference',
                  'render-chapter':'Render chapter', 'render-full':'Assemble full video',
                  'intro-render':'Render intro', 'narration':'Narration', 'intro-audio':'Intro narration',
                  'analyze':'Chapter analysis (audio + directing)', 'scene-plan':'Chapter analysis (audio + directing)',
                  'retarget-images':'Image prompt planning', 'voice-preview':'Voice preview'}
        if job['kind'] not in stages or job.get('seconds') is None:
            return
        timings = project.setdefault('production', {}).setdefault('timings', [])
        if any(t.get('jobId') == job['id'] and t.get('attemptStarted') == job['started'] for t in timings):
            return
        chapter = next((c['number'] for c in project['chapters'] if c['id'] == job.get('chapter')), None)
        timings.append({'stage':stages[job['kind']], 'seconds':round(job['seconds'], 3),
                        'finished':job['started'] + job['seconds'], 'jobId':job['id'],
                        'attemptStarted':job['started'], 'chapter':chapter, 'shot':job.get('shot'),
                        'status':job['status'], 'timingSource':'queue-attempt',
                        'paused':job.get('pausedDuringAttempt',False),
                        'performanceProfile':job.get('performanceProfile'),
                        'performanceUnits':job.get('performanceUnits'),
                        'performanceRun':job['id'],
                        'performanceId':performance_signature({'job':job['id'],'started':job['started']})})

    def sync_queue_states(self, project_id=None, completed_job=None):
        """Restore persistent item states without touching completed image assets."""
        with self.cv:
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT project,chapter,shot,status FROM jobs" + (' WHERE project=?' if project_id else '') + ' ORDER BY created',
                    (project_id,) if project_id else (),
                )
            ]
        latest = {(r["project"], r["chapter"], r["shot"]): r for r in rows}
        byproject = {}
        for r in latest.values():
            if r["chapter"]:
                byproject.setdefault(r["project"], []).append(r)
        if completed_job:
            byproject.setdefault(completed_job['project'], [])
        for pid, items in byproject.items():

            def update(p):
                if completed_job and completed_job['project'] == pid:
                    self.append_queue_timing(p, completed_job)
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
                self.store.mutate(pid, update, skip_unchanged=True)
            except FileNotFoundError:
                pass

    def unload_models(self):
        self.director.stop()
        self.providers["native-flux"].unload()

    def provider_status(self, key, provider):
        """Reconnect does not wait for a deliberately stopped paid worker.

        A declared catalogue is not live readiness. The production lease and
        generation path still perform real model/workflow checks before use.
        """
        managed = self.config.get('runpodManagedPodId') or self.config.get('runpodPreparedPodIds')
        if key=='comfyui' and managed:
            lease_path=self.store.root/'.managed-rental.json'
            try:state=json.loads(lease_path.read_text(encoding='utf-8')) if lease_path.exists() else {}
            except (OSError,ValueError):state={'status':'UNKNOWN'}
            status=state.get('status')
            if status in (None,'STOPPED','UNKNOWN'):
                try:catalog=provider.planningCatalog()
                except Exception:catalog={'installed':False,'configured':False,'requiresLiveValidation':True}
                return catalog | {'installed':False,'workerState':status or 'UNVERIFIED',
                                  'liveProbeSkipped':True,'requiresLiveValidation':True}
        return provider.healthCheck()

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
            "cloudOverlapAvailable":True,
            "cloudSchedulingAvailable":True,
            "unattendedProductionAvailable":True,
            "stagedDirectorAvailable":True,
            "leanDirectorAvailable":True,
            "leanPackingAvailable":True,
            "sourceVisualOverlapAvailable":True,
            "leanFactBeatsAvailable":True,
            "maxDirectorTasks":8,
            "managedGPU":{'configured':bool(self.config.get('runpodManagedPodId') or self.config.get('runpodPreparedPodIds')),
                          'podId':self.config.get('runpodManagedPodId',''),'watchdogLocation':'local-helper',
                          'requiredGPU':self.config.get('runpodRequiredGPUType',''),
                          'preparedWorkers':len(self.config.get('runpodPreparedPodIds') or []),
                          'waitSeconds':self.config.get('runpodAvailabilityWaitSeconds',0),
                          'maxHourlyUSD':self.config.get('runpodMaxHourlyUSD'),
                          'checkRegionalStock':bool(self.config.get('runpodCheckRegionalStock'))},
            "storage": self.storage.status(),
            "hardware": hardware,
            "director": self.director.healthCheck(),
            "directorProviders": {
                "local-qwen": LocalQwenDirector(self.config, self.store.root).healthCheck(),
                "openai-luna": OpenAIDirector(self.config, self.store.root).healthCheck(),
            },
            "providers": {
                k: self.provider_status(k,v)
                | {
                    "capabilities": v.getCapabilities(),
                    "recommended": v.getRecommendedSettings(),
                }
                for k, v in self.providers.items()
            },
            "renderer": {"installed": Path(self.config.get("ffmpeg", "")).is_file(),
                         "backend": 'native' if not self.renderer.allow_legacy_render_reuse else 'cpu',
                         "note": getattr(self.renderer,'backend_note','CPU photo-motion renderer.')},
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
            "comfyPollInterval",
            "comfyGenerationTimeoutSeconds",
            "ffmpeg",
            "renderWorkers",
            "renderBackend",
            "font",
            "openaiApiKey",
            "openaiPromptCacheMode",
            "openaiServiceTier",
            "runpodApiKey",
            "runpodManagedPodId",
            "runpodPreparedPodIds",
            "runpodRequiredGPUType",
            "runpodAvailabilityWaitSeconds",
            "runpodMaxHourlyUSD",
            "runpodCheckRegionalStock",
            "runpodBootstrapKleinPodIds",
            "runpodSSHKeyFile",
            "runpodCLI",
            "runpodActionKeyFile",
        }
        if not isinstance(data, dict) or any(k not in allowed for k in data):
            raise ValueError("Unknown helper configuration option.")
        if 'runpodManagedPodId' in data and data['runpodManagedPodId'] and not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',str(data['runpodManagedPodId'])):
            raise ValueError('Enter the existing reusable Runpod pod ID, or leave blank for external management.')
        if data.get('runpodSSHKeyFile') and not Path(data['runpodSSHKeyFile']).is_file():
            raise ValueError('The dedicated private SSH key file does not exist.')
        if 'runpodPreparedPodIds' in data and not isinstance(data['runpodPreparedPodIds'],list):
            raise ValueError('Prepared worker IDs must be a list.')
        if 'runpodPreparedPodIds' in data and data['runpodPreparedPodIds']:
            from studio_runpod import candidate_ids
            candidate_ids(data)
        if 'runpodBootstrapKleinPodIds' in data:
            values=data['runpodBootstrapKleinPodIds']
            if not isinstance(values,list) or len(values)>20 or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',v) for v in values):
                raise ValueError('Klein bootstrap requires explicit prepared worker IDs.')
            effective=self.config|data
            from studio_runpod import candidate_ids
            if values and not set(values).issubset(candidate_ids(effective)):
                raise ValueError('Model restore workers must belong to the selected prepared pool.')
        if 'runpodRequiredGPUType' in data and (not isinstance(data['runpodRequiredGPUType'],str) or len(data['runpodRequiredGPUType'])>128):
            raise ValueError('Enter the exact GPU type ID.')
        for field,minimum,maximum in (('runpodAvailabilityWaitSeconds',0,86400),('runpodMaxHourlyUSD',.01,100)):
            if field=='runpodMaxHourlyUSD' and data.get(field) is None:continue
            if field in data and (type(data[field]) not in (int,float) or not math.isfinite(data[field]) or not minimum<=data[field]<=maximum):
                raise ValueError('Invalid GPU availability wait or hourly price limit.')
        if 'runpodCheckRegionalStock' in data and type(data['runpodCheckRegionalStock']) is not bool:
            raise ValueError('Regional stock checking must be on or off.')
        for field in ('runpodCLI','runpodActionKeyFile'):
            if data.get(field) and not Path(data[field]).is_file():
                raise ValueError('The configured existing Runpod control file does not exist.')
        if 'renderWorkers' in data and (isinstance(data['renderWorkers'],bool)
                or not isinstance(data['renderWorkers'],int) or not 1 <= data['renderWorkers'] <= 3):
            raise ValueError('Choose one to three render workers.')
        if 'renderBackend' in data and data['renderBackend'] not in ('cpu','native'):
            raise ValueError('Choose CPU or local NVIDIA GPU rendering.')
        if 'openaiPromptCacheMode' in data and data['openaiPromptCacheMode'] not in ('explicit', 'implicit'):
            raise ValueError('Choose explicit or implicit prompt caching.')
        if 'openaiServiceTier' in data and data['openaiServiceTier'] not in ('default', 'flex','fast'):
            raise ValueError('Choose Standard or Flex Luna processing.')
        for field, minimum, maximum in (('comfyPollInterval', .1, 2), ('comfyGenerationTimeoutSeconds', 30, 3600)):
            if field in data and (isinstance(data[field], bool) or not isinstance(data[field], (int, float))
                    or not math.isfinite(data[field]) or not minimum <= data[field] <= maximum):
                raise ValueError(f'{field} must be between {minimum} and {maximum}.')
        with self.cv:
            if self.current:
                raise ValueError(
                    "Pause and cancel the current operation before changing runtime paths."
                )
            self.unload_models()
            data = dict(data)
            secret_updates = {}
            for field in ("openaiApiKey", "runpodApiKey"):
                if field not in data:
                    continue
                key = str(data.pop(field)).strip()
                if key and (len(key) < 20 or field == "openaiApiKey" and not key.startswith("sk-")):
                    raise ValueError("Enter a valid " + ("OpenAI" if field == "openaiApiKey" else "Runpod") + " API key.")
                secret_updates[field] = key
            if secret_updates:
                path = self.config_path.with_name(".studio-secrets.json")
                saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
                saved.update(secret_updates)
                temporary = path.with_suffix(".json.tmp")
                temporary.touch(exist_ok=True)
                if os.name == "nt":
                    import subprocess
                    owner = subprocess.check_output(["whoami"], text=True, creationflags=subprocess.CREATE_NO_WINDOW).strip()
                    subprocess.run(["icacls", str(temporary), "/inheritance:r", "/grant:r", owner + ":(F)",
                                    "NT AUTHORITY\\SYSTEM:(F)", "BUILTIN\\Administrators:(F)"],
                                   check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                else:
                    temporary.chmod(0o600)
                temporary.write_text(json.dumps(saved), encoding="utf-8")
                temporary.replace(path)
                if "openaiApiKey" in saved:
                    self.config["openaiKeyFile"] = str(path)
                if "runpodApiKey" in saved:
                    self.config["runpodKeyFile"] = str(path)
            self.config.update(data)
            self.config_path.write_text(
                json.dumps(self.config, indent=2), encoding="utf-8"
            )
            self.director.config = self.config
            self.providers["native-flux"].config = self.config
            self.providers["comfyui"] = ComfyImageProvider(self.config)
            if 'renderBackend' in data:
                previous_renderer=self.renderer
                self.renderer=create_renderer(self.store,self.config)
                self.renderer.prepare_outro=self.prepare_outro
                if callable(getattr(previous_renderer,'on_output',None)):
                    self.renderer.on_output=previous_renderer.on_output
            else:
                self.renderer.config=self.config
        return self.health()

    def prepare_outro(self, p):
        from studio_engagement import settings, spoken_text, audio_signature
        s=settings(p)
        if not s['outroEnabled']: return p
        expected=audio_signature(p)
        if s['outroAudioSignature']==expected and s['outroAudioPath'] and self.store.asset(p['id'],s['outroAudioPath']).is_file(): return p
        self.before_audio()
        name=f'outro-narration-{expected[:20]}.wav'
        self.create_audio(spoken_text(s),p['settings']['voice'],p['settings']['speed'],
            self.store.folder(p['id'])/name,'off',p['settings'].get('narrationDelivery','standard'))
        def saved(q):
            if audio_signature(q)!=expected: raise ValueError('Outro changed while its narration was prepared. Prior audio retained.')
            q['settings'].setdefault('engagement',{}).update(outroAudioPath=name,outroAudioSignature=expected)
        self.store.mutate(p['id'],saved)
        p['settings'].setdefault('engagement',{}).update(outroAudioPath=name,outroAudioSignature=expected)
        return p

    def snapshot(self):
        with self.cv:
            rows = [
                dict(r)
                for r in self.db.execute(
                    "SELECT id,project,chapter,shot,kind,status,message,priority,created,started,seconds,attempt,seed,payload FROM jobs WHERE status IN ('QUEUED','RUNNING') OR id IN (SELECT id FROM jobs ORDER BY created DESC LIMIT 1100) ORDER BY created DESC"
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
            samples = {}
            for row in rows:
                payload = json.loads(row.pop("payload"))
                row["timingProfile"] = timing_profile(row['kind'], payload)
            for kind in {j["timingProfile"] for j in rows}:
                durations = [
                    r["seconds"]
                    for r in rows
                    if r["timingProfile"] == kind
                    and r["status"] == "COMPLETE"
                    and r["seconds"] > 0
                ][:30]
                if durations:
                    samples[kind] = durations
                    averages[kind] = sum(durations) / len(durations)
            pending = [r for r in rows if r["status"] in ("QUEUED", "RUNNING")]
            estimate = remaining_time(pending, samples, time.time())
            return {
                "paused": self.paused,
                "current": self.current,
                "counts": counts,
                "projectCounts": project_counts,
                **estimate,
                "etaSampleCountsByProfile": {kind: len(values) for kind, values in samples.items()},
                "averageSecondsByKind": averages,
                "jobs": rows,
                "outputFolder": str(self.store.root),
                "executions": (self.execution_journal.recent()
                    if hasattr(self,'execution_journal') else []),
                "storage": self.storage.status(),
            }

    def production_readiness(self, pid, options=None):
        """Read-only checks before the first potentially paid production stage."""
        import shutil
        p=self.store.load(pid);options=options or {};checks=[]
        unattended=options.get('unattended') is True
        if unattended:
            from studio_automation import run_settings
            p['settings']=run_settings(p['settings'])
        from studio_schedule import strategy,LABELS
        selected_strategy=strategy(options)
        def check(name, ready, message, blocking=True):
            checks.append({'name':name,'ready':bool(ready),'message':message,'blocking':blocking})
        if unattended:
            from studio_automation import project_cap_reason
            cap_reason=project_cap_reason(p['settings'])
            check('unattended-caps',cap_reason is None,cap_reason or 'Automatic production uses the existing saved caps; no spending limit is raised.')
        chapters=[c for c in p['chapters'] if c['sourceText'].strip()]
        check('chapters',bool(chapters),'Story chapters are ready.' if chapters else 'Paste at least one chapter.')
        from story_grounding import owner_conflicts
        bad_sources=[chapter for chapter in chapters if owner_conflicts(
            [event for group in chapter.get('analysis',{}).get('groups',[]) for event in group.get('changes',[])],
            p['characters']+chapter.get('people',[]))]
        if bad_sources:
            names=', '.join(chapter['name'] for chapter in bad_sources)
            check('source-grounding',False,
                  f'Saved source ownership needs correction in {names}. Reanalyze before generating new images. Existing videos and originals stay saved.',False)
        unresolved=self.execution_journal.unresolved_project(pid)
        check('recovery',not unresolved,'Saved remote work is resolved.' if not unresolved else
              'Reconcile the saved remote request IDs before starting; no automatic duplicate purchases.')
        try:voice=bool(self.audio.health().get('backend'))
        except Exception:voice=False
        check('voice',voice,'Voice generation stays on this laptop.' if voice else 'The local voice helper is unavailable.')
        check('renderer',Path(self.config.get('ffmpeg','')).is_file(),
              'Local video renderer must be installed; cloud rendering is an optional separate export.')
        provider_name=p['settings']['image']['provider']
        check('cloud-images',not p['settings'].get('cloudImagesOnly') or provider_name=='comfyui',
              'Cloud-only projects require their configured cloud image provider.')
        images_needed=any(not c['scenes'] or any(not s.get('imagePath') or s.get('generationStale') or not self.store.has_asset(pid,s['imagePath'])
                          for scene in c['scenes'] for s in scene['shots']) for c in chapters)
        saved_shots=[s for c in chapters for scene in c['scenes'] for s in scene['shots']]
        if p['intro']['enabled']:
            images_needed |= bool(not p['intro'].get('visualPath') and not p['intro'].get('shots'))
            images_needed |= any(not s.get('imagePath') or not self.store.has_asset(pid,s['imagePath'])
                                 for s in p['intro'].get('shots',[]))
            saved_shots += p['intro'].get('shots',[])
            if p['intro'].get('visualPath') and not p['intro'].get('shots'):
                images_needed |= not self.store.has_asset(pid,p['intro']['visualPath'])
        # The same preserved findings will stop full production before rendering.
        # Report them before any unrelated new chapter/GPU/API work is bought.
        # Pending checks can resume; stale/missing images can regenerate. Explicit
        # acceptance, Off and permitted automatic repairs retain their semantics.
        review_context={'files':{},'reviews':{},'chapters':{c['id']:c for c in p['chapters']},
                        'intro':{s['id'] for s in p['intro'].get('shots',[])}}
        blocking_reviews=[s for s in saved_shots if s.get('imagePath') and not s.get('generationStale')
                      and s.get('qc',{}).get('pass') is False
                      and s.get('qc',{}).get('status')!='PENDING'
                      and self.store.has_asset(pid,s['imagePath'])
                      and qc_decision(p,s,self.store,review_context)['blocking']]
        review_flags=[s for s in blocking_reviews if not automatic_repairs(p['settings'],s.get('qc',{}))]
        if unattended and any(s.get('imagePath') and qc_decision(p,s,self.store,review_context)['blocking'] for s in saved_shots): images_needed=True
        check('saved-visual-reviews',not review_flags or unattended,
              'Saved major findings will be repaired automatically within the saved caps; warnings remain visible.' if unattended and blocking_reviews else
              f'{len(review_flags)} saved images need review before the full video can finish. Review or explicitly repair those shots first; completed images and findings stay saved.'
              if review_flags else 'No saved visual findings currently block the full video.')
        from studio_runpod import configured
        managed=configured(self.config)
        if images_needed:
            if (selected_strategy=='align' or managed) and provider_name=='comfyui':
                try:
                    provider=self.provider(provider_name);catalog=provider.planningCatalog();image=p['settings']['image']
                    configured=image['model'] in catalog['models'] and image['workflow'] in catalog['workflow']
                    provider.validateSettings(image,check_hardware=False)
                except Exception:configured=False
                check('images',configured,'Declared image workflow is configured for offline planning; this is not proof of a running worker.' if configured else
                    'Configure the selected image model/workflow before planning. No fallback was started.')
                if managed:
                    window=p['settings'].get('cloudWindow',{});cap=window.get('gpuBudgetUSD')
                    check('gpu-cap',type(cap) in (int,float) and math.isfinite(cap) and cap>0,
                          'Automatic GPU startup needs a positive project GPU cap; its live rate is checked before startup.')
                    check('gpu-connection',Path(self.config.get('runpodSSHKeyFile','')).is_file()
                          and Path(self.config.get('runpodKeyFile','')).is_file(),
                          'Managed image generation needs the existing dedicated SSH key and private Runpod management credential.')
                    lease=self.store.root/'.managed-rental.json'
                    try:lease_state=json.loads(lease.read_text()) if lease.exists() else {}
                    except (OSError,ValueError):lease_state={'status':'UNKNOWN'}
                    check('gpu-recovery',lease_state.get('status') in (None,'STOPPED'),
                          'Earlier managed rental must be confirmed stopped before another run.')
                else:
                    check('image-dispatch',False,'The image GPU may stay stopped during planning. Connect it before dispatch; unavailable live generation stops with saved plans, rather than renting a GPU automatically.',False)
            else:
                try:installed=bool(self.provider(provider_name).healthCheck().get('installed'))
                except Exception:installed=False
                check('images',installed,'Image worker and workflow are available.' if installed else
                      'The selected image worker is unavailable. Connect the installed model before generation; no fallback was started.')
        director_name=p['settings']['director'].get('provider','local-qwen')
        director=OpenAIDirector(self.config,self.store.root) if director_name=='openai-luna' else LocalQwenDirector(self.config,self.store.root)
        check('director',director.healthCheck().get('installed'),
              'Selected director credentials/model must be configured.')
        if director_name=='openai-luna':
            cap=p['settings'].get('budget',{}).get('openaiUSD',0)
            valid=type(cap) in (int,float) and math.isfinite(cap) and cap>0
            check('api-budget',valid,'Set a positive API spending cap in Advanced settings before production.')
        if options.get('overlap'):
            try:
                from studio_overlap import validate_overlap
                validate_overlap(p,options);valid=True
            except ValueError:valid=False
            check('overlap',valid,'Overlap must match the selected cloud providers, checks and API budget.')
        free=shutil.disk_usage(self.store.folder(pid)).free
        check('scratch',free>=512*2**20,
              f'Local temporary disk: {free/2**30:.2f} GiB free. Long-video space is checked again against actual rendered inputs.')
        if free<2*2**30:
            check('scratch-headroom',False,
                  'Limited local scratch space: cloud archiving does not remove local working files. Free space before a long generation; this setup check is not a guarantee that the complete video will fit.',False)
        storage=self.storage.status(pid)
        check('archive',storage.get('enabled') and storage.get('provider')=='Google Cloud Storage',
              'Permanent project and media archive must be connected to Google storage.')
        check('rental',managed,'Studio starts only the configured stopped pod at image dispatch and confirms shutdown before rendering. Keep this helper and laptop awake: the deadline watchdog runs here; network failure can delay shutdown.' if managed else
              'A paused Studio queue does not stop a manually rented GPU. Keep its rental window bounded.',False)
        check('strategy',True,LABELS[selected_strategy]+'. Uncalibrated alignment plans chapters first.',False)
        level=check_level(p['settings'])
        check('visual-checks',level!='off',
              f'Visual checks: {level}. Technical decoder/black-image checks always remain active.',False)
        if p['settings'].get('automaticRepair'):
            reason=repair_budget_reason(p['settings'])
            check('automatic-repairs',automatic_repairs(p['settings']),
                  reason or ('Automatic repairs are enabled for confirmed major errors.' if qc_policy(p['settings'])=='practical' else 'Bounded automatic repairs are enabled.'),False)
        return {'project':pid,'revision':p['revision'],'ready':all(x['ready'] for x in checks if x['blocking']),
                'generationStrategy':selected_strategy,'rentalControl':'managed' if managed else 'external',
                'voiceLocation':'local','renderLocation':'local','checks':checks,'paidRequestsSubmitted':0}

    def enqueue(self, pid, chapter, kind, shots=None, options=None):
        p = self.store.load(pid)
        self.require_resolved_execution(pid,kind)
        options = options or {}
        if 'unattended' in options and type(options['unattended']) is not bool:
            raise ValueError('Unattended production must be enabled or disabled.')
        if kind=='produce-story':
            from studio_schedule import strategy
            strategy(options)
            if options.get('unattended'):
                from studio_automation import project_cap_reason, run_settings
                reason=project_cap_reason(run_settings(p['settings']))
                if reason:raise ValueError(reason)
        if options.get('preflightRevision') is not None and options['preflightRevision']!=p['revision']:
            raise ValueError('Project changed after readiness checks. Check the current project before starting.')
        if 'overlap' in options and not isinstance(options['overlap'],bool):
            raise ValueError('Cloud overlap must be explicitly enabled or disabled.')
        ch = get_chapter(p, chapter) if chapter else None
        if kind == 'voice-preview':
            chapter = None
            ch = None
        if kind not in (
            "produce-story",
            "prepare-story",
            "restyle-story",
            "economy-images",
            "analyze",
            "scene-plan",
            "narration",
            "voice-preview",
            "outro-audio",
            "split-video",
            "image",
            "qc",
            "character-reference",
            "render-chapter",
            "render-full",
            "intro-audio",
            "intro-image",
            "intro-render",
            "qc-intro",
            "retarget-images",
        ):
            raise ValueError("Unknown studio operation.")
        if kind in ("qc", "qc-intro") and p["settings"].get("qcCheckLevel") == "off":
            raise ValueError("Visual checks are Off. Enable checks in Settings before queuing a review.")
        if kind == "produce-story":
            if options.get('overlap'):
                from studio_overlap import validate_overlap
                validate_overlap(p, options)
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
                requested = shot['generationSettings'] | {'model': shot['imageModel']}
                if kind == 'qc':
                    # Reviewing a saved PNG does not need the image GPU online.
                    settings = copy.deepcopy(requested)
                elif provider.id == 'comfyui':
                    # Validate structure while preparing the queue. Actual
                    # generation still checks the live worker, VRAM and weights.
                    settings = provider.validateSettings(requested, check_hardware=False)
                else:
                    settings = provider.validateSettings(requested)
                shot["generationSettings"] = settings
                seed = settings["seed"]
            else:
                seed = None
            planned.append(
                (
                    uid("job-"),
                    sid,
                    seed,
                    options | ({"shotSnapshot": copy.deepcopy(shot), "qcTimingProfile": [check_level(p["settings"]), p["settings"].get("qcSampleEvery",5), p["settings"].get("qcPolicy","practical"), p["settings"]["director"]]} if shot else {}),
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
                    "SELECT id FROM jobs WHERE project=? AND chapter IS ? AND kind=? AND shot IS ? AND status IN ('QUEUED','RUNNING')" +
                    (' AND payload=?' if kind == 'voice-preview' else ''),
                    (pid, chapter, kind, sid) + ((json.dumps(payload),) if kind == 'voice-preview' else ()),
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
        self.sync_queue_states(pid)
        return self.snapshot()

    def provider(self, id):
        if id not in self.providers:
            raise ValueError("Unknown image provider: " + str(id))
        return self.providers[id]

    def require_resolved_execution(self,pid,kind):
        if kind in ('narration','intro-audio','voice-preview','render-chapter','render-full','intro-render'):
            return
        journal=getattr(self,'execution_journal',None)
        unresolved=journal.unresolved_project(pid) if journal else []
        if unresolved:
            from studio_execution import UnresolvedExecution
            raise UnresolvedExecution('Project has unresolved remote work. Reconcile saved execution IDs before dispatching again.')

    def select_director(self, project):
        self._timing_pause_epoch=getattr(getattr(getattr(self,'_overlap',None),'root',self),'pause_epoch',0)
        if getattr(self,'_overlap',None):
            self.validate_execution()
        config=getattr(self,'config',{})
        selected = project["settings"]["director"].get("provider", "local-qwen")
        if selected not in ("local-qwen", "openai-luna"):
            raise ValueError("Unknown director provider. Select local Qwen or Luna.")
        self.prepare_director_device(project)
        if selected == "openai-luna" and not isinstance(self.director, OpenAIDirector):
            self.director.stop()
            self.director = OpenAIDirector(config, self.store.root)
        elif selected == "local-qwen" and isinstance(self.director, OpenAIDirector):
            self.director.stop()
            self.director = LocalQwenDirector(config, self.store.root)
        self.director.focused_prompts = project["settings"].get("focusedPrompts") is True
        self.director.concise_prompt_supplement = project['settings']['director'].get('concisePrompts') is True
        self.director.reasoning = project["settings"]["director"].get("reasoning", "Balanced")
        if project['settings']['director'].get('executionMode','classic').startswith('staged'):
            # Separate config object; shared helper settings must not mutate.
            self.director.config = {**config,'openaiCompactWire':True}
            if project['settings']['director'].get('executionMode')=='staged-lean':
                # Spend reasoning on canonical facts; independent visual jobs
                # retain the user's chosen effort. Do not buy premium API tier.
                self.director.config['openaiTaskEffort']={role:('high' if self.director.reasoning=='High' else 'medium') for role in
                    ('Source fact analyst','Source fact correction','Targeted storyboard repair')}
            if project['settings']['director'].get('reasoningProfile','selected')=='adaptive':
                self.director.config['openaiTaskEffort']={**self.director.config.get('openaiTaskEffort',{}),
                    **{role:'none' for role in ('Visual storyboard director','Workflow planner')}}
        else:
            self.director.config = config
        if project['settings']['director'].get('processingTier'):
            self.director.config={**self.director.config,'openaiServiceTier':project['settings']['director']['processingTier']}
        if isinstance(self.director, OpenAIDirector):
            from cost_control import SpendLedger
            budget = project['settings'].get('budget', {})
            self.director.spend_ledger = (SpendLedger(self.store.folder(project['id']) / 'api-cost-ledger.json', budget['openaiUSD'],
                allow_multiple=bool(getattr(self,'_overlap',None)))
                if budget.get('openaiUSD') else None)
            self.director.max_output_tokens = project['settings']['director'].get('maxOutputTokens', 12000)
            # A remote request cannot be suspended by pausing its local reader.
            # Collect/save the current response, but keep ordinary dispatch gates
            # paused. Cancellation/shutdown still interrupt this receive gate.
            if getattr(self,'_overlap',None):
                self.director.receive_gate=lambda message:self._overlap.receive_gate(self.execution_context,message)
            else:
                self.director.receive_gate = lambda message: self.gate(message, wait_paused=False)

    def prepare_director_device(self, project):
        # Remote Luna uses no laptop VRAM. Moving the voice weights to CPU for
        # it wastes RAM and forces another transfer when narration resumes.
        if project['settings']['director'].get('provider', 'local-qwen') != 'openai-luna':
            self.before_image()

    def prepare_image_device(self, provider):
        if provider.id != 'comfyui':
            self.before_image()

    def control(self, action, job=None, project=None):
        sync_project = project if action in ('retry-missing','cancel-all') else None
        if action == 'retry-missing':
            if not project:
                raise ValueError('Choose a project before retrying missing images.')
            p = self.store.load(project)
            with self.cv:
                # Requeue just the newest failed attempt for each missing shot.
                # Historical failures and successful assets are never replayed.
                rows = self.db.execute(
                    "SELECT id,chapter,shot,status FROM jobs WHERE project=? AND kind='image' ORDER BY created DESC",
                    (project,),
                ).fetchall()
                latest = {}
                for row in rows:
                    latest.setdefault((row['chapter'], row['shot']), row)
                available = 1000 - self.db.execute(
                    "SELECT count(*) FROM jobs WHERE status IN ('QUEUED','RUNNING')"
                ).fetchone()[0]
                for c in p['chapters']:
                    for sc in c['scenes']:
                        for s in sc['shots']:
                            if s.get('imagePath') and self.store.has_asset(project, s['imagePath']):
                                continue
                            row = latest.get((c['id'], s['id']))
                            if not row or row['status'] not in ('FAILED','CANCELLED') or available <= 0:
                                continue
                            self.db.execute("UPDATE jobs SET status='QUEUED',message='Retrying missing image with saved seed' WHERE id=?", (row['id'],))
                            available -= 1
                self.db.commit()
            action = 'resume'
        with self.cv:
            if action == "pause":
                self.pause_epoch += 1
                self.paused = True
            elif action == "resume":
                self.paused = False
                self.yield_requested = False
            elif action == "cancel-current":
                context = getattr(self,'active_executions',{}).get(job) if job else None
                if context:
                    context.cancelled.set()
                elif job and job != self.current:
                    raise ValueError('Choose an active job or execution to cancel.')
                else:
                    self.cancel = True
            elif action == "cancel-all":
                if project:self.store.folder(project) # Validate the selected identity.
                current=self.db.execute('SELECT project FROM jobs WHERE id=?',(self.current,)).fetchone() if self.current else None
                if not project or current and current['project']==project:self.cancel=True
                if not project:self.paused = True # Global legacy control still holds the entire queue.
                self.db.execute(
                    "UPDATE jobs SET status='CANCELLED',message='Cancelled; completed assets retained' WHERE status='QUEUED'"+
                    (' AND project=?' if project else ''),(project,) if project else ()
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
        self.sync_queue_states(sync_project)
        return self.snapshot()

    def gate(self, message="", step=0, total=0, wait_paused=True):
        with self.cv:
            if self.closed or self.cancel:
                raise JobCancelled()
            if self.yield_requested:
                raise AudioYield()
            progress_key = (self.current, message)
            if message and self.current and getattr(self, '_progress_key', None) != progress_key:
                self.db.execute(
                    "UPDATE jobs SET message=? WHERE id=?", (message, self.current)
                )
                self.db.commit()
                self._progress_key = progress_key
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
                started = time.time()
                attempt_pause_epoch=self.pause_epoch
                job_performance={}
                self.db.execute(
                    "UPDATE jobs SET status='RUNNING',started=?,attempt=attempt+1 WHERE id=?",
                    (started, job["id"]),
                )
                self.db.commit()
            state = "COMPLETE"
            message = "Complete"
            trace_job = self.trace.begin(job['project'], 'Queue: ' + job['kind'],
                                         {'jobId': job['id']}, kind='job')
            trace_token=self.trace.parent.set(trace_job)
            from studio_power import ProductionPowerRequest
            power=ProductionPowerRequest(enabled=job['kind'] in ('produce-story','prepare-story','render-full','render-chapter'))
            try:
                power.acquire()
                self.legacy_queue.control("yield-audio")
                self.legacy_queue.control("pause")
                while not self.gpu_lock.acquire(timeout=0.25):
                    self.gate("Waiting for GPU helper")
                try:
                    p = self.store.load(job["project"])
                    burst=getattr(self,'_queued_image_rental',None)
                    if burst and (job['kind']!='image' or burst.project!=job['project']):
                        try:burst.stop('queue switched project or operation')
                        finally:self._queued_image_rental=None
                    direct_stage={'image':'Image generation','qc':'Visual QC','narration':'Narration',
                        'render-chapter':'Render chapter','render-full':'Assemble full video',
                        'character-reference':'Character reference','intro-image':'Intro images'}.get(job['kind'])
                    if direct_stage:
                        detail={'chapter':get_chapter(p,job['chapter'])['number'] if job['chapter'] else None,'shot':job['shot']}
                        job_performance={'performanceProfile':profile_for(direct_stage,p,self.config,detail),
                            'performanceUnits':observation_units(direct_stage,p,detail)}
                    self.require_resolved_execution(p['id'],job['kind'])
                    options = json.loads(job["payload"])
                    if job["kind"] == "produce-story":
                        self.produce_story(p["id"], options)
                    elif job['kind'] == 'restyle-story':
                        from economy_studio import restyle_story
                        restyle_story(self,p['id'])
                    elif job['kind'] == 'prepare-story':
                        self.prepare_story(p['id'], options)
                    elif job['kind'] == 'retarget-images':
                        self.retarget_images(p)
                    elif job['kind'] == 'economy-images':
                        from economy_studio import generate_panel_story
                        generate_panel_story(self,p['id'],options)
                    elif job["kind"] in (
                        "analyze",
                        "scene-plan",
                        "narration",
                        "intro-audio",
                        "voice-preview",
                        "outro-audio",
                    ):
                        self.unload_models()
                        self.before_audio()
                        if job["kind"] == "voice-preview":
                            self.voice_preview(p, options)
                        elif job['kind'] == 'outro-audio':
                            self.prepare_outro(p)
                        elif job["kind"] == "intro-audio":
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
                                self.providers["existing"].unload()
                                self.analyze_chapter(
                                    self.store.load(p["id"]), job["chapter"], options
                                )
                    elif job["kind"] == "image":
                        self.begin_queued_image_rental(p,job,options)
                        self.generate(
                            p, job["chapter"], job["shot"], job["seed"], options
                        )
                    elif job["kind"] == "qc":
                        self.inspect_shot(p, job["chapter"], job["shot"])
                    elif job["kind"] == "intro-image":
                        self.intro_image(p, options)
                    elif job['kind'] == 'qc-intro':
                        self.inspect_intro(p)
                    elif job["kind"] == "character-reference":
                        self.character_reference(p, options)
                    else:
                        self.unload_models()
                        self.providers["existing"].unload()
                        if job['kind'] == 'split-video':
                            if not p.get('render',{}).get('path'): raise ValueError('Render a full video before splitting it.')
                            from studio_engagement import settings as engagement_settings
                            splitting=engagement_settings(p)
                            splitting['splitMode']=options.get('mode',splitting['splitMode'])
                            splitting['chaptersPerPart']=options.get('chaptersPerPart',splitting['chaptersPerPart'])
                            if splitting['splitMode'] not in ('duration','chapters'):
                                raise ValueError('Choose duration or chapter boundaries for parts.')
                            minutes=options.get('minutes',p['settings'].get('engagement',{}).get('partMinutes',120))
                            if isinstance(minutes,bool) or not isinstance(minutes,(int,float)) or not math.isfinite(minutes) or not 1 <= minutes <= 1440:
                                raise ValueError('Part length must be 1–1440 minutes.')
                            original=p['render']['path']
                            splitting['partMinutes']=minutes
                            parts=self.renderer.export_parts(p,p['render'],self.gate,splitting)
                            def split_saved(q):
                                if q.get('render',{}).get('path')!=original: raise ValueError('Video changed during splitting; completed parts retained.')
                                q['render']['parts']=parts
                            self.store.mutate(p['id'],split_saved)
                        elif job["kind"] == "intro-render":
                            render_input = self.intro_submission_signature(p)
                            result = self.renderer.intro(p, self.gate)
                            if not self.publish_intro_render(p['id'],result.name,render_input):
                                raise ValueError('Intro changed during rendering. The completed earlier version is saved in intro render history. Render the current intro when ready.')
                        elif job["kind"] == "render-full":
                            render_input = self.render_submission_signature(p)
                            result = self.renderer.full(p, self.gate)

                            def full_saved(q):
                                if self.render_submission_signature(q) != render_input:
                                    self.retain_superseded_render(q,result)
                                    return False
                                if (
                                    q.get("render", {}).get("path")
                                    and q["render"]["path"] != result["path"]
                                ):
                                    q.setdefault("renderHistory", []).append(
                                        q["render"]
                                    )
                                q.update(render=result, renderStale=False)
                                q.setdefault('exports',{})[result.get('exportDestination','youtube')]=result
                                if q.get('production'):
                                    q['production'].update(status='COMPLETE', stage='Full video ready', message='Full video ready', updated=time.time())
                                return True

                            if not self.store.mutate(p["id"], full_saved):
                                raise ValueError('Story changed during rendering. The completed earlier version is saved in render history. Render the current version when ready.')
                        else:
                            ch = get_chapter(p, job["chapter"])
                            render_input = self.render_submission_signature(p,ch['id'])
                            result = self.renderer.chapter_export(p, ch, self.gate)

                            def chapter_saved(q):
                                c = get_chapter(q, ch["id"])
                                if self.render_submission_signature(q,ch['id']) != render_input:
                                    self.retain_superseded_render(c,result)
                                    q['renderStale']=True
                                    return False
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
                                return True

                            if not self.store.mutate(p["id"], chapter_saved):
                                raise ValueError('Chapter changed during rendering. The completed earlier version is saved in render history. Render the current version when ready.')
                finally:
                    self.gpu_lock.release()
            except AudioYield:
                state = "QUEUED"
                message = "Paused to free models for Audio"
                self.paused = True
            except CloudBudgetPaused as error:
                state = "QUEUED"
                message = str(error)
                self.paused = True
                self.status(job['project'], job['chapter'], 'PAUSED')
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
                try:self.finish_queued_image_rental(job,state)
                except Exception as error:
                    state='FAILED';message=type(error).__name__+': '+str(error)[:1800]
                finally:power.release()
                if job["kind"] in ("produce-story", "prepare-story"):

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
                    elapsed = time.time() - started
                    self.trace.finish(trace_job, max(0, elapsed), state)
                    self.trace.parent.reset(trace_token)
                    self.db.execute(
                        "UPDATE jobs SET status=?,message=?,seconds=? WHERE id=?",
                        (state, message, elapsed, job["id"]),
                    )
                    self.db.commit()
                    self.current = None
                    self.cancel = False
                    self.cv.notify_all()
                completion=job | {'started':started, 'seconds':elapsed, 'status':state,
                    'pausedDuringAttempt':self.pause_epoch!=attempt_pause_epoch,**job_performance}
                self.sync_queue_states(job['project'],completion)
                self.learn_performance(performance_signature({'job':job['id'],'started':started}),job['id'],
                    job_performance.get('performanceProfile'),job_performance.get('performanceUnits'),elapsed,
                    {'status':state,'paused':completion['pausedDuringAttempt']},started+elapsed)

    def production_progress(self, pid, stage, **details):
        """Persist the current stage alongside assets, including between restarts."""

        def update(p):
            run = p.setdefault("production", {})
            run.update(status="RUNNING", stage=stage, updated=time.time(), **details)

        self.store.mutate(pid, update)
        self.gate(stage)

    def record_timing(self, pid, stage, seconds, details=None, trace_record=True, profile_override=None):
        learned={};observed_at=time.time()
        context=getattr(self,'execution_context',None)
        if context:
            details=(details or {}) | {'executionId':context.identity,'inputHash':context.input_hash,'jobId':context.parent_job}
        root=getattr(getattr(self,'_overlap',None),'root',self)
        if root.paused or root.pause_epoch!=getattr(self,'_timing_pause_epoch',root.pause_epoch) or (details or {}).get('hostInterrupted'):
            details=(details or {}) | {'paused':True}
        run_id=(details or {}).get('jobId') or self.current or getattr(self,'_performance_run_id','manual')
        observation_id=performance_signature({'project':pid,'stage':stage,'finished':observed_at,
            'job':(details or {}).get('executionId',self.current)})
        if trace_record:
            self.trace.record(pid, stage, seconds, details)
        def save(p):
            production = p.setdefault("production", {})
            if (details or {}).get('provider') == 'openai-luna' and (details or {}).get('estimatedUSD'):
                costs = production.setdefault('costs',{})
                costs['apiEstimatedUSD'] = costs.get('apiEstimatedUSD',0) + details['estimatedUSD']
            timings = production.setdefault("timings", [])
            profile=profile_override or profile_for(stage,p,self.config,details)
            units=observation_units(stage,p,details or {})
            learned.update(profile=profile,units=units)
            overlap=getattr(self,'_overlap',None)
            if overlap and stage=='Image + quality checks':
                pipeline_profile=profile_for('Image pipeline',p,self.config,
                    (details or {}) | {'imageTasks':overlap.image_tasks})
                with overlap.root.cv:overlap.pipeline_observations.append({
                    'start':observed_at-seconds,'end':observed_at,
                    'profile':performance_signature(pipeline_profile),'shot':(details or {}).get('shot'),
                    'eligible':(details or {}).get('status')=='COMPLETE' and not any(
                        (details or {}).get(k) for k in ('paused','reused','usagePending'))})
            timings.append(
                {
                    "stage": stage,
                    "seconds": round(seconds, 3),
                    "finished": observed_at,
                    "performanceProfile": profile,
                    "performanceUnits": units,
                    "performanceRun": run_id,
                    "performanceId": observation_id,
                    **(details or {}),
                }
            )

        self.store.mutate(pid, save)
        self.learn_performance(observation_id,run_id,
            learned['profile'],learned['units'],seconds,details or {},observed_at)

    def learn_performance(self,*args,**kwargs):
        # Asset production must not fail because the optional calibration DB is
        # temporarily unavailable. Profiled timings remain in project history.
        root=getattr(getattr(self,'_overlap',None),'root',self)
        try:
            result=self.performance.record(*args,**kwargs)
            root.performance_warning=None
            return result
        except (OSError,sqlite3.Error):
            root.performance_warning='Timing history is saved with the project; calibration could not update. Retry its timing report after storage recovers.'
            return False

    def measured_stage(self, pid, stage, callback, *args, **details):
        overlap=getattr(self,'_overlap',None)
        if overlap and self is overlap.main:
            with overlap.stage(stage,args,details):
                return self.measured_execution_stage(pid,stage,callback,*args,**details)
        return self.measured_execution_stage(pid,stage,callback,*args,**details)

    def measured_execution_stage(self, pid, stage, callback, *args, **details):
        with self.trace.span(pid, stage, details):
            return self._measured_stage(pid, stage, callback, *args, **details)

    def _measured_stage(self, pid, stage, callback, *args, **details):
        host_epoch=host_execution_gaps()
        root=getattr(getattr(self,'_overlap',None),'root',self)
        pause_epoch=root.pause_epoch
        was_paused=root.paused
        old_audio=None
        if stage=='Visual QC':details['reviewTimingScope']='whole-review'
        if stage=='Narration' and len(args)>1 and isinstance(args[1],dict):
            old_audio=copy.deepcopy(args[1].get('audio',{}))
            old_audio_available=bool(old_audio.get('path') and self.store.has_asset(pid,old_audio['path']))
        if stage=='AI directing':
            p=next((a for a in args if isinstance(a,dict) and a.get('id')==pid),None)
            if p:details['directorTasks']=p['settings']['director'].get('parallelism',1) if (getattr(self,'_overlap',None) or p['settings']['director'].get('executionMode','classic').startswith('staged')) else 1
        began = time.monotonic()
        began_at=time.time()
        try:
            result = callback(*args)
        except Exception:
            self.record_timing(
                pid, stage, time.monotonic() - began, details | host_interruption_details(host_epoch) | {"status": "FAILED","paused":was_paused or root.pause_epoch!=pause_epoch}, trace_record=False
            )
            raise
        if old_audio is not None:
            current_audio=get_chapter(self.store.load(pid),args[1]['id']).get('audio',{})
            details['reused']=bool(old_audio_available and old_audio==current_audio)
        if stage=='AI directing':
            # A retry can use many paid replies from the durable cache. Its
            # shorter wall time must not train a fresh full-chapter forecast.
            recent=self.store.load(pid).get('production',{}).get('timings',[])
            details['reused']=any(t.get('reused') and t.get('detail') and
                t.get('stage')!='Workflow planner' and t.get('chapter')==details.get('chapter') and t.get('finished',0)>=began_at for t in recent)
        status=result.get('status') if isinstance(result,dict) else None
        self.record_timing(
            pid, stage, time.monotonic() - began, details | host_interruption_details(host_epoch) | {"status":status if status in ('SUPERSEDED','CANCELLED','FAILED') else "COMPLETE","paused":was_paused or root.pause_epoch!=pause_epoch}, trace_record=False
        )
        return result

    def trace_report(self, pid):
        project = self.store.load(pid)
        rental=project.get('production',{}).get('gpuRental',{})
        window=({'cloudStartedAt':rental.get('started'),'cloudStoppedAt':rental.get('stopped')}
                if rental else project['settings'].get('cloudWindow'))
        return self.trace.report(pid,window)

    def performance_forecast(self,project,accepted=None):
        """Matching stage estimates; unknown work is never quietly zero."""
        accepted=accepted or set();planning=0;images=0;unknown=[];observations=[];cache={}
        def measure(stage,units,details):
            profile=profile_for(stage,project,self.config,details)
            key=(performance_signature(profile),units['kind'])
            if key not in cache:
                cache[key]=self.performance.estimate(profile,{'kind':units['kind'],'value':1})
            base=cache[key];row=dict(base)
            if base['seconds'] is not None:
                row['seconds']=base['seconds']*units['value']
                row['rangeSeconds']=[v*units['value'] for v in base['rangeSeconds']]
                if base['costUSD'] is not None:row['costUSD']=base['costUSD']*units['value']
            row['units']=units
            observations.append({'stage':stage,**row})
            if row['seconds'] is None:unknown.append(stage)
            return row['seconds']
        for chapter in project['chapters']:
            if not chapter['sourceText'].strip():continue
            if chapter['id'] not in accepted:
                units={'kind':'characters','value':len(self.narration_text(project,chapter))}
                audio=chapter.get('audio',{})
                if not self.saved_narration_ready(project,chapter):
                    seconds=measure('Narration',units,{'chapter':chapter['number']})
                    if seconds is not None:planning+=seconds
                if not self.analysis_is_current(project,chapter,audio.get('signature')):
                    seconds=measure('AI directing',units,{'chapter':chapter['number']})
                    if seconds is not None:planning+=seconds
            else:
                active={person['id'] for scene in chapter['scenes'] for shot in scene['shots']
                    for person in shot['characters'] if person['type']=='main'}
                if any(person['id'] in active and not person.get('references') for person in project['characters']):
                    unknown.append('Character reference')
                for scene in chapter['scenes']:
                    for shot in scene['shots']:
                        if self.saved_image_ready(project,shot):continue
                        review_only=bool(review_settings_stale(project,shot) and shot.get('imagePath')
                            and not shot.get('generationStale') and self.store.has_asset(project['id'],shot['imagePath']))
                        seconds=measure('Visual QC' if review_only else 'Image pipeline',{'kind':'images','value':1},
                            {'chapter':chapter['number'],'shot':shot['id'],
                                '_shotSpec':shot,
                                'imageTasks':getattr(getattr(self,'_overlap',None),'image_tasks',3)})
                        if seconds is not None:images+=seconds
        unique={performance_signature(row.get('profile') or {'stage':row['stage'],'status':row['status']}):row for row in observations}
        return {'planningSeconds':None if any(x in ('Narration','AI directing') for x in unknown) else planning,
            'readyImageSeconds':None if any(x in ('Image pipeline','Visual QC','Character reference') for x in unknown) else images,
            'unknownStages':sorted(set(unknown)),'profiles':list(unique.values()),
            'scope':'Remaining narration/directing and accepted ready images only; rendering, intro, setup, transfers outside the measured parent, repairs and manual rental stop are not a complete ETA.'}

    def performance_report(self,pid):
        p=self.store.load(pid)
        # Import only records that captured their actual settings at execution.
        # Never apply today's settings to legacy unprofiled observations.
        rows=[row for row in p.get('production',{}).get('timings',[])
            if row.get('performanceId') and row.get('performanceProfile') and row.get('performanceUnits') and row.get('seconds',0)>0]
        known=self.performance.known([row['performanceId'] for row in rows])
        for row in rows:
            if row['performanceId'] not in known:
                self.learn_performance(row['performanceId'],row.get('performanceRun','legacy'),
                    row.get('performanceProfile'),row.get('performanceUnits'),row['seconds'],row,row.get('finished'),source='project-history')
        if all(row['performanceId'] in known for row in rows):self.performance_warning=None
        accepted={c['id'] for c in p['chapters'] if c['scenes'] and self.saved_narration_ready(p,c)
                  and self.analysis_is_current(p,c,c.get('audio',{}).get('signature'))}
        return {'history':self.performance.summary(),'forecast':self.performance_forecast(p,accepted),
            'warning':getattr(self,'performance_warning',None),
            'scheduling':p.get('production',{}).get('scheduling'),
            'note':'Every supported stage/attempt is retained. Failed, reused, paused, incompatible and unresolved samples are excluded from speed calibration. Unknown work stays unknown.'}

    def prepare_story(self, pid, options):
        """Generate fresh narration and the selected director's complete plan.

        Paid image workers can stay stopped. Existing manual plans use the
        normal proposal/history protection; no restyling shortcut is used.
        """
        p = self.store.load(pid)
        for chid in [c['id'] for c in p['chapters'] if c['sourceText'].strip()]:
            p = self.store.load(pid)
            chapter = get_chapter(p, chid)
            previous_audio = chapter.get('audio', {}).get('signature')
            self.production_progress(pid, f"Chapter {chapter['number']} · preparing narration", chapterId=chid)
            self.before_audio()
            self.measured_stage(pid, 'Narration', self.narration, p, chapter, chapter=chapter['number'])
            p = self.store.load(pid)
            chapter = get_chapter(p, chid)
            if not options.get('forceAnalysis') and self.analysis_is_current(p, chapter, previous_audio):
                if options.get('acceptMainCharacters'):
                    self.confirm_chapter_people(pid, chid)
                continue
            self.production_progress(pid, f"Chapter {chapter['number']} · complete AI direction", chapterId=chid)
            self.measured_stage(pid, 'AI directing', self.analyze_chapter, p, chid,
                options | {'managedPipeline': True, 'offlinePlanning': True}, chapter=chapter['number'])
            latest = self.store.load(pid)
            if get_chapter(latest, chid).get('proposedPlan'):
                raise ValueError(f"{chapter['name']}: review the proposed analysis before continuing. Existing manual edits were preserved.")
            if options.get('acceptMainCharacters'):
                self.confirm_chapter_people(pid, chid)
        p = self.store.load(pid)
        if p['intro']['enabled'] and p['intro'].get('voiceText'):
            self.before_audio()
            self.measured_stage(pid, 'Intro narration', self.intro_audio, p, True)
            if p['intro'].get('sourceBrief') and not p['intro'].get('shots'):
                self.measured_stage(pid, 'Intro direction', self.plan_intro, self.store.load(pid))
        self.store.mutate(pid, lambda q: q.setdefault('production', {}).update(
            status='READY_FOR_IMAGES', stage='Fresh narration and direction prepared; connect the image worker'))

    @staticmethod
    def analysis_is_current(project, chapter, previous_audio):
        from studio_automation import source_conflicts
        if chapter.get('proposedPlan') or source_conflicts(project, chapter): return False
        participating = {cast['id'] for scene in chapter['scenes'] for shot in scene['shots'] for cast in shot['characters']}
        def relevant(state):
            return {'characters': {k: v for k, v in state.get('characters', {}).items() if k in participating},
                    'environment': state.get('environment', {}), 'objects': state.get('objects', {})}
        state, _ = state_before(project, chapter['id'])
        return bool(chapter['scenes'] and chapter.get('handoff', {}).get('sourceSignature') == digest(chapter['sourceText'])
                    and previous_audio == chapter.get('audio', {}).get('signature')
                    and relevant(chapter.get('inputState', {})) == relevant(state)
                    and not chapter.get('continuityNeedsReview', False))

    def preflight_chapter(self, pid, chid):
        def check(project):
            for scene in get_chapter(project, chid)['scenes']:
                for shot in scene['shots']:
                    shot['preflight'] = require_shot(project, shot)
        self.store.mutate(pid, check, skip_unchanged=True)

    def confirm_chapter_people(self, pid, chid):
        # Only the explicit full-production action opts into main acceptance.
        # This happens before directing the next chapter so it sees the bible.
        def confirm(project):
            chapter = get_chapter(project, chid)
            for person in chapter['people']:
                if person['type'] == 'main' and not person.get('removed'):
                    person['accepted'] = True
                    if not any(c['id'] == person['id'] for c in project['characters']):
                        project['characters'].append(copy.deepcopy(person))
            chapter['inputState'] = state_before(project, chid)[0]
        self.store.mutate(pid, confirm, skip_unchanged=True)

    def retarget_images(self, p):
        """Luna adapts an existing reviewed plan to a newly selected provider.

        Preserve story, timing and manually authored prompts; save each completed
        group immediately. The runtime resolves reference count at generation.
        """
        self.select_director(p)
        provider = self.provider(p['settings']['image']['provider'])
        selected = p['settings']['image']
        catalog = provider.planningCatalog() if provider.id == 'comfyui' else provider.healthCheck()
        if selected['model'] not in catalog['models'] or selected['workflow'] not in catalog['workflow']:
            raise ValueError('Selected image model/workflow is not configured.')
        caps = provider.getModelCapabilities(selected['model']) if hasattr(provider, 'getModelCapabilities') else provider.getCapabilities()
        for chapter in p['chapters']:
            self.director.timing_callback = lambda stage, seconds, details, number=chapter['number']: self.record_timing(
                p['id'], stage, seconds, details | {'chapter': number, 'detail': True})
            shots = [s for sc in chapter['scenes'] for s in sc['shots'] if not s.get('manual', {}).get('prompt')]
            for offset in range(0, len(shots), 12):
                group = copy.deepcopy(shots[offset:offset+12])
                for shot in group:
                    shot.update(imageProvider=provider.id, imageModel=selected['model'], workflow=selected['workflow'],
                        generationSettings=shot['generationSettings'] | selected | {'loras': []})
                    shot['prompt'], shot['negativePrompt'] = format_prompt(p, shot, provider)
                result = self.director.writeImagePrompt({'model': selected['model'], 'promptFormat': caps['promptFormat'],
                    'capabilities': caps, 'shots': [{'shotIndex': i, 'narration': s['narrationSegment'],
                        'draftPrompt': s['prompt']} for i,s in enumerate(group)],
                    'instruction': 'Adapt each supplied grounded draft into a concise natural-language prompt for the selected model. '
                        'Keep only its own story action, selected people and current clothing. Render as drawn 2D fantasy manhwa, never photography. '
                        'References preserve identity, not old outfits. No invented events, unrelated rooms, captions, borders or title text.'}, self.gate)
                indexes = [x['shotIndex'] for x in result['prompts']]
                if sorted(indexes) != list(range(len(group))):
                    raise ValueError('Director omitted or duplicated model-specific prompts; prior prompts retained.')
                for item in result['prompts']:
                    shot = group[item['shotIndex']]
                    shot['directorPrompt'] = item['prompt']
                    shot['prompt'] += ' Visual direction: ' + item['prompt']
                def save(latest):
                    for shot in group:
                        current = get_shot(latest, chapter['id'], shot['id'])
                        current.update({k:shot[k] for k in ('imageProvider','imageModel','workflow','generationSettings','prompt','negativePrompt','directorPrompt')})
                        current['generationStale'] = bool(current.get('imagePath'))
                    get_chapter(latest, chapter['id'])['renderStale'] = True
                    latest['renderStale'] = True
                self.store.mutate(p['id'], save)

    def begin_queued_image_rental(self,project,job,options):
        """Reuse an owned rental only through a contiguous same-project burst."""
        from studio_runpod import configured,ProductionGPU
        shot=options.get('shotSnapshot') or get_shot(project,job['chapter'],job['shot'])
        if not configured(self.config) or shot['imageProvider']!='comfyui':return
        # Do not rent merely to discover that an unbudgeted repair is blocked.
        if options.get('budgetedQCRepair') or (shot.get('imagePath') and not shot.get('generationStale')
                and shot.get('qc',{}).get('pass') is False):
            reason=repair_budget_reason(project['settings'])
            if reason:raise ValueError(reason)
        # Validate the submitted shot before buying cold startup time.
        require_shot(project,shot,new_image=True)
        rental=getattr(self,'_queued_image_rental',None)
        if rental is None:
            rental=ProductionGPU(self.config,self.store.root/'.managed-rental.json',project['id'],
                project['settings'].get('cloudWindow',{}),self.providers['comfyui'],self.gate,
                lambda state:self.record_gpu_state(project['id'],state),
                image_selection={'model':shot['imageModel'],'workflow':shot['workflow']})
            self._queued_image_rental=rental
        rental.ensure()

    def finish_queued_image_rental(self,job,status):
        rental=getattr(self,'_queued_image_rental',None)
        if rental is None:return
        with self.cv:
            next_job=self.db.execute("SELECT project,kind FROM jobs WHERE status='QUEUED' ORDER BY priority DESC,created LIMIT 1").fetchone()
            keep=(status=='COMPLETE' and not self.paused and not self.cancel and not self.closed
                and next_job and next_job['project']==job['project'] and next_job['kind']=='image')
        if keep:return
        try:rental.stop('queued image burst finished or interrupted')
        finally:self._queued_image_rental=None

    def produce_story(self,pid,options):
        if options.get('unattended') is True:
            from studio_automation import run_settings, project_cap_reason
            reason=project_cap_reason(run_settings(self.store.load(pid)['settings']))
            if reason:raise ValueError(reason)
            self.store.mutate(pid,lambda p:p.update(settings=run_settings(p['settings'])),skip_unchanged=True)
        from studio_runpod import configured,ProductionGPU
        if getattr(self,'_overlap',None) or not configured(self.config):
            return self._produce_story(pid,options)
        p=self.store.load(pid)
        if p['settings']['image']['provider']!='comfyui':
            raise ValueError('Managed rental requires the configured cloud ComfyUI workflow.')
        rental=ProductionGPU(self.config,self.store.root/'.managed-rental.json',pid,
            p['settings'].get('cloudWindow',{}),self.providers['comfyui'],self.gate,
            lambda state:self.record_gpu_state(pid,state),image_selection={
                'model':p['settings']['image']['model'],'workflow':p['settings']['image']['workflow']})
        self._production_gpu=rental
        try:return self._produce_story(pid,options)
        finally:
            try:rental.stop('production ended')
            finally:self._production_gpu=None

    def ensure_production_gpu(self):
        rental=getattr(self,'_production_gpu',None)
        if rental:rental.ensure()

    def record_gpu_state(self,pid,state):
        def save(p):
            run=p.setdefault('production',{});run['gpuRental']=state
            if state.get('status')=='STOPPED':
                history=run.setdefault('gpuRentalHistory',[])
                if not any(h.get('started')==state.get('started') and h.get('podId')==state.get('podId') for h in history):
                    history.append({k:v for k,v in state.items() if k!='spentByProject'})
                run.setdefault('costs',{}).update(gpuWindowEstimatedUSD=state.get('spentByProject',{}).get(pid),
                                                  gpuCostSource='managed rental windows; estimated, not invoice')
        self.store.mutate(pid,save)

    def _produce_story(self, pid, options):
        """One durable queue job; reuse completed stages and stop safely on errors."""
        p = self.store.load(pid)
        from studio_schedule import strategy,LABELS
        mode=strategy(options)
        self.store.mutate(pid,lambda q:q.setdefault('production',{}).update(
            generationStrategy=mode,generationStrategyLabel=LABELS[mode],
            rentalControl='managed' if getattr(self,'_production_gpu',None) else 'external'))
        if not getattr(self,'_overlap',None):self._performance_run_id=uid('run-')
        if options.get('overlap') and not getattr(self,'_overlap',None):
            from studio_overlap import CloudStoryOverlap
            overlap=CloudStoryOverlap(self,p,options)
            state,message='COMPLETE','Complete'
            try:
                return overlap.main.produce_story(pid,options)
            except BaseException as error:
                state='CANCELLED' if isinstance(error,JobCancelled) else 'FAILED'
                message=str(error)[:1800]
                # Paid Luna replies can finish collecting receipts without an
                # idle GPU rental while overlap.close waits for their threads.
                rental=getattr(self,'_production_gpu',None)
                if rental:rental.stop('production interrupted; collecting API receipts')
                raise
            finally:
                overlap.close(state,message)
        if p['settings'].get('cloudImagesOnly') and p['settings']['image']['provider'] != 'comfyui':
            raise ValueError('This project requires cloud images. Connect its ComfyUI worker; local image generation is disabled.')
        cloud_flow = (p['settings']['image']['provider'] == 'comfyui'
                      and not p['settings'].get('economyPanels'))
        if cloud_flow and not getattr(self,'_overlap',None):
            # Offline planning uses declared workflow capabilities. The live
            # provider/VRAM/model checks still run before paid image generation.
            self.prepare_story(pid, options | {'acceptMainCharacters': True})
            p = self.store.load(pid)
            for chapter in p['chapters']:
                if chapter['sourceText'].strip():
                    self.measured_stage(pid, 'Image preflight', self.preflight_chapter, pid, chapter['id'], chapter=chapter['number'])
        if p['settings'].get('economyPanels'):
            from economy_studio import restyle_story, generate_panel_story
            if not all(c.get('economyGroups') for c in p['chapters'] if c['sourceText'].strip()):
                restyle_story(self,pid)
                p = self.store.load(pid)
            if any(not s.get('imagePath') or s.get('generationStale') for c in p['chapters'] for sc in c['scenes'] for s in sc['shots']):
                generate_panel_story(self,pid,options | p['settings'].get('cloudWindow',{}))
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
            if getattr(self,'_overlap',None):
                self._overlap.before_chapter(chid)
                self._overlap.validate_settings()
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
            needs_analysis = not self.analysis_is_current(p, ch, previous_audio)
            if needs_analysis:
                self.production_progress(
                    pid, f"Chapter {index+1} / {len(chapters)} · AI director"
                )
                self.providers["existing"].unload()
                self.measured_stage(
                    pid,
                    "AI directing",
                    self.analyze_chapter,
                    p,
                    chid,
                    {"managedPipeline": True,"offlinePlanning":cloud_flow,"unattended":options.get('unattended') is True},
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
            self.confirm_chapter_people(pid, chid)
            p = self.store.load(pid)
            ch = get_chapter(p, chid)
            if p["settings"]["appearanceHandling"] != "Automatic" and any(
                s.get("appearanceChanges") and not s.get("appearanceChangesReviewed")
                for s in ch["scenes"]
            ):
                raise ValueError(
                    f"{ch['name']}: your appearance-change settings require review. Accept the changes and retry this run."
                )
            if getattr(getattr(self,'_overlap',None),'scheduler',None):
                self._overlap.publish_chapter(p,chid,index,chapters)
            else:
                self.generate_production_chapter_images(pid,chid,index,chapters)
            if not cloud_flow:
                self.render_production_chapter(pid, chid, index, chapters)

        if getattr(self,'_overlap',None):
            self._overlap.drain()
        p = self.store.load(pid)
        if (
            p["intro"]["enabled"]
            and p["intro"].get("voiceText", "").strip()
            and not self.saved_intro_audio_ready(p)
        ):
            self.production_progress(
                pid, "Preparing separate intro audio", chapterId=None, shotId=None
            )
            self.before_audio()
            self.intro_audio(p)
        p = self.store.load(pid)
        if (
            p["intro"]["enabled"]
            and (p['intro'].get('shots') or p['intro'].get('visualPrompt', '').strip())
            and (any(not s.get('imagePath') or not self.store.has_asset(pid,s['imagePath'])
                     for s in p['intro'].get('shots', []))
                 or not p['intro'].get('shots') and (not p['intro'].get('visualPath')
                     or not self.store.has_asset(pid,p['intro']['visualPath'])))
        ):
            self.production_progress(
                pid, "Generating intro visual", chapterId=None, shotId=None
            )
            self.intro_image(p, {'_collectReview':self.collect_production_reviews(p)})
        # Complete chapter AND intro images before local chapter rendering.
        if p['settings'].get('unattendedProduction'):
            self.complete_automatic_reviews(pid)
        rental=getattr(self,'_production_gpu',None)
        if rental:rental.stop('chapter and intro images finished')
        self.require_production_reviews(pid)
        if cloud_flow:
            for index, chid in enumerate(chapters):
                self.render_production_chapter(pid, chid, index, chapters)
        self.production_progress(
            pid,
            "Rendering full story",
            chapterId=None,
            shotId=None,
            completedChapters=len(chapters),
        )
        self.unload_models()
        self.providers["existing"].unload()
        render_project = self.store.load(pid)
        render_input = self.render_submission_signature(render_project)
        result = self.measured_stage(
            pid,
            "Assemble full video",
            self.renderer.full,
            render_project,
            self.gate,
        )

        def saved(latest):
            if self.render_submission_signature(latest) != render_input:
                self.retain_superseded_render(latest,result)
                return False
            if (
                latest.get("render", {}).get("path")
                and latest["render"]["path"] != result["path"]
            ):
                latest.setdefault("renderHistory", []).append(latest["render"])
            latest.update(render=result, renderStale=False)
            latest.setdefault('exports',{})[result.get('exportDestination','youtube')]=result
            latest["production"].update(
                status="ARCHIVING" if latest['settings'].get('unattendedProduction') else "COMPLETE",
                stage="Saving complete video to cloud" if latest['settings'].get('unattendedProduction') else "Full video ready",
                videoPath=result["path"],
                completedChapters=len(chapters),
            )
            return True

        if not self.store.mutate(pid, saved):
            raise ValueError('Story changed during assembly. The completed earlier version is saved in render history. Render the current version when ready.')
        if self.store.load(pid)['settings'].get('unattendedProduction'):
            self.wait_for_cloud_save(pid)
            self.store.mutate(pid,lambda p:p['production'].update(status='COMPLETE',stage='Full video ready in cloud storage'))
            self.wait_for_cloud_save(pid)

    def wait_for_cloud_save(self, pid):
        """Wait for the existing archive owner; never race a second uploader."""
        if not self.storage.enabled:
            raise RuntimeError('Cloud archive is unavailable; completed local video is preserved.')
        revision=self.store.load(pid)['revision'];self.storage.enqueue(pid)
        deadline=time.monotonic()+max(30,min(7200,float(self.config.get('cloudSaveTimeoutSeconds',3600))))
        retries=0
        while True:
            self.gate()
            state=self.storage.status(pid)
            saved=state.get('projects',{}).get(pid,{})
            if state.get('error'):
                raise RuntimeError('Cloud save failed; the completed video remains saved locally.')
            if saved.get('error'):
                if retries>=2:raise RuntimeError('Cloud save retries exhausted; the completed video remains saved locally.')
                retries+=1;self.storage.enqueue(pid)
                continue
            if saved.get('revision',-1)>=revision and saved.get('status')=='SYNCED':return
            if time.monotonic()>=deadline:
                raise RuntimeError('Cloud save deadline reached; the completed video is preserved and archive retry remains queued.')
            time.sleep(.25)

    def complete_automatic_reviews(self,pid):
        """Repair confirmed findings once under the durable per-shot retry ceiling."""
        p=self.store.load(pid)
        for chapter in p['chapters']:
            for scene in chapter['scenes']:
                for shot in scene['shots']:
                    current=self.store.load(pid);item=get_shot(current,chapter['id'],shot['id'])
                    if not item.get('imagePath') or not qc_decision(current,item,self.store)['blocking']:continue
                    if item.get('manual',{}).get('imagePath'):
                        raise RuntimeError('Automatic review found a major error in a manually locked image; original selection and findings retained.')
                    qc=item.get('qc',{})
                    if not automatic_repairs(current['settings'],qc):
                        raise RuntimeError('Automatic quality checks could not resolve this shot within the selected policy; saved assets and findings retained.')
                    signature=self.visual_spec_signature(current,item)
                    used=item.get('automaticRepairAttempts',0) if item.get('automaticRepairSpec')==signature else 0
                    if used>=current['settings']['maxImageRetries']:
                        raise RuntimeError('Automatic image repair limit reached; saved assets and findings retained.')
                    self.production_progress(pid,'Repairing confirmed visual errors automatically',shotId=item['id'])
                    self.ensure_production_gpu()
                    provider=self.provider(item['imageProvider'])
                    from studio_automation import repair_operation
                    operation=repair_operation(current,item,provider)
                    self.measured_stage(pid,'Automatic image repair',self.generate,current,chapter['id'],item['id'],
                        item['generationSettings']['seed'],{'operation':operation,'budgetedQCRepair':True,
                            '_repairPrompt':qc.get('repairPrompt') or item['prompt'],'_collectReview':True},
                        chapter=chapter['number'],shot=item['id'])
                    latest=self.store.load(pid)
                    if qc_decision(latest,get_shot(latest,chapter['id'],item['id']),self.store)['blocking']:
                        raise RuntimeError('Automatic image repairs exhausted without a usable result; saved assets and findings retained.')
        if self.store.load(pid)['intro']['enabled']:
            self.inspect_intro(self.store.load(pid),sampled_only=True)
            for original in self.store.load(pid)['intro'].get('shots',[]):
                for _ in range(self.store.load(pid)['settings']['maxImageRetries']):
                    current=self.store.load(pid);item=next(s for s in current['intro']['shots'] if s['id']==original['id'])
                    if not qc_decision(current,item,self.store)['blocking']:break
                    if not automatic_repairs(current['settings'],item.get('qc',{})):
                        raise RuntimeError('Automatic intro quality checks could not resolve the image; original assets retained.')
                    self.intro_image(current,{'repairShot':item['id'],'_collectReview':True})
                latest=self.store.load(pid);item=next(s for s in latest['intro']['shots'] if s['id']==original['id'])
                if qc_decision(latest,item,self.store)['blocking']:
                    raise RuntimeError('Automatic intro repair limit reached; original assets and findings retained.')

    def reserve_automatic_repair(self,pid,chid,sid,signature,intro=False):
        """Consume a durable retry before dispatch, including uncertain attempts."""
        def reserve(project):
            item=next(s for s in project['intro']['shots'] if s['id']==sid) if intro else get_shot(project,chid,sid)
            used=item.get('automaticRepairAttempts',0) if item.get('automaticRepairSpec')==signature else 0
            if used>=project['settings']['maxImageRetries']:
                raise ValueError('Automatic repair limit reached; saved work retained.')
            item.update(automaticRepairSpec=signature,automaticRepairAttempts=used+1)
        self.store.mutate(pid,reserve)

    @staticmethod
    def retain_superseded_render(target, result):
        target.setdefault('renderHistory',[]).append(result | {'selected':False,
            'reason':'Source or selected assets changed during rendering; current project was preserved.'})
        target['renderStale']=True


    def collect_production_reviews(self, project):
        from studio_qc import policy
        return bool((getattr(self,'_overlap',None) or project['settings'].get('unattendedProduction')) and policy(project['settings'])=='practical')

    def reviewable_saved_image(self, project, shot):
        return bool(review_settings_stale(project,shot) and shot.get('imagePath')
                    and not shot.get('generationStale') and self.store.has_asset(project['id'],shot['imagePath']))

    def retained_production_image(self, project, shot):
        return bool(self.collect_production_reviews(project) and shot.get('imagePath')
            and not shot.get('generationStale')
            and self.store.has_asset(project['id'],shot['imagePath']))

    def require_production_reviews(self, pid):
        project=self.store.load(pid)
        shots=[shot for chapter in project['chapters'] for scene in chapter['scenes'] for shot in scene['shots']]
        if project['intro']['enabled']:shots+=project['intro'].get('shots',[])
        flags=[shot['id'] for shot in shots if shot.get('imagePath') and qc_decision(project,shot,self.store)['blocking']]
        if flags:
            self.production_progress(pid,'Images saved · review required',reviewRequiredShots=flags)
            if project['settings'].get('unattendedProduction'):
                raise RuntimeError(f'Automatic quality checks failed for {len(flags)} images; completed assets and findings retained.')
            raise RuntimeError(f'Visual QC: {len(flags)} saved images need review before rendering. '
                'The GPU is stopped; no replacement was purchased. Review or repair the flagged shots, then continue.')

    def generate_production_chapter_images(self,pid,chid,index,chapters):
        p=self.store.load(pid)
        ch=get_chapter(p,chid)
        if any(not (self.saved_image_ready(p,s) or self.retained_production_image(p,s) or self.reviewable_saved_image(p,s)) for sc in ch['scenes'] for s in sc['shots']):
            self.ensure_production_gpu()
        active_characters = {
            c["id"]
            for sc in ch["scenes"]
            for shot in sc["shots"]
            for c in shot["characters"]
            if c["type"] == "main" and not self.saved_image_ready(p,shot)
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
                    if self.saved_image_ready(latest, shot) or self.retained_production_image(latest,shot):
                        continue
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
                        settings["width"] = min(settings.get("width", 384), 384)
                        settings["height"] = min(settings.get("height", 384), 384)
                    requested=settings | {"model":shot['imageModel']}
                    validated = (provider.validateSettings(requested,check_hardware=False)
                        if provider.id=='comfyui' and getattr(self,'_overlap',None)
                        else provider.validateSettings(requested))
                    shot["generationSettings"] = validated

        self.store.mutate(pid, prepare_images)
        p = self.store.load(pid)
        ch = get_chapter(p, chid)
        shots = [s for scene in ch["scenes"] for s in scene["shots"]]
        if getattr(self,'_overlap',None):
            self.preflight_chapter(pid,chid)
            self._overlap.chapter_dependencies.append(digest(ch.get('handoff',{})))
        for shot_index, shot in enumerate(shots):
            # Cached shots need no per-shot progress writes to the entire
            # durable project. One chapter update below records completion.
            if (self.saved_image_ready(p, shot) or self.retained_production_image(p,shot)
                    or self.reviewable_saved_image(p,shot)):
                if ((getattr(self,'_overlap',None) or p['settings'].get('unattendedProduction') or review_settings_stale(p,shot))
                        and should_check(p,shot) and qc_decision(p,shot,self.store)['disposition']!='accepted'):
                    qc=shot.get('qc',{})
                    asset_hash=hashlib.sha256(self.store.asset(pid,shot['imagePath']).read_bytes()).hexdigest()
                    if (qc.get('checkedImagePath')!=shot['imagePath'] or qc.get('checkedImageSHA256')!=asset_hash
                            or qc.get('shotSpecSignature')!=self.visual_spec_signature(p,shot)
                            or review_settings_stale(p,shot) or qc.get('status') in (None,'PENDING','UNREVIEWED')):
                        if getattr(self,'_overlap',None):
                            self._overlap.submit_shot(self.store.load(pid),chid,shot,review_only=True)
                        else:
                            review=self.measured_stage(pid,'Visual QC',self.inspect_shot,self.store.load(pid),chid,shot['id'],
                                                       chapter=ch['number'],shot=shot['id'])
                            if not review['selected']:
                                raise RuntimeError('Shot changed during automatic review; current edits and results retained.')
                continue
            if (shot.get('imagePath') and not shot.get('generationStale')
                    and shot.get('qc',{}).get('pass') is False
                    and not automatic_repairs(p['settings'],shot.get('qc',{}))):
                raise RuntimeError('Saved image needs your review. No replacement was purchased; accept this image to continue.')
            self.production_progress(
                pid,
                f"Chapter {index+1} / {len(chapters)} · image {shot_index+1} / {len(shots)}",
                shotId=shot["id"],
                imageNumber=shot_index + 1,
                totalImages=len(shots),
                completedImages=shot_index,
            )
            complete = (
                self.saved_image_ready(p, shot)
            )
            if not complete:
                if getattr(self,'_overlap',None):
                    self._overlap.submit_shot(self.store.load(pid),chid,shot)
                    continue
                try:
                    self.measured_stage(
                        pid,
                        "Image + quality checks",
                        self.generate,
                        self.store.load(pid),
                        chid,
                        shot["id"],
                        shot["generationSettings"]["seed"],
                        {'_collectReview':self.collect_production_reviews(p),'deferAutomaticRepair':p['settings'].get('unattendedProduction',False)},
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

    def render_production_chapter(self, pid, chid, index, chapters):
        p = self.store.load(pid)
        ch = get_chapter(p, chid)
        shots = [s for scene in ch['scenes'] for s in scene['shots']]
        self.production_progress(
            pid,
            f"Chapter {index+1} / {len(chapters)} · render chapter",
            shotId=None,
            completedImages=len(shots),
            totalImages=len(shots),
        )
        self.unload_models()
        self.providers["existing"].unload()
        p = self.store.load(pid)
        ch = get_chapter(p, chid)
        render_input = self.render_submission_signature(p,chid)
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
            if self.render_submission_signature(latest,chid) != render_input:
                self.retain_superseded_render(chapter,result)
                latest['renderStale']=True
                return False
            if (
                chapter.get("render", {}).get("path")
                and chapter["render"]["path"] != result["path"]
            ):
                chapter.setdefault("renderHistory", []).append(chapter["render"])
            chapter.update(render=result, renderStale=False, status="COMPLETE")
            return True

        if not self.store.mutate(pid, chapter_saved):
            raise ValueError('Chapter changed during rendering. The completed earlier version is saved in render history. Review the current chapter before continuing.')


    def intro_submission_signature(self, project):
        intro=project['intro']
        fields={k:intro.get(k) for k in ('enabled','duration','placement','title','subtitle',
                 'showTitle','voiceText','visualPath','audioPath','motion','endOnNarration')}
        def asset(name):
            if not name:return None
            path=self.store.asset(project['id'],name)
            return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else {'missing':name}
        fields.update(visualBytes=asset(intro.get('visualPath')),audioBytes=asset(intro.get('audioPath')),
            shots=[{'image':asset(s.get('imagePath')),**{k:s.get(k) for k in ('start','end','motion')}}
                   for s in intro.get('shots',[])])
        from studio_branding import identity as watermark_identity
        return digest({'intro':fields,'video':project['settings']['video'],'watermark':watermark_identity(project,self.store)})

    def publish_intro_render(self, pid, path, expected_signature):
        def saved(latest):
            intro=latest['intro']
            if self.intro_submission_signature(latest) != expected_signature:
                self.retain_superseded_render(intro,{'path':path})
                return False
            if intro.get('videoPath') and intro['videoPath'] != path:
                intro.setdefault('renderHistory',[]).append({'path':intro['videoPath']})
            intro.update(videoPath=path,renderStale=False)
            return True
        return self.store.mutate(pid,saved)

    def render_submission_signature(self, project, chapter_id=None):
        from studio_render_inputs import render_submission_signature
        return render_submission_signature(project, self.store, self.renderer, chapter_id)

    @staticmethod
    def narration_text(project, chapter):
        return (chapter['cleanNarrationText'] if chapter.get('narrationMode')=='manual'
                else clean_narration(chapter['sourceText'],chapter['name'],chapter.get('includeChapterLabel',False)))

    @staticmethod
    def narration_signature(project, chapter):
        text=StudioService.narration_text(project,chapter)
        return digest({'text':text,'voice':project['settings']['voice'],'speed':project['settings']['speed'],
            'deliveryVersion':AUDIO_DELIVERY_VERSION,'soundEffects':project['settings'].get('soundEffects','subtle'),
            'narrationDelivery':project['settings'].get('narrationDelivery','standard'),
            'emphasisPhrases':project['settings'].get('emphasisPhrases',[]),'flowVersion':FLOW_VERSION})

    def saved_narration_ready(self, project, chapter):
        audio=chapter.get('audio',{})
        return bool(audio.get('signature')==self.narration_signature(project,chapter)
                    and audio.get('path') and self.store.has_asset(project['id'],audio['path']))

    def narration(self, p, ch):
        def narration_input(project,chapter):
            fields=('sourceText','name','narrationMode','includeChapterLabel')
            values={key:chapter.get(key) for key in fields}
            if chapter.get('narrationMode')=='manual':values['cleanNarrationText']=chapter.get('cleanNarrationText')
            return digest({'chapter':values,'voice':{key:project['settings'].get(key) for key in
                ('voice','speed','soundEffects','narrationDelivery','emphasisPhrases')}})
        expected_input=narration_input(p,ch)
        text=self.narration_text(p,ch)
        if not text.strip():raise ValueError('Paste chapter narration first.')
        signature=self.narration_signature(p,ch)
        if self.saved_narration_ready(p,ch):return
        self.status(p["id"], ch["id"], "AUDIO_GENERATING")
        folder = self.store.folder(p["id"]) / ch["id"]
        folder.mkdir(exist_ok=True)
        def prepare_text(q):
            current=get_chapter(q,ch['id'])
            if narration_input(q,current)!=expected_input:
                raise ValueError('Narration inputs changed before generation; current text retained.')
            current.update(cleanNarrationText=text)
        self.store.mutate(p['id'],prepare_text)
        result = self.create_audio(
            text,
            p["settings"]["voice"],
            p["settings"]["speed"],
            folder / f"chapter-{ch['number']:03d}-{signature[:12]}.wav",
            p['settings'].get('soundEffects', 'subtle'),
            p['settings'].get('narrationDelivery', 'standard'),
            p['settings'].get('emphasisPhrases', []),
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
            current=get_chapter(q,ch['id'])
            if narration_input(q,current)!=expected_input:
                current.setdefault('audioHistory',[]).append(result | {'selected':False,
                    'reason':'Narration inputs changed while audio was generated; current text/audio retained.'})
                return False
            get_chapter(q, ch["id"]).update(
                audio=result, status="DIRECTING", renderStale=True
            )
            q["renderStale"] = True
            return True

        if not self.store.mutate(p['id'],audio_saved):
            raise ValueError('Narration changed during generation; earlier audio retained in history.')

    def voice_preview(self, p, options):
        """A bounded local audition never replaces narration or the timeline."""
        text = str(options.get('text', '')).strip()
        if not text or len(text) > 1800:
            raise ValueError('Voice preview needs 1–1800 characters.')
        voice = options.get('voice', p['settings']['voice'])
        speed = options.get('speed', p['settings']['speed'])
        effects = options.get('soundEffects', p['settings'].get('soundEffects', 'subtle'))
        delivery = options.get('narrationDelivery', p['settings'].get('narrationDelivery', 'standard'))
        emphasis = options.get('emphasisPhrases', p['settings'].get('emphasisPhrases', []))
        signature = digest({'text':text, 'voice':voice, 'speed':speed,
                            'effects':effects, 'delivery':AUDIO_DELIVERY_VERSION,
                            'narrationDelivery':delivery, 'emphasisPhrases':emphasis, 'flowVersion':FLOW_VERSION})
        name = f'voice-preview-{signature[:16]}.wav'
        existing = next((v for v in p.get('voicePreviews', []) if v.get('path') == name), None)
        if existing and self.store.asset(p['id'], name).is_file():
            preview = dict(existing)
        else:
            result = self.create_audio(text, voice, speed, self.store.folder(p['id']) / name, effects, delivery, emphasis)
            preview = {**result, 'path':name, 'text':text, 'voice':voice, 'speed':speed,
                       'soundEffects':effects}
        renderer = getattr(self, 'renderer', None)
        if renderer and renderer.config.get('ffmpeg'):
            preview.update(renderer.audio_preview(p['id'], name, self.gate))
        def saved(q):
            previews = q.setdefault('voicePreviews', [])
            previews[:] = [item for item in previews if item['path'] != name]
            previews.append(preview)
            previews[:] = previews[-12:]
        self.store.mutate(p['id'], saved)

    def create_audio(self, text, voice, speed, target, effects='subtle', delivery='standard', emphasis=()):
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
        if delivery not in ('standard', 'cinematic'):
            raise ValueError('Choose standard or cinematic delivery.')
        if not isinstance(emphasis, (list, tuple)) or len(emphasis) > 12 or any(not isinstance(x,str) or not x.strip() or len(x)>100 for x in emphasis):
            raise ValueError('Use up to 12 emphasis phrases of 1–100 characters.')
        # Existing KModel is shared; KPipeline here only prepares phonemes.
        pipeline = KPipeline(
            lang_code=voice[0], repo_id="hexgrad/Kokoro-82M", model=False
        )
        self.audio.prepare(voice)
        sections = sentences(text)
        if delivery == 'cinematic':
            if not callable(getattr(self.audio, 'synthesize_timed', None)):
                raise ValueError('Connected narration needs the updated local helper. Existing audio is preserved.')
            result = create_connected_audio(text,sections,pipeline,self.audio,self.gate,target,voice,speed,effects,emphasis)
            cursor = 0
            for paragraph in re.split(r'\n\s*\n', text):
                count = len(sentences(paragraph))
                if count:
                    result['paragraphs'].append({'index':len(result['paragraphs']), 'text':paragraph,
                        'start':result['sentences'][cursor]['start'], 'end':result['sentences'][cursor+count-1]['end']})
                    cursor += count
            return result
        offset = 0
        timings = []
        narration_rms = .035
        tmp = target.with_suffix(".partial.wav")
        with wave.open(str(tmp), "wb") as wav:
            wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
            for index, sentence in enumerate(sections):
                self.gate(f"Narration sentence {index+1}/{len(sections)}")
                begin = offset
                def deliveries():
                    for segment in audio_segments(sentence, effects):
                        if segment['kind'] == 'effect':
                            yield None, effect_pcm(segment['effect'], narration_rms=narration_rms)
                        elif segment['kind'] == 'pause':
                            yield None, np.zeros(round(segment['duration'] * 24000), dtype='<f4')
                        else:
                            for item in pipeline(segment.get('ttsText', segment['text'])):
                                yield item, None
                for result, effect in deliveries():
                    if effect is not None:
                        wav.writeframes((effect * 32767).astype('<i2').tobytes())
                        offset += len(effect) / 24000
                        continue
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
                        pcm = speech_pcm(raw)
                        narration_rms = float(np.sqrt(np.mean(pcm**2))) if len(pcm) else narration_rms
                        wav.writeframes(
                            (np.clip(pcm, -1, 1) * 32767).astype("<i2").tobytes()
                        )
                        offset += len(pcm) / 24000
                if offset == begin:
                    # Ellipses / isolated punctuation are pauses, not failed words.
                    wav.writeframes(np.zeros(2400, dtype='<i2').tobytes())
                    offset += .1
                timings.append(
                    {
                        "index": index,
                        "text": sentence,
                        "start": begin,
                        "end": offset,
                        "timingSource": "synthesized-sentence-duration",
                        "delivery": audio_segments(sentence, effects),
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
            "deliveryVersion": AUDIO_DELIVERY_VERSION,
            "soundEffects": effects,
        }

    @staticmethod
    def intro_audio_signature(project):
        return digest({'text':project['intro'].get('voiceText','').strip(),
            'voice':{k:project['settings'].get(k) for k in ('voice','speed','soundEffects','narrationDelivery','emphasisPhrases')},
            'deliveryVersion':AUDIO_DELIVERY_VERSION,'flowVersion':FLOW_VERSION})

    def saved_intro_audio_ready(self, project):
        intro=project['intro']
        return bool(intro.get('audioSignature')==self.intro_audio_signature(project)
                    and intro.get('audioPath') and self.store.has_asset(project['id'],intro['audioPath']))

    def intro_audio(self, p, reuse=False):
        text=p['intro'].get('voiceText','').strip()
        if not text:raise ValueError('Enter explicit intro voice text first. Chapter labels are never inserted.')
        signature=self.intro_audio_signature(p)
        old=p['intro'];expected_path=old.get('audioPath','')
        expected_hash=self.store.asset_digest(p['id'],expected_path) if expected_path else None
        if reuse and self.saved_intro_audio_ready(p):return
        # A regenerate action always creates a separate version; never overwrite
        # a master referenced by an earlier render or a newer manual selection.
        name='intro-'+signature[:12]+'-'+uid()+'.wav'
        result=self.create_audio(text,p['settings']['voice'],p['settings']['speed'],self.store.folder(p['id'])/name,
            p['settings'].get('soundEffects','subtle'),p['settings'].get('narrationDelivery','standard'),
            p['settings'].get('emphasisPhrases',[]))
        saved={'audioPath':name,'audioDuration':result['duration'],'audioSentences':result['sentences'],
               'audioTextDigest':digest(text),'audioSignature':signature}
        def publish(q):
            intro=q['intro'];selected=intro.get('audioPath','')
            selected_hash=self.store.asset_digest(p['id'],selected) if selected else None
            if (self.intro_audio_signature(q)!=signature or selected!=expected_path or selected_hash!=expected_hash):
                intro.setdefault('audioHistory',[]).append(saved|{'selected':False,
                    'reason':'Intro inputs or selected audio changed during generation; current edits retained.'})
                return False
            if selected:
                intro.setdefault('audioHistory',[]).append({k:copy.deepcopy(intro.get(k)) for k in saved}|{'selected':False,'reason':'Earlier intro audio version'})
            intro.update(saved,renderStale=True);q['renderStale']=True
            return True
        if not self.store.mutate(p['id'],publish):
            raise ValueError('Intro changed during narration; current text/audio and completed earlier result retained.')

    def plan_intro(self, p):
        """The director visualizes a separately authored, verified recap script."""
        from director_provider import obj, arr, STR
        self.select_director(p)
        self.director.timing_callback = lambda stage,seconds,details: self.record_timing(p['id'],stage,seconds,details | {'intro': True, 'detail': True})
        number = {'type': 'number', 'minimum': 0, 'maximum': p['intro']['duration']}
        camera = obj({k: STR for k in ('shot', 'angle', 'composition')})
        schema = obj({'shots': arr(obj({'start': number, 'end': number,
            'characters': arr({'type': 'string', 'enum': [c['id'] for c in p['characters']]}),
            'action': STR, 'camera': camera, 'lighting': STR, 'prompt': STR,
            'motion': {'enum': ['static', 'slow zoom in', 'slow zoom out', 'pan left', 'pan right', 'pan up', 'pan down']}})) | {'minItems':4,'maxItems':6}})
        context = {'script': p['intro']['voiceText'], 'duration': p['intro']['duration'],
            'timing': p['intro'].get('audioSentences', []), 'verifiedSource': p['intro']['sourceBrief'],
            'people': [{k:c.get(k) for k in ('id','name','description','permanentIdentity','defaultAppearance')} for c in p['characters']],
            'style': p['settings']['style'], 'visualConstraints': p['settings'].get('visualConstraints', '')}
        plan = self.director.call('Direct a recap teaser in the supplied visual style and duration, from the verified source ONLY. The supplied narration script must not change. Open on its strongest concrete conflict or consequential moment, rather than a generic room or landscape. Choose 4–6 individual landscape shots aligned with narration; use purposeful visual escalation and leave an unresolved question only when the script supports it. Cover 0 to duration exactly, without gaps or overlaps. Attach only relevant supplied character IDs and preserve the separate identities and appearance states in the context. No title, labels, borders, collage or captions. Describe one simultaneous moment per prompt; make the illustrations expressive and intentionally composed. Do not invent events, spoil unprovided chapters, or include people beyond the verified chapter range.', context, schema, self.gate)
        cursor = 0
        shots = []
        for item in plan['shots']:
            if abs(item['start']-cursor) > .02 or item['end'] <= item['start']:
                raise ValueError('Director intro shots do not cover the narration in order. Prior intro is preserved.')
            cursor = item['end']
            cast = [{ 'id': cid, 'type': 'main', 'appearanceState': next(c['defaultAppearance'] for c in p['characters'] if c['id']==cid)} for cid in item['characters']]
            shots.append(item | {'id': uid('intro-shot-'), 'chapterId': p['chapters'][0]['id'],
                'characters': cast, 'imagePath': '', 'manual': {}, 'status': 'READY_FOR_IMAGES',
                'intentionalAppearanceChanges': [],
                'generationSettings': copy.deepcopy(p['settings']['image']), 'imageProvider': p['settings']['image']['provider'],
                'imageModel': p['settings']['image']['model'], 'workflow': p['settings']['image']['workflow']})
        if abs(cursor-p['intro']['duration']) > .02:
            raise ValueError('Director intro shots do not reach the end of the intro.')
        self.store.mutate(p['id'], lambda q:q['intro'].update(shots=shots, directorProvider=p['settings']['director']['provider'], directorPlan=plan))

    def check_cloud_budget(self, p, provider):
        rental=getattr(self,'_production_gpu',None)
        if provider.id=='comfyui' and rental:
            rental.check();return
        window = p['settings'].get('cloudWindow', {})
        if provider.id == 'comfyui' and window.get('cloudStartedAt'):
            from economy_studio import cloud_cost
            estimate = cloud_cost(window['cloudStartedAt'], window['gpuHourlyUSD'],
                                  window.get('gpuPreviouslySpentUSD', 0), window.get('imageReserveSeconds', 90)+25)
            if estimate > window.get('gpuBudgetUSD', float('inf')):
                message = 'Cloud budget reached: queue paused with completed images and seeds preserved. Stop the Runpod pod now; pausing this queue does not stop rental billing.'
                owner=self._overlap.root if getattr(self,'_overlap',None) else self
                with owner.cv:
                    owner.paused = True
                self.store.mutate(p['id'], lambda latest: latest.setdefault('production', {}).update(
                    status='PAUSED', stage='Cloud budget reached — stop the rented GPU',
                    message=message, budgetBlocked=True, updated=time.time()))
                raise CloudBudgetPaused(message)

    def inspect_intro(self, p, sampled_only=False):
        if p['settings'].get('qcCheckLevel') == 'off':
            if sampled_only:return
            raise ValueError('Visual checks are Off. Enable checks in Settings first.')
        for shot in p['intro'].get('shots', []):
            if sampled_only and not should_check(p,shot):continue
            if not shot.get('imagePath'):
                raise ValueError('Generate every intro shot before visual review.')
            checked_path=shot['imagePath']
            checked_digest=self.store.asset_digest(p['id'],checked_path)
            checked_spec=self.visual_spec_signature(p,shot)
            existing=shot.get('qc',{})
            if qc_decision(p,shot,self.store)['disposition']=='accepted':continue
            if (existing.get('checkedImagePath')==checked_path
                    and existing.get('checkedImageSHA256')==checked_digest
                    and existing.get('shotSpecSignature')==checked_spec
                    and not review_settings_stale(p,shot)
                    and (existing.get('status')=='PASSED' or sampled_only
                         and existing.get('status') not in (None,'PENDING','UNREVIEWED'))):continue
            qc = self.visual_check(p, shot | {'intentionalAppearanceChanges': shot.get('intentionalAppearanceChanges', [])}, self.store.asset(p['id'], shot['imagePath']))
            qc['status'] = 'PASSED' if qc['pass'] else 'REVIEW_REQUIRED' if qc.get('action') == 'review' else 'FAILED'
            qc.update(checkedImagePath=checked_path,checkedImageSHA256=checked_digest,shotSpecSignature=checked_spec)
            def save(latest):
                item = next(s for s in latest['intro']['shots'] if s['id'] == shot['id'])
                if (item.get('imagePath')!=checked_path
                        or self.store.asset_digest(p['id'],checked_path)!=checked_digest
                        or self.visual_spec_signature(latest,item)!=checked_spec
                        or qc.get("reviewSettingsSignature",review_settings_signature(latest["settings"]))!=review_settings_signature(latest["settings"])):
                    self.retain_stale_qc(item,qc,checked_path)
                    return False
                item.update(qc=qc)
                item['qcDecision']=qc_decision(latest,item,self.store)
                item['status']=production_status(latest,item,self.store)
                return True
            if not self.store.mutate(p['id'], save):
                raise RuntimeError('Intro changed during visual review; current edits and earlier findings retained.')

    def intro_image(self, p, options=None):
        options = options or {}
        if options.get('repairShot'):
            selected=next((s for s in p['intro'].get('shots',[]) if s['id']==options['repairShot']),None)
            if selected is None:raise ValueError('Intro repair shot is unavailable; original assets retained.')
            reason=repair_budget_reason(p['settings'])
            if reason:raise ValueError(reason)
            if selected.get('manual',{}).get('imagePath'):
                raise ValueError('Intro image is manually locked; original selection retained.')
            signature=self.visual_spec_signature(p,selected)
            if (selected.get('automaticRepairSpec')==signature
                    and selected.get('automaticRepairAttempts',0)>=p['settings']['maxImageRetries']):
                raise ValueError('Automatic intro repair limit reached; no rental started.')
        self.ensure_production_gpu()
        self.unload_models()
        provider = self.provider(p["settings"]["image"]["provider"])
        self.prepare_image_device(provider)
        if p['settings'].get('cloudImagesOnly') and provider.id != 'comfyui':
            raise ValueError('Local image generation is disabled for this project.')
        if p['intro'].get('shots'):
            for shot in p['intro']['shots']:
                repair=options.get('repairShot')==shot['id']
                if options.get('repairShot') and not repair:continue
                if shot.get('imagePath') and self.store.has_asset(p['id'], shot['imagePath']) and not repair:
                    continue
                expected_spec=self.visual_spec_signature(p,shot)
                expected_path=shot.get('imagePath','')
                expected_hash=self.store.asset_digest(p['id'],expected_path) if expected_path else None
                from studio_automation import repair_operation, repair_prompt
                operation=repair_operation(p,shot,provider) if repair else 'generate'
                if repair:
                    reason=repair_budget_reason(p['settings'])
                    if reason:raise ValueError(reason)
                    self.reserve_automatic_repair(p['id'],None,shot['id'],self.visual_spec_signature(p,shot),intro=True)
                self.check_cloud_budget(p, provider)
                references, metadata = select_references(p, shot, self.store, provider,reserved_slots=1 if operation=='edit' else 0)
                settings = provider.validateSettings(shot['generationSettings'])
                base_prompt=repair_prompt(shot,shot.get('qc',{}).get('repairPrompt'),operation) if repair else shot['prompt']
                prompt = reference_prompt(p, shot, metadata,operation) + ' ' + base_prompt if references else base_prompt
                result = provider.generateImage({'prompt': prompt, 'negativePrompt': '', 'referenceImages': references,
                    'settings': settings, 'workflow': shot['workflow'], 'operation': operation,
                    **({'sourceImage':data_url(self.store.asset(p['id'],shot['imagePath']))} if operation=='edit' else {})}, self.checkpoint)
                name = shot['id'] + '-' + uid() + '.png'
                target = self.store.folder(p['id']) / name
                partial = target.with_suffix('.partial.png')
                result.pop('pil').save(partial, 'PNG'); partial.replace(target)
                def save_intro(latest):
                    item = next(s for s in latest['intro']['shots'] if s['id']==shot['id'])
                    selected=item.get('imagePath','')
                    selected_hash=self.store.asset_digest(p['id'],selected) if selected else None
                    if (selected!=expected_path or selected_hash!=expected_hash
                            or self.visual_spec_signature(latest,item)!=expected_spec):
                        item.setdefault('history',[]).append({'imagePath':name,'imageMetadata':result,
                            'selected':False,'reason':'Intro changed during generation; current edits retained.'})
                        return False
                    if item.get('imagePath'):
                        item.setdefault('history',[]).append({k:copy.deepcopy(item.get(k)) for k in ('imagePath','imageMetadata','qc')})
                    item.update(imagePath=name, status='COMPLETE', referenceImages=metadata,
                        imageMetadata=result | {'prompt': prompt, 'referenceImages': metadata}, qc={'status': 'PENDING' if should_check(p,shot) else 'UNCHECKED', 'pass': None, 'checkLevel': check_level(p['settings'])})
                    latest['renderStale'] = True
                    return True
                if not self.store.mutate(p['id'], save_intro):
                    raise RuntimeError('Intro changed during automatic generation; current edits and completed image retained.')
                if should_check(p,shot) and not options.get('deferQC'):
                    review_shot = shot | {'intentionalAppearanceChanges': shot.get('intentionalAppearanceChanges', [])}
                    self.director.timing_callback = lambda stage,seconds,details: self.record_timing(p['id'], stage, seconds, details | {'intro': True, 'detail': True})
                    qc = self.visual_check(self.store.load(p['id']), review_shot, target)
                    qc['status'] = 'PASSED' if qc['pass'] else 'REVIEW_REQUIRED'
                    qc.update(checkedImagePath=name,checkedImageSHA256=self.store.asset_digest(p['id'],name),
                              shotSpecSignature=expected_spec)
                    def save_review(latest):
                        item = next(s for s in latest['intro']['shots'] if s['id'] == shot['id'])
                        if (item.get('imagePath')!=name
                                or self.store.asset_digest(p['id'],name)!=qc['checkedImageSHA256']
                                or self.visual_spec_signature(latest,item)!=expected_spec
                                or qc.get("reviewSettingsSignature",review_settings_signature(latest["settings"]))!=review_settings_signature(latest["settings"])):
                            self.retain_stale_qc(item,qc,name)
                            return False
                        item['qc'] = qc
                        item['qcDecision']=qc_decision(latest,item,self.store)
                        item['status']=production_status(latest,item,self.store)
                        return True
                    if not self.store.mutate(p['id'], save_review):
                        raise RuntimeError('Intro changed during automatic review; current edits and findings retained.')
                    latest=self.store.load(p['id'])
                    checked=next(s for s in latest['intro']['shots'] if s['id']==shot['id'])
                    if qc_decision(latest,checked,self.store)['blocking'] and not options.get('_collectReview'):
                        raise ValueError('Intro image needs manual review. Accept it or request a repair explicitly; no automatic repair was purchased.')
            return
        self.check_cloud_budget(p, provider)
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
        if (affected or sound_only) and not any(c["id"] == temporary["id"] for c in ch["people"]):
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
        expected_reference=self.reference_input_signature(p,options['characterId'])
        self.unload_models()
        provider = self.provider(p["settings"]["image"]["provider"])
        self.prepare_image_device(provider)
        if p['settings'].get('cloudImagesOnly') and provider.id != 'comfyui':
            raise ValueError('Local image generation is disabled for this project.')
        self.check_cloud_budget(p, provider)
        if provider.id != "existing":
            self.providers["existing"].unload()
        person = next(c for c in p["characters"] if c["id"] == options["characterId"])
        settings = provider.validateSettings(p["settings"]["image"])
        identity = "; ".join(f"{field}: {value}" for field, value in person["permanentIdentity"].items() if value)
        appearance = "; ".join(f"{field}: {value}" for field, value in person["defaultAppearance"].items() if value)
        portrait_kind = options.get("referenceKind", "face")
        framing = "Head and shoulders only; hands and wrists outside the frame." if portrait_kind == "face" else "Show the requested reference angle clearly."
        prompt = f"One single-character {portrait_kind} identity reference portrait of {person['name']}. {person['description']}. Exact permanent traits: {identity}. Default appearance: {appearance}. {framing} Show only the specified distinguishing marks; do not add extra wounds, blood, scars or tattoos. A stated scar is a healed identity mark, not a fresh injury. {p['settings']['style']}. Neutral plain background, no other people, no text."
        refs = [
            data_url(self.store.asset(p["id"], r["path"]))
            for r in person["references"][
                : provider.getCapabilities().get("maxReferenceImages", 0)
            ]
        ]
        reference_roles = [{'path':r['path'],'role':'identity'} for r in person['references'][:len(refs)]]
        maximum = provider.getCapabilities().get('maxReferenceImages',0)
        if maximum > 1 and provider.getCapabilities().get('supportsStyleReference') and p.get('styleReferences'):
            refs = refs[:maximum-1]
            reference_roles = reference_roles[:maximum-1]
            style_ref = p['styleReferences'][0]
            refs.append(data_url(self.store.asset(p['id'],style_ref['path'])))
            reference_roles.append({'path':style_ref['path'],'role':'style'})
            prompt += f' Use image {len(refs)} for illustration linework and palette only, not its people or objects. Earlier images establish identity.'
        if refs and provider.id == "native-flux":
            settings.update(width=384, height=384)
        operation = 'generate'
        repair_source = options.get('repairReferencePath')
        if repair_source:
            known_paths = {r['path'] for r in person['references']}
            identity_path = options.get('identityReferencePath')
            if repair_source not in known_paths or identity_path not in known_paths:
                raise ValueError('Reference repairs must use this character’s saved source and identity references.')
            if not provider.getCapabilities().get('supportsImageEditing'):
                raise ValueError('Select a provider that supports reference image editing.')
            prompt = str(options.get('repairPrompt','')).strip()
            if not prompt:
                raise ValueError('A targeted reference repair prompt is required.')
            operation = 'edit'
            refs = [data_url(self.store.asset(p['id'],identity_path))]
            reference_roles = [{'path':repair_source,'role':'edit-source'}, {'path':identity_path,'role':'identity'}]
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
                "purpose": "character-reference",
                "operation": operation,
                **({'sourceImage':data_url(self.store.asset(p['id'],repair_source))} if repair_source else {}),
            },
            self.checkpoint,
        )
        name = "references/" + uid() + ".png"
        path = self.store.asset(p["id"], name)
        path.parent.mkdir(exist_ok=True)
        staged=path.with_suffix('.partial.png')
        result.pop("pil").save(staged,"PNG")
        with staged.open('r+b') as handle:os.fsync(handle.fileno())
        staged.replace(path)

        def save(latest):
            current=next((c for c in latest['characters'] if c['id']==person['id']),None)
            if self.reference_input_signature(latest,person['id'])!=expected_reference:
                owner=current if current is not None else latest
                owner.setdefault('referenceHistory',[]).append({'path':name,'characterId':person['id'],
                    'metadata':result,'selected':False,'reason':'Reference inputs changed during generation; current selection retained.'})
                return False
            next(c for c in latest["characters"] if c["id"] == person["id"])[
                "references"
            ].insert(0,
                {
                    "path": name,
                    "kind": options.get("referenceKind", "face"),
                    "metadata": result | {"prompt": prompt, "referenceRoles":reference_roles, "requestedSettings": settings,
                                           "settings": result.get("settings", settings)},
                }
            )

        if self.store.mutate(p['id'],save) is False:
            raise ValueError('Character reference changed during generation; earlier result retained in reference history.')

    def reference_input_signature(self,project,character_id):
        person=next((c for c in project['characters'] if c['id']==character_id),None)
        if person is None:return None
        references=person.get('references',[])+project.get('styleReferences',[])
        fingerprints=[]
        for reference in references:
            path=self.store.asset(project['id'],reference['path'])
            fingerprints.append({'reference':reference,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()
                if path.is_file() else None})
        return digest({'character':{key:person.get(key) for key in
            ('id','name','description','permanentIdentity','defaultAppearance','references')},
            'style':project['settings']['style'],'image':project['settings']['image'],
            'referenceAssets':fingerprints})

    def director_calls(self, project, chapter, calls, expected=None):
        from director_tasks import validate_director_result
        overlap=getattr(self,'_overlap',None)
        if (overlap and self is overlap.main
                and project['settings']['director'].get('parallelism',1)>1):
            return overlap.director_calls(project,chapter,calls,expected)
        return [validate_director_result(method,context,getattr(self.director,method)(context,self.gate))
            for method,context in calls]

    def director_group_limits(self,remote):
        # Keep production context sizes unchanged. Private matched test services
        # can override this seam without changing installed project settings.
        return (9000,48) if remote else (2500,12)

    def analyze_chapter(self,p,chid,options):
        staged=p['settings']['director'].get('executionMode','classic').startswith('staged')
        if not staged or options.get('sceneId') or getattr(self,'_overlap',None):
            return self.analyze(p,chid,options)
        from studio_overlap import CloudStoryOverlap
        overlap=CloudStoryOverlap(self,p,{'overlap':True})
        status,message='COMPLETE','Chapter plan saved'
        try:
            overlap.main.analyze(p,chid,{**options,'offlinePlanning':True,'managedPipeline':True})
        except BaseException as error:
            status='CANCELLED' if isinstance(error,JobCancelled) else 'FAILED';message=str(error)[:1800]
            raise
        finally:overlap.close(status,message)
        if p['settings'].get('autoContinue') and not options.get('managedPipeline'):
            current=self.store.load(p['id']);chapter=get_chapter(current,chid)
            needs_review=current['settings']['appearanceHandling']!='Automatic' and any(
                scene['appearanceChanges'] and not scene.get('appearanceChangesReviewed') for scene in chapter['scenes'])
            if not chapter.get('proposedPlan') and not needs_review:
                self.enqueue(p['id'],chid,'image',[shot['id'] for scene in chapter['scenes'] for shot in scene['shots']])

    def analyze(self, p, chid, options):
        expected_analysis=self.analysis_input_signature(p,chid)
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
        casting_limit = 32000 if isinstance(self.director,OpenAIDirector) and p['settings']['director'].get('executionMode','classic').startswith('staged') else 8500
        for casting_text in text_groups(ch["sourceText"],max_chars=casting_limit):
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
                    profile['permanentIdentity'] = grounded_identity(profile['permanentIdentity'], evidence)
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
        if p['settings']['director'].get('executionMode')=='staged-lean':
            from director_staged import relevant_main_cast
            chapter_index=next(i for i,item in enumerate(p['chapters']) if item['id']==chid)
            previous=next((item for item in reversed(p['chapters'][:chapter_index]) if item.get('scenes')),None)
            known_people=relevant_main_cast(known_people,ch,passes,previous)
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
        remote_director = isinstance(self.director, OpenAIDirector)
        group_chars, group_sentences = self.director_group_limits(remote_director)
        if remote_director and p['settings']['director'].get('executionMode','classic').startswith('staged'):
            from director_staged import fact_group_limits
            group_sentences,group_chars=fact_group_limits(p['settings']['director'])
        for item in timings:
            if current and (size + len(item["text"]) > group_chars or len(current) >= group_sentences):
                groups.append(current)
                current = []
                size = 0
            current.append(item)
            size += len(item["text"])
        if current:
            groups.append(current)
        provider = self.provider(p["settings"]["image"]["provider"])
        offline_planning = options.get('offlinePlanning') and provider.id == 'comfyui'
        health = provider.planningCatalog() if offline_planning else provider.healthCheck()
        if not health["installed"] and not (offline_planning and health.get('configured')):
            raise ValueError(
                "Selected image workflow is unavailable. Choose an installed model before analyzing."
            )
        staged = p['settings']['director'].get('executionMode','classic').startswith('staged') and not selected_scene
        if staged and (not remote_director or not getattr(self,'_overlap',None)):
            raise ValueError('Staged Luna directing needs cloud overlap and full-chapter analysis. Existing assets stay saved.')
        prepared = None
        if staged:
            from director_staged import prepare_staged
            prepared = prepare_staged(self,p,ch,groups,chapter_cast,state,memory,expected_analysis)
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
            compact_state = {k: v for k, v in state.items() if k not in ("appearanceHistory","environmentHistory","environmentScopes")}
            context = {
                "sentences": text,
                "knownMainCharacters": canonical,
                "priorState": compact_state,
                "storyMemory": memory,
                "layoutMode": p["settings"]["layoutMode"],
                "customTargets": p["settings"]["customLayout"],
                "chapterCast": chapter_cast,
                "visualConstraints": p['settings'].get('visualConstraints', ''),
                "productionDirection": p['settings'].get('productionDirection', ''),
            }
            self.gate(f"Analyzing group {group_index+1}/{len(groups)}")
            ready = prepared[group_index] if prepared else None
            analysis = copy.deepcopy(ready['analysis']) if ready else self.director.analyzeStory(context, self.gate)
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
                if detected.get('permanentIdentity'):
                    detected['permanentIdentity'] = grounded_identity(
                        detected['permanentIdentity'], detected.get('evidence', ''))
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
            plan = copy.deepcopy(ready['storyboard']['plan']) if ready else self.director.planChapter(
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
            detail = copy.deepcopy(ready['storyboard']['detail']) if ready else self.director.planScenes(scene_context, self.gate)
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
            cameras = {x['shotIndex']:x for x in ready['storyboard']['cameras']} if ready else {}
            finishing_calls=[]
            if not ready and p["settings"]["generationMode"] != "QUICK":
                finishing_calls.append(('planLayout',
                    {
                        "shots": details,
                        "style": p["settings"]["style"],
                        "mode": p["settings"]["layoutMode"],
                    }))
            finishing_calls.append(('selectImageWorkflow',
                {
                    "available": [
                        {
                            "provider": provider.id,
                            "models": [p['settings']['image']['model']] if p['settings'].get('lockImageModel') else health["models"],
                            "workflows": [p['settings']['image']['workflow']] if p['settings'].get('lockImageModel') else health["workflow"],
                            "capabilities": provider.getModelCapabilities(p['settings']['image']['model']) if hasattr(provider, 'getModelCapabilities') else provider.getCapabilities(),
                        }
                    ],
                    "selected": p["settings"]["image"],
                    "mainCharacterReferences": sum(
                        bool(c["references"]) for c in p["characters"]
                    ),
                }))
            if not ready:
                finishing_calls.append(('checkContinuity',
                {
                    "knownState": compact_state,
                    "people": cast,
                    "objects": analysis["objects"],
                    "changes": analysis["changes"],
                    "sentences": text,
                    "shots": details if p["settings"]["generationMode"] != "QUICK" else [],
                }))
            finishing_results=self.director_calls(p,chid,finishing_calls,expected_analysis)
            if not ready and p["settings"]["generationMode"] != "QUICK":
                result=finishing_results.pop(0)
                cameras={x["shotIndex"]:x for x in result["cameras"]}
                passes.append({"pass":"cinematographer","group":group_index,"output":result})
            if ready:
                workflow=finishing_results[0]
                continuity=copy.deepcopy(ready['continuity'])
                passes.append({'pass':'staged-storyboard','group':group_index,
                    'output':ready['storyboard'],'factContext':digest(ready['context'])})
                if ready.get('visualReview') is not None:
                    passes.append({'pass':'storyboard-continuity-review','group':group_index,'output':ready['visualReview']})
                if ready.get('repairHistory'):
                    passes.append({'pass':'targeted-storyboard-repair','group':group_index,'output':ready['repairHistory']})
            else:
                workflow,continuity=finishing_results
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
                object_changes = align_evidence(object_changes, text)
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
            require_owners(analysis['changes'],cast)
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
                    set_environment_location(state,rs['location'],scene['locationId'])
                    for e in analysis.get("environmentChanges", []):
                        if (
                            sa <= e["sentence"] <= sb
                            and e["reason"].casefold()
                            in group[e["sentence"]]["text"].casefold()
                        ):
                            apply_environment_event(state,e,ch['number'],id)
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
                    if staged:
                        from director_staged import apply_source_changes
                        state=apply_source_changes(state,changes,ch['number'],id)
                    else:
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
                    settings = (provider.validateSettings(shot_settings, check_hardware=False)
                                if offline_planning else provider.validateSettings(shot_settings))
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
                    if ready:
                        supplement=ready['storyboard']['prompts'][cursor-1]['prompt']
                        shot['directorPrompt']=supplement
                        # Full prompt already contains these AI-selected visual
                        # fields. Do not append a second contradictory paraphrase.
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
        if not staged and p["settings"]["generationMode"] != "QUICK":
            planned_shots = [s for scene in scenes for s in scene["shots"]]
            prompt_batch = 12 if remote_director else 4
            prompt_groups=[]
            prompt_calls=[]
            for begin in range(0, len(planned_shots), prompt_batch):
                group = planned_shots[begin : begin + prompt_batch]
                prompt_groups.append(group)
                prompt_calls.append(('writeImagePrompt',
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
                    }))
            prompt_outputs=self.director_calls(p,chid,prompt_calls,expected_analysis)
            for group,output in zip(prompt_groups,prompt_outputs):
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

        if options.get('unattended') and any(scene.get('manual') or any(s.get('manual') for s in scene['shots']) for scene in ch['scenes']):
            from studio_automation import preserve_manual_plan
            scenes=preserve_manual_plan(ch['scenes'],scenes)
            known={person['id'] for person in people}
            people += [copy.deepcopy(person) for person in ch.get('people',[]) if person['id'] not in known]

        def finish(latest):
            chapter = get_chapter(latest, chid)
            if self.analysis_input_signature(latest,chid)!=expected_analysis:
                chapter['proposedPlan']={'scenes':scenes,'people':people,'analyses':analyses,
                    'sourceSignature':digest(ch['sourceText']),
                    'reason':'Chapter inputs changed during direction; current text and plan retained.'}
                return
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
            if manual_shots and not options.get("replaceManual", False) and not options.get('unattended'):
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
            if options.get('unattended') and chapter.get('proposedPlan'):
                chapter.setdefault('proposedPlanHistory',[]).append(chapter.pop('proposedPlan'))
            chapter.update(
                people=people,
                scenes=scenes,
                analysis={
                    "groups": analyses,
                    "passes": passes,
                    "providerRecommendation": workflow,
                    **({'executionId':self.execution_context.identity,'inputHash':self.execution_context.input_hash}
                        if getattr(self,'execution_context',None) else {}),
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

    @staticmethod
    def analysis_input_signature(project,chid):
        chapter=get_chapter(project,chid)
        incoming_state,incoming_memory=state_before(project,chid)
        fields=('sourceText','name','scenes','people','narrationMode','cleanNarrationText','includeChapterLabel')
        return digest({'chapter':{key:chapter.get(key) for key in fields},
            'audio':chapter.get('audio',{}).get('signature'),'settings':project['settings'],
            'inputState':incoming_state,'inputMemory':incoming_memory,
            'identities':[{key:person.get(key) for key in ('id','name','description','aliases',
                'permanentIdentity','defaultAppearance','references')}
                for person in project['characters']]})

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
        if getattr(self,'_overlap',None):self.validate_execution()
        self.director.stop()
        ch = get_chapter(p, chid)
        shot = options.get("shotSnapshot") or get_shot(p, chid, sid)
        if options.get('budgetedQCRepair') or (shot.get('imagePath') and not shot.get('generationStale')
                and shot.get('qc',{}).get('pass') is False):
            reason=repair_budget_reason(p['settings'])
            if reason:raise ValueError(reason)
        preflight = require_shot(p, shot,new_image=True)
        expected_spec = self.visual_spec_signature(p, shot)
        expected_path = shot.get('imagePath','')
        def selected_digest(project):
            current=get_shot(project,chid,sid)
            selected=current.get('imagePath')
            asset=self.store.asset(project['id'],selected) if selected else None
            return hashlib.sha256(asset.read_bytes()).hexdigest() if asset and asset.is_file() else None
        expected_image_digest=selected_digest(p)
        provider = self.provider(shot["imageProvider"])
        self.prepare_image_device(provider)
        if p['settings'].get('cloudImagesOnly') and provider.id != 'comfyui':
            raise ValueError('Local image generation is disabled for this project.')
        if provider.id != "native-flux":
            self.providers["native-flux"].unload()
        if provider.id != "existing":
            self.providers["existing"].unload()
        self.status(p["id"], chid, "GENERATING")
        settings = shot["generationSettings"] | {
            "model": shot["imageModel"],
            "seed": seed,
        }
        operation = options.get("operation", "generate")
        references, ref_metadata = select_references(
            p, shot, self.store, provider,
            reserved_slots=1 if provider.id == "comfyui" and operation == "edit" else 0,
        )
        if provider.id == "native-flux" and options.get("operation") == "edit":
            references = references[:1]
            ref_metadata = ref_metadata[:1]
        request = {
            "prompt": shot["prompt"],
            "negativePrompt": shot["negativePrompt"],
            "referenceImages": references,
            "settings": settings,
            "operation": options.get("operation", "generate"),
            "workflow": shot["workflow"],
        }
        if options.get('_repairPrompt'):
            from studio_automation import repair_prompt
            request['prompt']=repair_prompt(shot,options['_repairPrompt'],operation)
        if provider.id in ("native-flux", "comfyui") and (
            references or request["operation"] == "edit"
        ):
            request["prompt"] = reference_prompt(
                p, shot, ref_metadata, request["operation"]
            ) + " " + request["prompt"]
        if request["operation"] in ("edit", "inpaint"):
            source = (shot.get('imagePath') if options.get('budgetedQCRepair') else
                      shot.get("sourceImagePath") or shot.get("imagePath"))
            if not source:
                raise ValueError(
                    "Image repair needs a generated or uploaded source image."
                )
            request["sourceImage"] = data_url(self.store.asset(p["id"], source))
            shot['sourceImagePath']=source
            request["mask"] = options.get("mask")
        retries = int(p["settings"]["maxImageRetries"])
        if options.get('deferAutomaticRepair'):retries=0
        if options.get('budgetedQCRepair') and p['settings'].get('unattendedProduction'):
            used=shot.get('automaticRepairAttempts',0) if shot.get('automaticRepairSpec')==expected_spec else 0
            retries=max(0,retries-used)
        qc = {"status": "PENDING" if should_check(p,shot) else "UNCHECKED", "pass": None,
              "checkLevel": check_level(p["settings"]), "sampleEvery": p["settings"].get("qcSampleEvery",5)}
        attempts = []
        fallback_used = False
        actual_workflow = shot["workflow"]
        max_attempts = (
            retries + 1 + (1 if p["settings"]["image"].get("fallbackEnabled") else 0)
        )
        if options.get('budgetedQCRepair'):
            if retries<1:raise ValueError('Image repair is disabled by the zero-retry limit.')
            # This first request is already a replacement, not a free initial
            # image. Do not silently purchase maxImageRetries + 1 repairs.
            max_attempts=retries
        for attempt in range(max_attempts):
            self.gate(f'Generating {ch["name"]} · shot {sid} · attempt {attempt+1}')
            self.check_cloud_budget(p, provider)
            if p['settings'].get('unattendedProduction') and (options.get('budgetedQCRepair') or attempt>0):
                self.reserve_automatic_repair(p['id'],chid,sid,expected_spec)
            try:
                with self.trace.span(p['id'], 'Image backend', {'chapter': ch['number'], 'shot': sid,
                                                               'provider': provider.id, 'model': settings['model'], 'seed': seed}):
                    result = provider.generateImage(request, self.checkpoint)
            except (JobCancelled, AudioYield, RemoteGenerationUncertain):
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
                if attempt>=max_attempts-1:
                    raise
                fallback = p["settings"]["image"].get("fallback")
                if (
                    not fallback_used
                    and p["settings"]["image"].get("fallbackEnabled")
                    and fallback
                    and provider.id != fallback["provider"]
                ):
                    if p['settings'].get('cloudImagesOnly') and fallback['provider'] != 'comfyui':
                        raise ValueError('Cloud image generation failed. The configured local fallback is disabled for this cloud-only project.') from e
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
            with self.trace.span(p['id'], 'Save image', {'chapter': ch['number'], 'shot': sid}):
                result.pop("pil").save(tmp, "PNG")
                with tmp.open('r+b') as handle:os.fsync(handle.fileno())
                tmp.replace(path)
            relative = str(path.relative_to(self.store.folder(p["id"]))).replace(
                "\\", "/"
            )
            effective_settings = result.get("settings", settings)
            metadata = {
                "preflight": preflight,
                "provider": provider.id,
                "model": result.get("model", settings["model"]),
                "workflow": result.get("workflow", actual_workflow),
                "requestedModel": settings["model"],
                "requestedWorkflow": actual_workflow,
                "sampler": effective_settings.get("sampler"),
                "scheduler": effective_settings.get("scheduler"),
                "seed": seed,
                "resolution": [settings["width"], settings["height"]],
                "loras": effective_settings.get("loras", []),
                "controlnets": settings.get("controlnets", []),
                "ipAdapter": settings.get("ipAdapter", {}),
                "denoisingStrength": effective_settings.get("denoisingStrength"),
                "referenceImages": ref_metadata,
                "sourceImagePath": shot.get("sourceImagePath"),
                "prompt": request["prompt"],
                "promptMeasurements": generation_measurements(p,request["prompt"],result),
                "negativePrompt": request["negativePrompt"],
                "settings": effective_settings,
                "requestedSettings": settings,
                "result": result,
                "time": time.time(),
                "attempt": attempt,
                "intentionalAppearanceChanges": shot["intentionalAppearanceChanges"],
                "fallbackUsed": fallback_used,
                "imageSHA256":hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            if getattr(self,'execution_context',None):
                metadata['executionId']=self.execution_context.identity
            attempts.append(metadata)

            # Save before any QC request so a cancel or crash cannot erase the PNG.
            def saved(latest):
                s = get_shot(latest, chid, sid)
                if (s.get('imagePath','') != expected_path
                        or selected_digest(latest)!=expected_image_digest
                        or self.visual_spec_signature(latest, s) != expected_spec):
                    s.setdefault('history', []).append({'imagePath':relative,'metadata':metadata,
                        'qc':{'status':'UNREVIEWED','pass':None},'selected':False,
                        'reason':'Shot or selected image changed during generation; current selection preserved.'})
                    return False
                if s.get("imagePath"):
                    s.setdefault('history',[]).append(
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
                    sourceImagePath=metadata.get('sourceImagePath') or '',
                    retryCount=attempt,
                    status="QC",
                    qc=copy.deepcopy(qc),
                    generationError="",
                )
                s["generationStale"] = (
                    s["prompt"] != shot["prompt"]
                    or s["imageModel"] != settings["model"]
                )
                get_chapter(latest, chid)["renderStale"] = True
                latest["renderStale"] = True
                return True

            sidecar=path.with_suffix('.json')
            sidecar_temp=sidecar.with_suffix('.json.partial')
            with sidecar_temp.open('w',encoding='utf-8') as handle:
                handle.write(json.dumps(metadata,indent=2,ensure_ascii=False))
                handle.flush();os.fsync(handle.fileno())
            sidecar_temp.replace(sidecar)
            selected = self.store.mutate(p["id"], saved)
            if not selected:
                return {'status':'SUPERSEDED','imagePath':relative,'selected':False}
            expected_path = relative
            expected_image_digest=metadata['imageSHA256']
            if not options.get('deferQC') and should_check(p,shot):
                provider.unload()
                self.providers["existing"].unload()
                self.status(p["id"], chid, "QC")
                qc = self.visual_check(p, shot, path)
                qc["status"] = (
                    "PASSED"
                    if qc.get("pass") is True
                    else "REVIEW_REQUIRED" if qc.get("action") == "review"
                    else "FAILED" if qc.get("pass") is False else "UNREVIEWED"
                )
                qc.update(checkedImagePath=relative,checkedImageSHA256=hashlib.sha256(path.read_bytes()).hexdigest(),
                          shotSpecSignature=expected_spec)
                current = self.store.load(p['id'])
                current_shot = get_shot(current,chid,sid)
                if (current_shot.get('imagePath','') != expected_path
                        or selected_digest(current)!=expected_image_digest
                        or self.visual_spec_signature(current,current_shot) != expected_spec
                        or qc.get("reviewSettingsSignature",review_settings_signature(current["settings"]))!=review_settings_signature(current["settings"])):
                    self.store.mutate(p['id'],lambda latest:self.retain_stale_qc(
                        get_shot(latest,chid,sid),qc,relative))
                    return {'status':'SUPERSEDED','imagePath':relative,'selected':False}
            if (
                qc.get("pass") is False
                and (qc.get("action") != "review" or p['settings'].get('unattendedProduction'))
                and automatic_repairs(self.store.load(p["id"])["settings"],qc)
                and attempt < retries
            ):
                self.status(p["id"], chid, "REPAIRING")
                from studio_automation import repair_prompt
                request["prompt"] = repair_prompt(shot,qc.get('repairPrompt'),request['operation'])
                if (
                    provider.getCapabilities()["supportsImageEditing"]
                    and provider.id != "existing"
                    and len(references)<provider.getCapabilities().get('maxReferenceImages',0)
                ):
                    request.update(operation="edit", sourceImage=data_url(path))
                if request.get("operation") == "edit":
                    shot["sourceImagePath"] = relative
                    if provider.id in ("native-flux", "comfyui"):
                        request["prompt"] = reference_prompt(p, shot, ref_metadata, "edit") + " Edit only the confirmed defects and preserve the scene: " + request["prompt"]
                continue
            break

        def complete(latest):
            s = get_shot(latest, chid, sid)
            if (s.get('imagePath','') != expected_path
                    or selected_digest(latest)!=expected_image_digest
                    or self.visual_spec_signature(latest,s) != expected_spec
                    or qc.get("reviewSettingsSignature",review_settings_signature(latest["settings"]))!=review_settings_signature(latest["settings"])):
                self.retain_stale_qc(s,qc,relative)
                return
            s.update(
                qc=qc,
                retryHistory=attempts,
            )
            s["qcDecision"] = qc_decision(latest,s,self.store)
            s.update(status=production_status(latest,s,self.store)
            )
            chapter = get_chapter(latest, chid)
            allshots = [x for sc in chapter["scenes"] for x in sc["shots"]]
            chapter["status"] = (
                "COMPLETE"
                if all(
                    self.saved_image_ready(latest,x)
                    for x in allshots
                )
                else "READY_FOR_IMAGES"
            )

        self.store.mutate(p["id"], complete)
        current = self.store.load(p["id"])
        if (qc_decision(current,get_shot(current,chid,sid),self.store)["blocking"]
                and qc.get('status')!='PENDING' and not options.get('_collectReview')):
            raise RuntimeError(
                "Visual QC needs your review. The image and AI findings are saved. Accept it to continue; repair requires a supported cost estimate."
            )

    def saved_image_ready(self, project, shot):
        return bool(shot.get("imagePath") and not shot.get("generationStale")
            and self.store.has_asset(project["id"],shot["imagePath"])
            and not qc_decision(project,shot,self.store)["blocking"]
            and (shot.get("status") in ("COMPLETE","PASSED") or shot.get("qc",{}).get("pass") is False))

    def visual_spec_signature(self, project, shot):
        from studio_render_inputs import visual_spec_signature
        return visual_spec_signature(project, shot, self.store, self.renderer)

    @staticmethod
    def retain_stale_qc(shot, qc, image_path):
        shot.setdefault('qcHistory',[]).append({'qc':qc,'imagePath':image_path,'selected':False,
            'reason':'Shot changed during review; result is for the earlier image/specification.'})
        # A newer check or an explicit manual QC decision belongs to the user.
        # Otherwise the changed image/specification requires a new review.
        current_qc = shot.get('qc',{})
        newer_check = (current_qc.get('checkedImagePath') == shot.get('imagePath')
            and current_qc.get('checkedImageSHA256')
            and (current_qc.get('checkedImagePath') != image_path
                 or current_qc.get('checkedImageSHA256') != qc.get('checkedImageSHA256')
                 or current_qc.get('shotSpecSignature') != qc.get('shotSpecSignature')
                 or current_qc.get('reviewSettingsSignature') != qc.get('reviewSettingsSignature')))
        if not shot.get('manual',{}).get('qc') and not newer_check:
            shot['qc']={'status':'PENDING','pass':None,
                        'issues':['Shot changed during review. Check the current image before rendering.']}
            shot['status']='COMPLETE' if shot.get('imagePath') else 'READY_FOR_IMAGES'

    def visual_check(self, p, shot, path):
        if p["settings"].get("qcCheckLevel") == "off":
            raise ValueError("Visual checks are Off. Enable checks in Settings first.")
        self.select_director(p)
        # Provider selection may replace the adapter. Bind this check's telemetry
        # afterward so a fresh director cannot lose it or inherit another shot.
        timing_details = {'detail': True, 'shot': shot.get('id')}
        review_chapter=shot_chapter(p,shot)
        if review_chapter is not None:
            timing_details['chapter'] = review_chapter['number']
        else:
            timing_details['intro'] = True
        def record_review_timing(stage, seconds, details):
            owned_details = {key: value for key, value in details.items()
                             if key not in ('chapter', 'intro', 'shot')}
            self.record_timing(p['id'], 'Visual QC', seconds, owned_details | timing_details,
                               profile_override=profile_for('Visual QC',p,self.config,{'_shotSpec':shot}))
        self.director.timing_callback = record_review_timing
        self.director.reasoning = p["settings"]["director"].get("qcReasoning") or (
            "High" if p["settings"]["generationMode"] == "MAX QUALITY" else "Fast"
        )
        references, reference_metadata = select_references(
            p, shot, self.store, self.provider(shot["imageProvider"])
        )
        vision_limit = getattr(self.director, 'max_vision_images', 3)
        main_ids = {person['id'] for person in shot['characters'] if person.get('type') == 'main'}
        reference_pairs = list(zip(references, reference_metadata))
        required_pairs=[];extra_identity_pairs=[];seen_main=set()
        for pair in reference_pairs:
            identity=pair[1].get('characterId')
            if identity not in main_ids:continue
            if identity in seen_main:extra_identity_pairs.append(pair)
            else:required_pairs.append(pair);seen_main.add(identity)
        required_references = [image for image, _ in required_pairs]
        if len(required_references) + 1 > vision_limit:
            raise ValueError(f'Visual review needs the generated image and {len(required_references)} main-character '
                             f'references, but this director accepts {vision_limit} images per check. '
                             'Use a compatible review workflow; no identity references were silently dropped.')
        optional_pairs = [(image, meta) for image, meta in reference_pairs if meta.get('characterId') not in main_ids]
        review_pairs = required_pairs + (extra_identity_pairs+optional_pairs)[:vision_limit - 1 - len(required_pairs)]
        review_references = [image for image, _ in review_pairs]
        character_names = {person['id']: person['name'] for person in p.get('characters', [])}
        location_names = {place['id']: place.get('name', '') for place in p.get('locations', [])}
        reference_roles = [{'imageIndex': 0, 'role': 'generated-shot'}]
        for index, (_, meta) in enumerate(review_pairs, 1):
            role = 'character-identity' if meta.get('characterId') else 'location' if meta.get('locationId') else 'style-or-composition'
            name = character_names.get(meta.get('characterId')) or location_names.get(meta.get('locationId')) or ''
            reference_roles.append({'imageIndex': index, 'role': role, 'name': name})
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
        expected['characters'] = [copy.deepcopy(c) | {'appearanceState': visible_appearance(shot, c)}
                                  for c in shot['characters']]
        expected['continuity'] = visual_environment(shot)
        chapter = review_chapter
        scene = next((scene for scene in chapter.get('scenes',[]) if scene['id']==shot.get('sceneId')),None) if chapter else None
        scene_ids = {c['id'] for c in (scene.get('characters',[]) if scene else shot['characters'])}
        expected['scenePeople'] = [{'id':c['id'],'name':c['name'],'type':c['type'],'description':c.get('description','')}
                                  for c in (chapter.get('people',[]) if chapter else []) if c['id'] in scene_ids]
        expected['framingPolicy'] = ('Shot characters are the intended visible focal cast. Source-grounded scene people may appear in the background when consistent with narration; do not flag them as invented solely because they are not focal characters. Do not require every scene person in a close-up. Wrong identity, reversed actions and severe duplicated bodies remain major errors.')
        expected['narrationSegment'] = shot.get('narrationSegment', '')
        expected['inventoryContext'] = {c['id']: {k:v for k,v in c.get('appearanceState', {}).items()
            if k in ('gun','hammer','knife','sword','bomb')} for c in shot['characters']}
        expected['propVisibilityPolicy'] = ('Inventory context preserves possessions, not visible framing requirements. '
            'Only the current action, narration, pose and composition require an object to appear. '
            'A close-up may omit carried objects. Do not demand every inventory item in every frame or add an unrelated weapon to an action.')
        expected['visualStyle'] = p['settings']['style']
        expected['visualConstraints'] = p['settings'].get('imageVisualConstraints', p['settings'].get('visualConstraints', ''))
        qc_started = time.monotonic()
        result = self.director.inspectGeneratedImage(
            {
                "expectedShot": expected,
                "strictness": p["settings"]["continuityStrictness"],
                "qualityPolicy": qc_policy(p["settings"]),
                "intentionalChanges": shot["intentionalAppearanceChanges"],
                "imageOrder": "Generated shot first. Use referenceRoles to identify each later image: location/style references do not define character identity. Compare clothing to current state, not reference clothing.",
                "referenceRoles": reference_roles,
                "_images": [data_url(path), *review_references],
            },
            self.gate,
        )
        result["seconds"] = round(time.monotonic() - qc_started, 3)
        result["provider"] = p["settings"]["director"].get("provider", "local-qwen")
        result["reasoning"] = self.director.reasoning
        result["referenceRoles"] = reference_roles
        result["reviewSettingsSignature"] = review_settings_signature(p["settings"])
        self.director.stop()
        return result

    def inspect_shot(self, p, chid, sid):
        if p["settings"].get("qcCheckLevel") == "off":
            raise ValueError("Visual checks are Off. Enable checks in Settings first.")
        shot = get_shot(p, chid, sid)
        if not shot.get("imagePath"):
            raise ValueError("Generate the image before visual review.")
        checked_path = shot['imagePath']
        checked_spec = self.visual_spec_signature(p,shot)
        checked_digest = hashlib.sha256(self.store.asset(p['id'],checked_path).read_bytes()).hexdigest()
        self.unload_models()
        self.providers["existing"].unload()
        self.status(p["id"], chid, "QC")
        self.director.timing_callback = lambda stage, seconds, details: self.record_timing(
            p['id'], stage, seconds, details | {'chapter': get_chapter(p, chid)['number'], 'shot': sid, 'detail': True})
        qc = self.visual_check(p, shot, self.store.asset(p["id"], shot["imagePath"]))
        qc["status"] = (
            "PASSED"
            if qc.get("pass") is True
            else "REVIEW_REQUIRED" if qc.get("action") == "review"
            else "FAILED" if qc.get("pass") is False else "UNREVIEWED"
        )
        qc.update(checkedImagePath=checked_path,checkedImageSHA256=checked_digest,shotSpecSignature=checked_spec)
        def apply(q):
            current = get_shot(q,chid,sid)
            current_path = self.store.asset(p['id'],current.get('imagePath','')) if current.get('imagePath') else None
            current_digest = hashlib.sha256(current_path.read_bytes()).hexdigest() if current_path and current_path.is_file() else None
            if (current.get('imagePath') != checked_path or current_digest != checked_digest
                    or self.visual_spec_signature(q,current) != checked_spec
                    or qc.get("reviewSettingsSignature",review_settings_signature(q["settings"]))!=review_settings_signature(q["settings"])):
                self.retain_stale_qc(current,qc,checked_path)
                # The caller may have already chosen a new image and its review.
                # Preserve that current state; never certify it with this result.
                return False
            current.update(
                qc=qc,
            )
            current["qcDecision"]=qc_decision(q,current,self.store)
            current["status"]=production_status(q,current,self.store)
            return True
        return {'selected':self.store.mutate(p['id'],apply),'qc':qc}

    def close(self):
        with self.cv:
            self.closed = True
            self.cv.notify_all()
        self.unload_models()
        self.storage.close()
        self.thread.join(5)
        if not self.thread.is_alive():
            self.db.close()
            self.trace.close()
            self.performance.close()
            if hasattr(self,'execution_journal'):
                self.execution_journal.close()
