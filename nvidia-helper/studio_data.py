"""Versioned project facts and atomic persistence, independent of AI providers."""

import copy, hashlib, json, math, re, threading, time, uuid
from pathlib import Path

SCHEMA_VERSION = 1
STATES = (
    "NOT_ANALYZED",
    "ANALYZING",
    "NARRATION_PREPARING",
    "AUDIO_GENERATING",
    "DIRECTING",
    "IMAGE_PROVIDER_CHECK",
    "READY_FOR_IMAGES",
    "QUEUED",
    "GENERATING",
    "QC",
    "REPAIRING",
    "PASSED",
    "FAILED",
    "PAUSED",
    "CANCELLED",
    "COMPLETE",
)


def uid(prefix=""):
    return prefix + uuid.uuid4().hex[:16]


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def analysis_location(project, detected, pending):
    name = " ".join(detected["name"].strip().split())
    canonical = name.casefold()
    existing = next(
        (
            item
            for item in project["locations"] + pending
            if " ".join(item["name"].strip().split()).casefold() == canonical
        ),
        None,
    )
    if existing:
        return existing
    location = {
        "id": "loc-" + digest({"project": project["id"], "name": canonical})[:16],
        "name": name,
        "description": detected["description"],
        "references": [],
    }
    pending.append(location)
    return location


def clean_narration(text, title="", include_title=False):
    lines = []
    for line in str(text).splitlines():
        stripped = line.strip().strip("#* ").strip()
        # Quoted dialogue never matches an anchored heading.
        if not include_title and (
            stripped.casefold() == title.strip().casefold()
            and title.strip()
            or re.fullmatch(
                r"(?:chapter|part|book)\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten)(?:\s*[:—–-]\s*[^\n]+)?",
                stripped,
                re.I,
            )
        ):
            continue
        if re.match(
            r"^(?:(?:scene|shot)\s+\d+\s*[:—–-]?|(?:director|production|camera|image prompt|negative prompt|status|metadata)\s*:)",
            stripped,
            re.I,
        ):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def sentences(text):
    # Preserve paragraphs and dialogue; punctuation boundaries belong to real narration.
    return [
        x.strip()
        for x in re.split(r'(?<=[.!?])\s+(?=[“"A-Z0-9])|\n+', text)
        if x.strip()
    ]


def text_groups(text, max_chars=8500):
    """Bound model input while retaining every character and full words."""
    offset = 0
    while offset < len(text):
        end = min(len(text), offset + max_chars)
        if end < len(text):
            boundary = max(text.rfind("\n", offset, end), text.rfind(" ", offset, end))
            if boundary > offset + max_chars // 2:
                end = boundary + 1
        yield text[offset:end]
        offset = end


def unidentified_gunshot(text):
    return bool(re.search(r"\bgunshots?\b.+\bheard\b", text, re.I)) and not re.search(
        r"\b(?:man|woman|boy|girl|he|she|they|people|crowd|fired|firing|shoots|shooting|aimed)\b",
        text,
        re.I,
    )


def first_verified_appearances(project, chapter):
    """New unnamed protagonists cannot inherit an earlier unidentified victim.

    Known recurring people remain available from the start of later chapters.
    An explicit name/alias may establish identity before an appearance quote.
    """

    def normalize(text):
        return re.sub(r"[^\w]+", " ", text.casefold()).strip()

    earlier = project["chapters"][: project["chapters"].index(chapter)]
    known = {
        p["id"] for ch in earlier for p in ch.get("people", []) if p["type"] == "main"
    }
    records = chapter.get("audio", {}).get("sentences", [])
    result = {}
    generic = {
        "man",
        "woman",
        "person",
        "boy",
        "girl",
        "unknown",
        "unnamed man",
        "unnamed woman",
    }
    for person in chapter["people"]:
        if (
            person["type"] != "main"
            or person["id"] in known
            or not person.get("evidence")
        ):
            continue
        quote = normalize(person["evidence"])
        prefix = " ".join(quote.split()[:8])
        aliases = [normalize(x) for x in [person["name"]] + person.get("aliases", [])]
        aliases = [x for x in aliases if len(x) >= 4 and x not in generic]
        matches = [
            i
            for i, record in enumerate(records)
            if (len(prefix) >= 25 and prefix in normalize(record["text"]))
            or any(
                re.search(r"\b" + re.escape(alias) + r"\b", normalize(record["text"]))
                for alias in aliases
            )
        ]
        if matches:
            result[person["id"]] = min(matches)
    return result


