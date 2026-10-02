import copy, json, tempfile, threading, time, unittest
from pathlib import Path
from studio_data import *
from image_provider import (
    ImageProvider,
    ExistingImageProvider,
    NativeFluxProvider,
    format_prompt,
)
from director_provider import validate_schema, ANALYSIS
from studio_render import VideoRenderer


class StudioDataTest(unittest.TestCase):
    def test_clean_audio_preserves_quoted_dialogue_and_story(self):
        text = "Chapter One\n\nMichael said, “Chapter One is the title.”\nScene 7: Hallway\nDirector: use a close-up\nSarah put the key in her pocket."
        cleaned = clean_narration(text)
        self.assertNotIn("Scene 7", cleaned)
        self.assertNotIn("Director:", cleaned)
        self.assertTrue(cleaned.startswith("Michael said"))
        self.assertIn("“Chapter One is the title.”", cleaned)
        self.assertIn("Sarah put the key", cleaned)

    def test_atomic_project_survives_restart_and_cas_prevents_lost_updates(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder)
            p = store.save(new_project())
            c = p["chapters"][0]
            c["sourceText"] = "Keep this story."
            p = store.save(p, expected=0)
            other = ProjectStore(folder)
            reloaded = other.load(p["id"])
            self.assertEqual(reloaded["chapters"][0]["sourceText"], "Keep this story.")
            with self.assertRaises(ValueError):
                other.save(reloaded, expected=0)
            self.assertEqual(other.load(p["id"])["revision"], 1)

    def test_chapter_handoff_carries_object_and_allows_clothing_change(self):
        p = new_project()
        main = character("Michael")
        main["permanentIdentity"]["eyes"] = "brown"
        main["defaultAppearance"]["outfit"] = "black jacket"
        p["characters"] = [main]
        c1 = p["chapters"][0]
        c2 = new_chapter(2)
        p["chapters"].append(c2)
        state = apply_changes(
            {},
            [
                {
                    "characterId": main["id"],
                    "to": {
                        "outfit": "blue uniform",
                        "key": "right hand",
                        "injury": "right cheek",
                    },
                    "reason": "He changed clothes and kept the key.",
                }
            ],
            1,
            "scene-1",
        )
        c1["handoff"] = {"state": state, "memory": {"objects": ["brass key"]}}
        continued, memory = state_before(p, c2["id"])
        self.assertEqual(continued["characters"][main["id"]]["outfit"], "blue uniform")
        self.assertEqual(continued["characters"][main["id"]]["key"], "right hand")
        self.assertEqual(main["permanentIdentity"]["eyes"], "brown")
        self.assertEqual(memory["objects"], ["brass key"])

    def test_object_destination_and_sender_do_not_become_wrong_possessions(self):
        sarah = character("Sarah")
        michael = character("Michael")
        people = [sarah, michael]
        objects = ["brass key", "teacher's desk", "crate"]
        event = {
            "characterId": michael["id"],
            "field": "desk",
            "value": "teacher's desk",
            "reason": "Michael placed the brass key on the teacher's desk.",
            "sentence": 0,
        }
        result = grounded_object_changes([event], objects, people)[0]
        self.assertEqual(result["field"], "key")
        self.assertEqual(result["value"], "teacher's desk")
        event.update(
            characterId=sarah["id"],
            field="key",
            value="held",
            reason="Sarah handed Michael a brass key.",
        )
        self.assertEqual(
            grounded_object_changes([event], objects, people)[0]["value"], "removed"
        )
        event.update(field="crate", reason="Michael scraped his cheek on the crate.")
        self.assertEqual(grounded_object_changes([event], objects, people), [])

    def test_invalid_intro_and_timing_are_rejected(self):
        p = new_project()
        p["intro"]["duration"] = 9
        with self.assertRaises(ValueError):
            validate_project(p)
        p["intro"]["duration"] = 15
        p["chapters"][0]["scenes"] = [{"shots": [{"start": 2, "end": 1}]}]
        with self.assertRaises(ValueError):
            validate_project(p)

    def test_main_library_not_changed_by_temporary_people(self):
        p = new_project()
        p["chapters"][0]["people"] = [
            character("Teacher", kind="supporting"),
            character("Unnamed man", kind="temporary"),
        ]
        self.assertEqual(p["characters"], [])
        self.assertEqual(len(p["chapters"][0]["people"]), 2)

    def test_director_json_schema_rejects_malformed_people(self):
        with self.assertRaises(ValueError):
            validate_schema({"summary": "prose only"}, ANALYSIS)

    def test_provider_prompts_keep_current_appearance_not_default_outfit(self):
        p = new_project()
        c = character("Sarah")
        c["permanentIdentity"]["eyes"] = "green"
        c["defaultAppearance"]["outfit"] = "red coat"
        p["characters"] = [c]
        ch = p["chapters"][0]
        shot = {
            "chapterId": ch["id"],
            "characters": [
                {"id": c["id"], "appearanceState": {"outfit": "white sweater"}}
            ],
            "action": "Sarah hands Michael the brass key",
            "camera": {
                "shot": "medium",
                "angle": "eye level",
                "composition": "Sarah left",
            },
            "location": "platform",
            "expression": "worried",
            "pose": "arm extended",
            "lighting": "evening",
            "continuity": {},
        }
        prompt, negative = format_prompt(p, shot, ExistingImageProvider(lambda: None))
        self.assertIn("white sweater", prompt)
        self.assertNotIn("red coat", prompt)
        self.assertIn("green", prompt)
        self.assertTrue(negative)
        prompt, negative = format_prompt(p, shot, NativeFluxProvider({}, "."))
        self.assertFalse(negative)
        self.assertIn("white sweater", prompt)

    def test_cross_chapter_regeneration_reports_dependencies_without_destroying_assets(
        self,
    ):
        p = new_project()
        c = p["chapters"][0]
        next = new_chapter(2)
        next["audio"] = {"path": "keep.wav"}
        p["chapters"].append(next)
        c["handoff"] = {"state": {"outfit": "uniform"}}
        warn_dependents(p, c, {"state": {"outfit": "jacket"}})
        self.assertEqual(p["warnings"][0]["affected"], [next["id"]])
        self.assertEqual(next["audio"]["path"], "keep.wav")

    def test_asset_path_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder)
            p = store.save(new_project())
            with self.assertRaises(ValueError):
                store.asset(p["id"], "../../private.wav")

    def test_native_does_not_enable_wrong_sampler_or_sd_guidance(self):
        provider = NativeFluxProvider({}, ".")
        with self.assertRaises(ValueError):
            provider.validateSettings(
                {
                    "model": "flux2-klein-4b-q4",
                    "width": 512,
                    "height": 512,
                    "steps": 4,
                    "guidance": 7,
                }
            )

    def test_native_rejects_unsupported_sd_edit_strength(self):
        provider = NativeFluxProvider({}, ".")
        with self.assertRaisesRegex(ValueError, "instruction-based editing"):
            provider.validateSettings(
                {
                    "model": "flux2-klein-4b-q4",
                    "width": 384,
                    "height": 384,
                    "steps": 4,
                    "guidance": 1,
                    "sampler": "euler",
                    "scheduler": "flux2",
                    "denoisingStrength": 0.65,
                }
            )

    def test_native_masked_inpaint_does_not_silently_become_an_edit(self):
        provider = NativeFluxProvider({}, ".")
        with self.assertRaisesRegex(ValueError, "masked inpainting"):
            provider.generateImage({"operation": "inpaint"}, lambda *args: None)


