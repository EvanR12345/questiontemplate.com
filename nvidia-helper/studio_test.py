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
    def test_direct_job_times_survive_retries_without_double_counting(self):
        from studio_service import StudioService
        p = new_project()
        job = {'id':'job-one', 'kind':'image', 'started':100, 'seconds':3.25,
               'status':'FAILED', 'chapter':p['chapters'][0]['id'], 'shot':'shot-one'}
        StudioService.append_queue_timing(p, job)
        StudioService.append_queue_timing(p, job)
        StudioService.append_queue_timing(p, job | {'started':200, 'seconds':2, 'status':'COMPLETE'})
        StudioService.append_queue_timing(p, job | {'kind':'produce-story', 'id':'parent'})
        timings = p['production']['timings']
        self.assertEqual(len(timings), 2)
        self.assertEqual(sum(t['seconds'] for t in timings), 5.25)
        self.assertEqual([t['status'] for t in timings], ['FAILED','COMPLETE'])
        self.assertEqual(timings[0]['chapter'], 1)
        StudioService.append_queue_timing(p, job | {'id':'full', 'kind':'render-full', 'chapter':None, 'shot':None})
        self.assertEqual(timings[-1]['stage'], 'Assemble full video')
        self.assertIsNone(timings[-1]['chapter'])

    def test_dialogue_evidence_wrapper_does_not_break_sentence_alignment(self):
        sentences = [{'text': '"I filled my stomach with the bomb.'},
                     {'text': 'You think threatening me would work?"'}]
        event = {'sentence': 8, 'reason': '"I filled my stomach with the bomb."'}
        align_evidence([event], sentences)
        self.assertEqual(event['sentence'], 0)
        self.assertIn(event['reason'], sentences[0]['text'])
        invented = {'sentence': 8, 'reason': 'He picked up a sword.'}
        align_evidence([invented], sentences)
        self.assertEqual(invented['sentence'], 8)

    def test_black_hair_does_not_authorize_invented_black_skin_or_height(self):
        result = grounded_identity({'naturalHair':'black', 'skin':'black', 'height':'tall', 'face':'black beard'},
            'A black-haired man with a black beard and a scar on the left cheek.')
        self.assertEqual(result['naturalHair'], 'black')
        self.assertEqual(result['skin'], '')
        self.assertEqual(result['height'], '')
        self.assertEqual(result['face'], 'black beard')
        self.assertEqual(grounded_identity({'skin':'pale'}, 'His skin was pale.')['skin'], 'pale')

    def test_retry_preserves_location_identity_and_user_reference_assets(self):
        project = new_project()
        detected = {"name": "Prison cell", "description": "A cold cell"}
        first = analysis_location(project, detected, [])
        retried = analysis_location(project, detected, [])
        self.assertEqual(first["id"], retried["id"])
        self.assertNotEqual(
            first["id"], analysis_location(new_project(), detected, [])["id"]
        )
        first.update(id="user-location", references=[{"path": "my-cell.png"}])
        project["locations"].append(first)
        pending = []
        kept = analysis_location(
            project, {"name": " PRISON  CELL ", "description": "AI revision"}, pending
        )
        self.assertIs(kept, first)
        self.assertEqual(kept["references"], [{"path": "my-cell.png"}])
        self.assertEqual(kept["description"], "A cold cell")
        self.assertEqual(pending, [])

    def test_heard_gunshot_does_not_reveal_an_unidentified_shooters_face(self):
        self.assertTrue(unidentified_gunshot("Bang. A loud gunshot was heard."))
        self.assertTrue(unidentified_gunshot("Three more gunshots were heard."))
        self.assertFalse(
            unidentified_gunshot("Michael fired, and a gunshot was heard by the crowd.")
        )
        self.assertFalse(unidentified_gunshot("A man fired his gun."))

    def test_quoted_shooting_action_does_not_support_an_invented_heart_wound(self):
        event = {
            "characterId": "hero",
            "type": "injury",
            "to": {"injury": "heart wound"},
            "reason": "The man continued to shoot at his enemies.",
        }
        result = apply_changes(
            {"characters": {"hero": {}}, "appearanceHistory": []}, [event], 1, "scene"
        )
        self.assertEqual(result["characters"]["hero"], {})
        self.assertEqual(result["appearanceHistory"], [])
        event["reason"] = "A cut opened on his right cheek."
        event["to"]["injury"] = "cut on right cheek"
        self.assertTrue(supported_appearance_change(event))
        event["to"]["injury"] = "heart wound"
        self.assertFalse(supported_appearance_change(event))
        event.update(reason="User override", origin="MANUAL")
        self.assertTrue(supported_appearance_change(event))

    def test_unknown_opening_victim_does_not_establish_later_protagonist_identity(self):
        p = new_project()
        ch = p["chapters"][0]
        person = character("Bearded man")
        person["evidence"] = "A black-haired man with a black beard stood with a gun."
        ch["people"] = [person]
        ch["audio"]["sentences"] = [
            {"text": text}
            for text in (
                "A gunshot was heard.",
                "A man was shot in the heart.",
                "A woman screamed.",
                person["evidence"],
            )
        ]
        self.assertEqual(first_verified_appearances(p, ch), {person["id"]: 3})
        second = new_chapter(2)
        p["chapters"].append(second)
        second["people"] = [copy.deepcopy(person)]
        second["audio"]["sentences"] = [{"text": "He raised his gun."}]
        self.assertEqual(first_verified_appearances(p, second), {})

    def test_identity_repair_preserves_plan_seeds_and_legitimate_later_object_state(
        self,
    ):
        from studio_service import StudioService

        with tempfile.TemporaryDirectory() as folder:
            service = object.__new__(StudioService)
            service.store = ProjectStore(folder)
            p = new_project()
            ch = p["chapters"][0]
            person = character("Bearded man")
            person["evidence"] = (
                "A black-haired man with a black beard stood with a gun."
            )
            ch["people"] = [person]
            ch["sourceText"] = (
                "A gunshot was heard. A man was shot in the heart. A woman screamed. "
                + person["evidence"]
            )
            ch["audio"]["sentences"] = [
                {"text": text}
                for text in (
                    "A gunshot was heard.",
                    "A man was shot in the heart.",
                    "A woman screamed.",
                    person["evidence"],
                )
            ]
            ch["inputState"] = {
                "characters": {},
                "environment": {},
                "appearanceHistory": [],
            }
            scene = {"id": "scene-test", "shots": [], "appearanceChanges": []}

            def shot(index):
                return {
                    "id": "shot-" + str(index),
                    "sceneId": scene["id"],
                    "chapterId": ch["id"],
                    "start": index * 4,
                    "end": index * 4 + 4,
                    "startSentence": index,
                    "endSentence": index,
                    "narrationSegment": ch["audio"]["sentences"][index]["text"],
                    "characters": [
                        {
                            "id": person["id"],
                            "type": "main",
                            "appearanceState": {"injury": "heart wound"},
                        }
                    ],
                    "intentionalAppearanceChanges": [],
                    "continuity": {},
                    "camera": {
                        "shot": "medium",
                        "angle": "eye level",
                        "composition": "man",
                    },
                    "action": "man",
                    "expression": "calm",
                    "pose": "standing",
                    "location": "party",
                    "lighting": "dim",
                    "generationSettings": {"seed": 123},
                    "imageProvider": "fake",
                    "imageModel": "fake",
                    "workflow": "basic",
                    "prompt": "old",
                    "negativePrompt": "",
                    "status": "READY_FOR_IMAGES",
                    "manual": {},
                }

            opening = shot(1)
            opening["intentionalAppearanceChanges"] = [
                {
                    "characterId": person["id"],
                    "type": "injury",
                    "to": {"injury": "heart wound"},
                    "reason": "A man was shot in the heart.",
                }
            ]
            later = shot(3)
            later["intentionalAppearanceChanges"] = [
                {
                    "characterId": person["id"],
                    "type": "gun",
                    "to": {"gun": "held"},
                    "reason": person["evidence"],
                }
            ]
            scene["shots"] = [opening, later]
            ch["scenes"] = [scene]
            ch["handoff"] = {"state": {}}
            p = service.store.save(p)

            class Provider:
                def getCapabilities(self):
                    return {
                        "supportsNegativePrompt": False,
                        "promptFormat": "natural-language",
                    }

            class Director:
                def call(self, role, context, schema, gate):
                    temporary = next(
                        c for c in context["allowedPeople"] if c["type"] == "temporary"
                    )
                    return {
                        "shot0": {
                            "characters": [temporary["id"]],
                            "action": "An unidentified victim falls",
                            "composition": "anonymous victim",
                            "expression": "shock",
                            "pose": "falling",
                        }
                    }

            service.provider = lambda *args: Provider()
            service.director = Director()
            service.gate = lambda *args: None
            service.repair_identity_bindings(p["id"], ch["id"])
            result = service.store.load(p["id"])["chapters"][0]
            first, last = result["scenes"][0]["shots"]
            self.assertEqual(first["characters"][0]["type"], "temporary")
            self.assertNotIn("injury", last["characters"][0]["appearanceState"])
            self.assertEqual(
                result["handoff"]["state"]["characters"][person["id"]]["gun"], "held"
            )
            self.assertEqual(last["generationSettings"]["seed"], 123)
            self.assertEqual(result["scenes"][0]["id"], "scene-test")
            service.repair_identity_bindings(p["id"], ch["id"])
            self.assertEqual(
                len(service.store.load(p["id"])["chapters"][0]["people"]), 2
            )

    def test_same_sentence_pair_camera_views_get_real_sentence_boundaries(self):
        from studio_service import StudioService

        service = object.__new__(StudioService)
        shots = [
            {"startSentence": 6, "endSentence": 7, "action": "Bodies fall"},
            {"startSentence": 6, "endSentence": 7, "action": "Close-up on coats"},
        ]
        repaired = service.trim_shot_overlaps(shots)
        service.validate_shot_ranges(repaired, {"startSentence": 6, "endSentence": 7})
        self.assertEqual(
            [(s["startSentence"], s["endSentence"]) for s in repaired], [(6, 6), (7, 7)]
        )
        self.assertEqual(
            [s["action"] for s in repaired], ["Bodies fall", "Close-up on coats"]
        )

    def test_duplicate_views_of_one_sentence_keep_alternative_without_duplicated_audio(
        self,
    ):
        from studio_service import StudioService

        service = object.__new__(StudioService)
        repaired = service.trim_shot_overlaps(
            [
                {"startSentence": 0, "endSentence": 0, "action": "Wide"},
                {"startSentence": 0, "endSentence": 0, "action": "Close-up"},
            ]
        )
        service.validate_shot_ranges(repaired, {"startSentence": 0, "endSentence": 0})
        self.assertEqual(len(repaired), 1)
        self.assertEqual(repaired[0]["alternateDirections"][0]["action"], "Close-up")

    def test_casting_input_groups_preserve_all_chapter_text(self):
        text = "A named character speaks.\n" * 1000
        groups = list(text_groups(text, 900))
        self.assertEqual("".join(groups), text)
        self.assertTrue(all(len(group) <= 900 for group in groups))

    def test_overlapping_ai_shots_trim_at_director_selected_boundary(self):
        from studio_service import StudioService

        service = object.__new__(StudioService)
        shots = [
            {"startSentence": 0, "endSentence": 1, "action": "A close-up"},
            {"startSentence": 1, "endSentence": 1, "action": "A reaction"},
        ]
        service.trim_shot_overlaps(shots)
        service.validate_shot_ranges(shots, {"startSentence": 0, "endSentence": 1})
        self.assertEqual(shots[0]["endSentence"], 0)
        self.assertEqual(shots[1]["action"], "A reaction")
        self.assertEqual(shots[0]["timingRepair"]["originalEndSentence"], 1)
        with self.assertRaises(ValueError):
            service.validate_shot_ranges(
                [{"startSentence": 0, "endSentence": 0}],
                {"startSentence": 0, "endSentence": 2},
            )

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