def new_chapter(number):
    return {
        "id": uid("ch-"),
        "number": number,
        "name": f"Chapter {number}",
        "sourceText": "",
        "cleanNarrationText": "",
        "narrationMode": "automatic",
        "includeChapterLabel": False,
        "status": "NOT_ANALYZED",
        "audio": {},
        "people": [],
        "scenes": [],
        "timeline": [],
        "analysis": {},
        "handoff": {},
        "manual": {},
        "history": [],
        "render": {},
        "errors": [],
    }


def new_project(name="My story"):
    return {
        "schemaVersion": SCHEMA_VERSION,
        "id": uid("pr-"),
        "name": name,
        "revision": 0,
        "created": time.time(),
        "updated": time.time(),
        "settings": {
            "style": "cinematic illustration",
            "layoutMode": "AUTO",
            "generationMode": "BALANCED",
            "autoContinue": False,
            "voice": "am_michael",
            "speed": 1.0,
            "director": {
                "provider": "local-qwen",
                "model": "Qwen3.5-4B Q4_K_M",
                "reasoning": "Balanced",
                "vision": False,
            },
            "image": {
                "provider": "existing",
                "model": "sd15",
                "workflow": "text-to-image",
                "preset": "Balanced Quality",
                "width": 512,
                "height": 512,
                "steps": 20,
                "sampler": "DPM++ 2M",
                "scheduler": "karras",
                "guidance": 7,
                "referenceStrength": 0.65,
                "loras": [],
                "controlnets": [],
                "ipAdapter": {},
                "fallbackEnabled": False,
                "fallback": {"provider": "existing", "model": "sd15"},
            },
            "continuityStrictness": "Medium",
            "appearanceHandling": "Automatic",
            "maxImageRetries": 2,
            "visionQC": False,
            "automaticRepair": True,
            "customLayout": {
                "sceneCount": None,
                "minDuration": 3,
                "maxDuration": 30,
                "imagesPerMinute": 8,
                "minShots": 1,
                "maxShots": 4,
                "pacing": "balanced",
                "cameraVariety": "medium",
                "closeUpFrequency": "medium",
                "establishingFrequency": "low",
                "transition": "cut",
                "movement": "subtle",
                "promptDetail": "normal",
            },
            "video": {
                "width": 1280,
                "height": 720,
                "fps": 24,
                "crf": 21,
                "imageFit": "cover",
                "motionMode": "gentle",
            },
        },
        "intro": {
            "enabled": False,
            "duration": 15,
            "placement": "full_story_only",
            "title": name,
            "subtitle": "",
            "voiceText": "",
            "visualPath": "",
            "audioPath": "",
            "motion": "slow zoom in",
        },
        "characters": [],
        "locations": [],
        "styleReferences": [],
        "continuity": {},
        "storyMemory": {},
        "chapters": [new_chapter(1)],
        "assets": [],
        "render": {},
        "warnings": [],
    }


def character(name, description="", kind="main"):
    return {
        "id": uid("char-"),
        "name": name,
        "type": kind,
        "description": description,
        "gender": "",
        "approximateAge": "",
        "permanentIdentity": {
            "face": "",
            "naturalHair": "",
            "eyes": "",
            "skin": "",
            "build": "",
            "height": "",
            "distinguishingMarks": "",
            "identityAccessories": "",
        },
        "defaultAppearance": {"outfit": "", "hairStyle": "", "accessories": ""},
        "currentAppearance": {},
        "appearanceHistory": [],
        "clothingHistory": [],
        "references": [],
        "relationships": [],
        "personality": "",
        "accepted": True,
    }