class ProductionQueueTest(unittest.TestCase):
    def test_failed_render_never_replaces_a_completed_cached_asset(self):
        from unittest.mock import patch

        class FailedProcess:
            returncode = 1

            def __init__(self, args, **kwargs):
                Path(args[-1]).write_bytes(b"incomplete video")

            def poll(self):
                return self.returncode

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "intro.mp4"
            target.write_bytes(b"previous completed video")
            renderer = VideoRenderer(None, {})
            with patch.object(renderer, "executable", return_value="ffmpeg"), patch(
                "studio_render.subprocess.Popen", FailedProcess
            ):
                with self.assertRaisesRegex(RuntimeError, "FFmpeg rendering failed"):
                    renderer.run(
                        [str(target)], lambda *args: None, Path(folder) / "render.log"
                    )
            self.assertEqual(target.read_bytes(), b"previous completed video")

    def test_thousand_jobs_cancel_recover_and_retry_keep_assets_and_seeds(self):
        from studio_service import StudioService

        class IdleService(StudioService):
            def worker(self):
                return

        class Legacy:
            def control(self, *args):
                pass

        class Audio:
            gpu = "test GPU"

        with tempfile.TemporaryDirectory() as folder:

            def start():
                return IdleService(
                    folder,
                    Audio(),
                    lambda: None,
                    threading.Lock(),
                    lambda: None,
                    lambda: None,
                    Legacy(),
                )

            service = start()
            service.providers["existing"].healthCheck = lambda: {
                "installed": True,
                "models": ["sd15"],
            }
            service.control("pause")
            p = new_project()
            c = p["chapters"][0]
            scene = {
                "id": uid("scene-"),
                "characters": [],
                "appearanceChanges": [],
                "shots": [],
            }
            for n in range(1000):
                scene["shots"].append(
                    {
                        "id": uid("shot-"),
                        "chapterId": c["id"],
                        "sceneId": scene["id"],
                        "start": float(n),
                        "end": float(n + 1),
                        "characters": [],
                        "camera": {},
                        "generationSettings": p["settings"]["image"] | {"seed": n},
                        "imageProvider": "existing",
                        "imageModel": "sd15",
                        "workflow": "text-to-image",
                        "prompt": "a key",
                        "negativePrompt": "",
                        "status": "READY_FOR_IMAGES",
                        "imagePath": "",
                    }
                )
            c["scenes"] = [scene]
            p = service.store.save(p)
            asset = service.store.asset(p["id"], "saved.png")
            asset.write_bytes(b"completed asset retained")
            scene["shots"][0]["imagePath"] = "saved.png"
            service.store.save(p)
            queued = service.enqueue(
                p["id"], c["id"], "image", [s["id"] for s in scene["shots"]]
            )
            self.assertEqual(queued["counts"]["QUEUED"], 1000)
            first = queued["jobs"][-1]
            seed = first["seed"]
            cancelled = service.control("cancel-all")
            self.assertEqual(cancelled["counts"]["CANCELLED"], 1000)
            self.assertTrue(asset.exists())
            self.assertEqual(
                service.store.load(p["id"])["chapters"][0]["scenes"][0]["shots"][0][
                    "status"
                ],
                "CANCELLED",
            )
            service.control("retry", first["id"])
            self.assertEqual(service.snapshot()["counts"]["QUEUED"], 1)
            self.assertEqual(
                next(j for j in service.snapshot()["jobs"] if j["id"] == first["id"])[
                    "seed"
                ],
                seed,
            )
            # A low-priority waiting item must remain visible after a long
            # sequence of newer completed jobs has filled the history window.
            template = dict(
                service.db.execute(
                    "SELECT * FROM jobs WHERE id=?", (first["id"],)
                ).fetchone()
            )
            for index in range(1101):
                row = template | {
                    "id": uid("job-"),
                    "status": "COMPLETE",
                    "created": time.time() + index,
                    "seconds": 1,
                }
                service.db.execute(
                    "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    tuple(row.values()),
                )
            service.db.commit()
            snapshot = service.snapshot()
            self.assertIn(first["id"], {j["id"] for j in snapshot["jobs"]})
            self.assertEqual(snapshot["counts"]["COMPLETE"], 1101)
            self.assertEqual(
                snapshot["projectCounts"][p["id"]]["image"]["COMPLETE"], 1101
            )
            service.db.execute("DELETE FROM jobs WHERE status='COMPLETE'")
            service.db.execute(
                "UPDATE jobs SET status='RUNNING' WHERE id=?", (first["id"],)
            )
            service.db.commit()
            service.close()
            restarted = start()
            self.assertTrue(restarted.paused)
            self.assertEqual(restarted.snapshot()["counts"]["QUEUED"], 1)
            self.assertTrue(asset.exists())
            self.assertEqual(
                get_shot(restarted.store.load(p["id"]), c["id"], first["shot"])[
                    "status"
                ],
                "PAUSED",
            )
            restarted.close()

    def test_contradictory_visual_pass_is_rejected(self):
        from director_provider import DirectorProvider

        class Contradictory(DirectorProvider):
            vision_available = True

            def call(self, *args, **kwargs):
                return {
                    "pass": True,
                    "issues": ["watch on wrong wrist"],
                    "action": "image_edit",
                    "repairPrompt": "Fix wrist.",
                }

        result = Contradictory().inspectGeneratedImage({}, lambda *args: None)
        self.assertFalse(result["pass"])


if __name__ == "__main__":
    unittest.main()
