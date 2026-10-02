"""Exercise unattended orchestration with deterministic stage adapters."""

import copy
import tempfile
import threading
import unittest
from pathlib import Path
from studio_data import (
    new_project,
    new_chapter,
    character,
    uid,
    digest,
    get_chapter,
    get_shot,
    state_before,
)
from studio_service import StudioService, JobCancelled


class Provider:
    id = "existing"

    def unload(self):
        pass

    def getCapabilities(self):
        return {"maxReferenceImages": 1}

    def validateSettings(self, settings):
        return settings | {"seed": settings.get("seed") or 123}


class Renderer:
    def __init__(self, store):
        self.store = store
        self.full_calls = 0

    def chapter(self, p, ch, gate):
        name = ch["id"] + ".mp4"
        self.store.asset(p["id"], name).write_bytes(b"chapter video")
        return {"path": name, "duration": 4, "signature": ch["id"]}

    def full(self, p, gate):
        assert all(c.get("render", {}).get("path") for c in p["chapters"])
        self.full_calls += 1
        self.store.asset(p["id"], "story.mp4").write_bytes(b"full video")
        return {"path": "story.mp4", "duration": 8, "signature": "full"}


class Harness(StudioService):
    def worker(self):
        pass

    def unload_models(self):
        pass

    def narration(self, p, ch):
        signature = digest(ch["sourceText"])
        if ch.get("audio", {}).get("signature") == signature:
            return
        self.calls.append(("audio", ch["number"]))
        self.store.mutate(
            p["id"],
            lambda q: get_chapter(q, ch["id"]).update(
                audio={"signature": signature, "path": "audio.wav", "duration": 4},
                cleanNarrationText=ch["sourceText"],
            ),
        )

    def analyze(self, p, chid, options):
        assert options["managedPipeline"]
        ch = get_chapter(p, chid)
        self.calls.append(("analysis", ch["number"]))
        if self.proposal:
            self.store.mutate(
                p["id"],
                lambda q: get_chapter(q, chid).update(proposedPlan={"scenes": []}),
            )
            return
        main = (
            copy.deepcopy(p["characters"][0])
            if p["characters"]
            else character("Mira", kind="main")
        )
        teacher = character("Teacher", kind="supporting")
        scene = {
            "id": uid("scene-"),
            "chapterId": chid,
            "characters": [{"id": main["id"], "type": "main"}],
            "appearanceChanges": [],
            "shots": [],
        }
        shot = {
            "id": uid("shot-"),
            "chapterId": chid,
            "sceneId": scene["id"],
            "start": 0,
            "end": 4,
            "characters": [{"id": main["id"], "type": "main", "appearanceState": {}}],
            "generationSettings": p["settings"]["image"] | {"seed": 123},
            "camera": {},
            "imageProvider": "existing",
            "imageModel": "sd15",
            "workflow": "text-to-image",
            "prompt": "Mira carries a key",
            "negativePrompt": "",
            "status": "READY_FOR_IMAGES",
            "imagePath": "",
            "manual": {},
        }
        scene["shots"] = [shot]
        state, _ = state_before(p, chid)
        self.store.mutate(
            p["id"],
            lambda q: get_chapter(q, chid).update(
                people=[main, teacher],
                scenes=[scene],
                inputState=state,
                handoff={
                    "sourceSignature": digest(ch["sourceText"]),
                    "state": state,
                    "memory": {},
                },
            ),
        )

    def character_reference(self, p, options):
        self.calls.append(("reference", options["characterId"]))
        path = "face.png"
        self.store.asset(p["id"], path).write_bytes(b"reference")
        self.store.mutate(
            p["id"],
            lambda q: next(
                c for c in q["characters"] if c["id"] == options["characterId"]
            )["references"].append({"path": path, "kind": "face"}),
        )

    def generate(self, p, chid, sid, seed, options):
        chapter = get_chapter(p, chid)
        self.calls.append(("image", chapter["number"], seed))
        if self.fail_chapter == chapter["number"]:
            raise ValueError("image backend unavailable")
        path = sid + ".png"
        self.store.asset(p["id"], path).write_bytes(b"image")
        self.store.mutate(
            p["id"],
            lambda q: get_shot(q, chid, sid).update(imagePath=path, status="COMPLETE"),
        )
        if self.cancel_after_image:
            self.cancel_after_image = False
            raise JobCancelled()


class FullVideoTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Harness(
            self.temp.name,
            type("Audio", (), {"gpu": "test"})(),
            lambda: None,
            threading.Lock(),
            lambda: None,
            lambda: None,
            type("Legacy", (), {"control": lambda *args: None})(),
        )
        self.service.providers = {"existing": Provider()}
        self.service.renderer = Renderer(self.service.store)
        self.service.calls = []
        self.service.fail_chapter = None
        self.service.cancel_after_image = False
        self.service.proposal = False
        self.project = new_project()
        self.project["chapters"].append(new_chapter(2))
        for chapter in self.project["chapters"]:
            chapter["sourceText"] = "Mira carries a brass key."
        self.project = self.service.store.save(self.project)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_two_chapters_finish_as_one_video_and_reuse_completed_stages(self):
        self.service.produce_story(self.project["id"], {})
        p = self.service.store.load(self.project["id"])
        self.assertEqual(p["production"]["status"], "COMPLETE")
        self.assertEqual(p["render"]["path"], "story.mp4")
        self.assertEqual(len(p["characters"]), 1)
        self.assertEqual(p["characters"][0]["name"], "Mira")
        self.assertEqual(sum(c[0] == "reference" for c in self.service.calls), 1)
        calls = self.service.calls.copy()
        self.service.produce_story(self.project["id"], {})
        self.assertEqual(self.service.calls, calls)

    def test_retry_preserves_completed_chapter_and_failed_image_seed(self):
        self.service.fail_chapter = 2
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.service.produce_story(self.project["id"], {})
        p = self.service.store.load(self.project["id"])
        first = p["chapters"][0]["scenes"][0]["shots"][0]["imagePath"]
        self.assertTrue(self.service.store.asset(p["id"], first).exists())
        self.assertFalse(p.get("render"))
        self.service.fail_chapter = None
        self.service.produce_story(p["id"], {})
        self.assertEqual(sum(c[:2] == ("image", 1) for c in self.service.calls), 1)
        self.assertEqual(
            [c[2] for c in self.service.calls if c[:2] == ("image", 2)], [123, 123]
        )

    def test_cancel_after_saved_image_can_restart_without_regenerating_it(self):
        self.service.cancel_after_image = True
        with self.assertRaises(JobCancelled):
            self.service.produce_story(self.project["id"], {})
        self.service.produce_story(self.project["id"], {})
        self.assertEqual(sum(c[:2] == ("image", 1) for c in self.service.calls), 1)

    def test_manual_proposed_plan_stops_automatic_run_without_render(self):
        self.service.proposal = True
        with self.assertRaisesRegex(ValueError, "manual edits were preserved"):
            self.service.produce_story(self.project["id"], {})
        self.assertEqual(self.service.renderer.full_calls, 0)

    def test_run_deduplicates_and_blocks_competing_jobs(self):
        self.service.control("pause")
        q = self.service.enqueue(self.project["id"], None, "produce-story")
        self.assertEqual(q["counts"]["QUEUED"], 1)
        self.assertEqual(
            self.service.enqueue(self.project["id"], None, "produce-story")["counts"][
                "QUEUED"
            ],
            1,
        )
        with self.assertRaisesRegex(ValueError, "full-video run"):
            self.service.enqueue(
                self.project["id"], self.project["chapters"][0]["id"], "analyze"
            )


if __name__ == "__main__":
    unittest.main()