def validate_project(p):
    if not isinstance(p, dict) or not re.fullmatch(
        r"pr-[a-f0-9]{16}", str(p.get("id", ""))
    ):
        raise ValueError("Invalid project ID.")
    if p.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError(
            "Unsupported project version. Export a backup before upgrading."
        )
    if not isinstance(p.get("chapters"), list) or len(p["chapters"]) > 1000:
        raise ValueError("Use up to 1000 chapters.")
    if not isinstance(p.get("settings"), dict) or not isinstance(p.get("intro"), dict):
        raise ValueError("Project settings and intro configuration are required.")
    if (
        not isinstance(p["intro"].get("duration"), (int, float))
        or not math.isfinite(p["intro"]["duration"])
        or not 10 <= p["intro"]["duration"] <= 20
    ):
        raise ValueError("Intro duration must be 10–20 seconds.")
    if p["intro"]["placement"] not in ("full_story_only", "every_chapter"):
        raise ValueError("Invalid intro placement.")
    if not 0 <= int(p["settings"].get("maxImageRetries", 2)) <= 10:
        raise ValueError("Use 0–10 image retries.")
    seen = set()
    cast_types = ("main", "supporting", "temporary", "background", "group")

    def validate_people(people):
        if not isinstance(people, list):
            raise ValueError("Characters must be an array.")
        ids = set()
        for person in people:
            if (
                not isinstance(person, dict)
                or not person.get("id")
                or person["id"] in ids
                or person.get("type") not in cast_types
            ):
                raise ValueError(
                    "Character IDs must be unique and classifications valid."
                )
            ids.add(person["id"])
            if not isinstance(person.get("permanentIdentity"), dict) or not isinstance(
                person.get("defaultAppearance"), dict
            ):
                raise ValueError(
                    "Keep permanent identity and appearance as separate objects."
                )
            if not isinstance(person.get("references"), list):
                raise ValueError("Character references must be an array.")
        return ids

    main_ids = validate_people(p.get("characters", []))
    scene_ids = set()
    shot_ids = set()
    for ch in p["chapters"]:
        if not isinstance(ch, dict) or not re.fullmatch(
            r"ch-[a-f0-9]{16}", str(ch.get("id", ""))
        ):
            raise ValueError("Invalid chapter ID.")
        if ch["id"] in seen:
            raise ValueError("Duplicate chapter ID.")
        seen.add(ch["id"])
        if len(ch.get("sourceText", "")) > 2000000:
            raise ValueError("Chapter exceeds 2 million characters.")
        if ch.get("status") not in STATES:
            raise ValueError("Invalid chapter status.")
        if not isinstance(ch.get("sourceText", ""), str) or not isinstance(
            ch.get("cleanNarrationText", ""), str
        ):
            raise ValueError("Chapter narration must be text.")
        chapter_cast = main_ids | validate_people(ch.get("people", []))
        if not isinstance(ch.get("scenes", []), list):
            raise ValueError("Scenes must be an array.")
        for scene in ch.get("scenes", []):
            if (
                not isinstance(scene, dict)
                or not scene.get("id")
                or scene["id"] in scene_ids
            ):
                raise ValueError("Scene IDs must be unique.")
            scene_ids.add(scene["id"])
            for shot in scene.get("shots", []):
                if (
                    not isinstance(shot, dict)
                    or not shot.get("id")
                    or shot["id"] in shot_ids
                ):
                    raise ValueError("Shot IDs must be unique.")
                shot_ids.add(shot["id"])
                for n in (shot.get("start", 0), shot.get("end", 0)):
                    if not isinstance(n, (float, int)) or not math.isfinite(n) or n < 0:
                        raise ValueError("Invalid shot time.")
                if shot.get("end", 0) <= shot.get("start", 0):
                    raise ValueError("A shot must have positive duration.")
                if (
                    shot.get("sceneId") != scene["id"]
                    or shot.get("chapterId") != ch["id"]
                ):
                    raise ValueError(
                        "Shot chapter/scene references do not match their parent."
                    )
                if shot.get("status") not in STATES:
                    raise ValueError("Invalid shot status.")
                if not isinstance(shot.get("characters"), list) or any(
                    c.get("id") not in chapter_cast
                    or c.get("type") not in cast_types
                    or not isinstance(c.get("appearanceState"), dict)
                    for c in shot["characters"]
                ):
                    raise ValueError(
                        "Every shot character must refer to a known person and appearance state."
                    )
                if not isinstance(
                    shot.get("generationSettings"), dict
                ) or not isinstance(shot.get("camera"), dict):
                    raise ValueError(
                        "Shot settings and camera must be structured objects."
                    )
                for field in (
                    "prompt",
                    "negativePrompt",
                    "imageProvider",
                    "imageModel",
                    "workflow",
                ):
                    if not isinstance(shot.get(field, ""), str):
                        raise ValueError("Shot " + field + " must be text.")
    return p


class ProjectStore:
    def __init__(self, root):
        self.root = (Path(root) / "studio").resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def folder(self, id):
        if not re.fullmatch(r"pr-[a-f0-9]{16}", str(id)):
            raise ValueError("Invalid project ID.")
        return self.root / id

    def load(self, id):
        with self.lock:
            return json.loads(
                (self.folder(id) / "project.json").read_text(encoding="utf-8")
            )

    def save(self, p, expected=None):
        validate_project(p)
        with self.lock:
            folder = self.folder(p["id"])
            folder.mkdir(exist_ok=True)
            path = folder / "project.json"
            if path.exists():
                old = json.loads(path.read_text(encoding="utf-8"))
                if expected is not None and old["revision"] != expected:
                    raise ValueError(
                        "Project changed in another operation. Reload before saving."
                    )
                p["revision"] = old["revision"] + 1
                backup = folder / "project.previous.json"
                backup.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
            p["updated"] = time.time()
            temp = folder / "project.tmp"
            temp.write_text(
                json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temp.replace(path)
            metadata = folder / "revision.tmp"
            metadata.write_text(
                json.dumps({"revision": p["revision"], "updated": p["updated"]}),
                encoding="utf-8",
            )
            metadata.replace(folder / "revision.json")
            return copy.deepcopy(p)

    def revision(self, id):
        with self.lock:
            path = self.folder(id) / "revision.json"
            return (
                json.loads(path.read_text(encoding="utf-8"))
                if path.exists()
                else {"revision": self.load(id)["revision"]}
            )

    def mutate(self, id, fn):
        with self.lock:
            p = self.load(id)
            result = fn(p)
            self.save(p)
            return result if result is not None else p

    def list(self):
        result = []
        with self.lock:
            for f in self.root.glob("pr-*/project.json"):
                p = json.loads(f.read_text(encoding="utf-8"))
                result.append(
                    {k: p[k] for k in ("id", "name", "revision", "updated")}
                    | {"chapters": len(p["chapters"])}
                )
        return sorted(result, key=lambda p: p["updated"], reverse=True)

    def asset(self, id, name):
        folder = self.folder(id).resolve()
        path = (folder / name).resolve()
        if not path.is_relative_to(folder) or path.suffix.lower() not in (
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
            ".wav",
            ".mp4",
            ".json",
        ):
            raise ValueError("Invalid asset path.")
        if any(x.startswith(".") for x in Path(name).parts):
            raise ValueError("Invalid asset path.")
        return path


def get_chapter(p, id):
    return next(c for c in p["chapters"] if c["id"] == id)


def get_shot(p, chid, shotid):
    return next(
        s
        for scene in get_chapter(p, chid)["scenes"]
        for s in scene["shots"]
        if s["id"] == shotid
    )


def state_before(p, chid):
    state = {}
    memory = {}
    for ch in p["chapters"]:
        if ch["id"] == chid:
            break
        if ch.get("handoff"):
            state = copy.deepcopy(ch["handoff"].get("state", state))
            memory = copy.deepcopy(ch["handoff"].get("memory", memory))
    for person in p["characters"]:
        state.setdefault("characters", {}).setdefault(
            person["id"], copy.deepcopy(person.get("defaultAppearance", {}))
        )
    return state, memory


def supported_appearance_change(event):
    """A quoted sentence alone does not prove the proposed appearance change."""
    if event.get("origin") == "MANUAL":
        return True
    if event.get("type") == "injury":
        reason = event.get("reason", "")
        injury = str(event.get("to", {}).get("injury", ""))
        parts = r"\b(?:heart|head|face|cheek|eye|eyebrow|nose|mouth|neck|chest|shoulder|arm|hand|wrist|finger|leg|knee|foot|ankle|back|stomach)\b"
        if set(re.findall(parts, injury.casefold())) - set(
            re.findall(parts, reason.casefold())
        ):
            return False
        if (
            re.search(r"\bright\b", injury, re.I)
            and re.search(r"\bleft\b", reason, re.I)
            and not re.search(r"\bright\b", reason, re.I)
        ):
            return False
        if (
            re.search(r"\bleft\b", injury, re.I)
            and re.search(r"\bright\b", reason, re.I)
            and not re.search(r"\bleft\b", reason, re.I)
        ):
            return False
        return bool(
            re.search(
                r"\b(?:injur\w*|wound\w*|cut|cuts|bruis\w*|bleed\w*|blood\w*|stab\w*|burn\w*|hurt|scar\w*|shot|hit|damage\w*|pain\w*|punch\w*|crush\w*|gash\w*)\b",
                event.get("reason", ""),
                re.I,
            )
        )
    return True


def apply_changes(state, changes, chapter, scene):
    result = copy.deepcopy(state)
    for event in changes:
        if (
            not isinstance(event, dict)
            or not event.get("reason")
            or not event.get("characterId")
            or not isinstance(event.get("to"), dict)
            or not supported_appearance_change(event)
        ):
            continue
        current = result.setdefault("characters", {}).setdefault(
            event["characterId"], {}
        )
        current.update(event["to"])
        result.setdefault("appearanceHistory", []).append(
            event | {"chapter": chapter, "scene": scene}
        )
    return result


def normalize_object_change(event, objects, people=()):
    """Canonicalize quoted object facts; never infer an unmentioned position."""
    result = copy.deepcopy(event)
    field = result["field"]
    value = result["value"]
    reason = result["reason"]
    candidates = [
        label
        for label in objects
        if label.casefold() in value.casefold() or label.casefold() in reason.casefold()
    ]
    if candidates and (
        field in ("accessories", "right hand", "left hand")
        or any(field == label.split()[-1].lower() for label in candidates)
    ):
        # The handled object precedes its destination: in "placed the key on
        # the desk", key is the object and desk is its position.
        verb = re.search(
            r"\b(handed|gave|received|picked up|carried|put|placed|dropped|held|holding|kept|keeping|took|left|removed)\b",
            reason,
            re.I,
        )
        following = reason[verb.end() :] if verb else reason
        positions = [
            (following.casefold().find(x.casefold()), x)
            for x in candidates
            if x.casefold() in following.casefold()
        ]
        label = (
            min(positions, key=lambda x: x[0])[1]
            if positions
            else next(
                (x for x in candidates if x.casefold() in value.casefold()),
                candidates[0],
            )
        )
        result["field"] = re.sub(r"[^a-z0-9_]", "", label.lower().split()[-1])
        hand = re.search(
            r"\b(?:in|into|with)\s+(?:his|her|their|the|a)\s+(right|left)\s+hand\b",
            reason,
            re.I,
        )
        if hand:
            result["value"] = hand[1].lower() + " hand"
        elif value.casefold() == label.casefold() and re.search(
            r"\b(handed|received|picked up|carried)\b", reason, re.I
        ):
            result["value"] = "held"
        person = next((p for p in people if p["id"] == result["characterId"]), None)
        if person and re.match(
            re.escape(person["name"]) + r"\s+(?:handed|gave)\b", reason, re.I
        ):
            result["value"] = "removed"
    return result


def grounded_object_changes(events, objects, people):
    # Mentioning a crate during an injury does not mean the character possesses it.
    verbs = r"\b(picked up|handed|gave|received|carried|put|placed|dropped|held|holding|kept|keeping|took|takes|left|sets|set down|removed)\b"
    names = [
        re.sub(r"[^a-z0-9_]", "", label.lower().split()[-1])
        for label in objects
        if label.strip()
    ]
    return [
        normalize_object_change(e, objects, people)
        for e in events
        if re.search(verbs, e.get("reason", ""), re.I)
        and any(
            re.search(r"\b" + re.escape(name) + r"\b", e["reason"], re.I)
            for name in names
        )
    ]


def align_evidence(events, sentence_records):
    """Correct an AI's sentence index only when its quote has one exact source."""
    for event in events:
        matches = [
            i
            for i, s in enumerate(sentence_records)
            if event.get("reason")
            and event["reason"].casefold() in s["text"].casefold()
        ]
        if len(matches) == 1:
            event["sentence"] = matches[0]
    return events


def warn_dependents(p, ch, previous):
    current = ch.get("handoff", {})

    def facts(handoff):
        state = {
            k: v
            for k, v in handoff.get("state", {}).items()
            if k != "appearanceHistory"
        }
        return {
            "state": state,
            "sourceSignature": handoff.get("sourceSignature"),
            "memory": {
                k: v
                for k, v in handoff.get("memory", {}).items()
                if k not in ("majorEvents",)
            },
        }

    if not previous or digest(facts(previous)) == digest(facts(current)):
        return
    following = p["chapters"][p["chapters"].index(ch) + 1 :]
    if following:
        fields = set()
        old = previous.get("state", {}).get("characters", {})
        new = current.get("state", {}).get("characters", {})
        for cid in set(old) | set(new):
            fields.update(
                k
                for k in set(old.get(cid, {})) | set(new.get(cid, {}))
                if old.get(cid, {}).get(k) != new.get(cid, {}).get(k)
            )
        categories = []
        if fields & {"outfit", "jacketDamage"}:
            categories.append("clothing")
        if fields & {
            "hairStyle",
            "makeup",
            "wetness",
            "dirt",
            "disguise",
            "transformation",
        }:
            categories.append("appearance")
        if fields & {"injury"}:
            categories.append("injuries")
        if fields - {
            "outfit",
            "jacketDamage",
            "hairStyle",
            "makeup",
            "wetness",
            "dirt",
            "disguise",
            "transformation",
            "injury",
        }:
            categories.append("objects/accessories")
        if previous.get("state", {}).get("environment") != current.get("state", {}).get(
            "environment"
        ):
            categories.append("locations/time/weather")
        if previous.get("sourceSignature") != current.get(
            "sourceSignature"
        ) or previous.get("memory") != current.get("memory"):
            categories.append("plot facts/relationships")
        existing = next(
            (
                w
                for w in p["warnings"]
                if w["chapterId"] == ch["id"] and not w.get("resolved")
            ),
            None,
        )
        warning = {
            "chapterId": ch["id"],
            "affected": [c["id"] for c in following],
            "categories": categories or ["story state"],
            "message": ch["name"] + " changed story state used by later chapters.",
            "resolved": False,
        }
        if existing:
            existing.update(warning)
        else:
            p["warnings"].append({"id": uid()} | warning)
