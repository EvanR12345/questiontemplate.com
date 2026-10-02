import json, tempfile, unittest, io, urllib.error
from pathlib import Path
from unittest.mock import patch
from director_provider import (
    LocalQwenDirector,
    DirectorProvider,
    obj,
    STR,
    validate_schema,
    compact_source_evidence,
)


class DirectorRecoveryTest(unittest.TestCase):
    def test_oversized_evidence_is_referenced_without_losing_source_or_cached_passes(
        self,
    ):
        with tempfile.TemporaryDirectory() as folder:
            director = LocalQwenDirector({}, Path(folder))
            director.start = lambda gate: None
            sentence = (
                "Michael receives the brass key and puts it in his left pocket. " * 14
            )
            context = {
                "sentences": [{"index": 0, "text": sentence}],
                "people": [{"id": "michael"}],
                "chapterCast": [{"id": "michael"}],
                "analysis": {
                    "changes": [
                        {
                            "characterId": "michael",
                            "field": "key",
                            "value": "left pocket",
                            "sentence": 0,
                            "reason": sentence,
                        }
                        for _ in range(24)
                    ]
                },
            }
            self.assertGreater(len(json.dumps(context)), 14000)
            captured = []

            def response(url, body, timeout):
                captured.append(json.loads(body["messages"][1]["content"]))
                return {
                    "choices": [
                        {
                            "message": {"content": '{"summary":"complete"}'},
                            "finish_reason": "stop",
                        }
                    ]
                }

            with patch("director_provider.request_json", response):
                for _ in range(2):
                    self.assertEqual(
                        director.call(
                            "Chapter director",
                            context,
                            obj({"summary": STR}),
                            lambda *args: None,
                        ),
                        {"summary": "complete"},
                    )
            self.assertEqual(len(captured), 1)
            compact = captured[0]
            self.assertEqual(compact["sentences"], context["sentences"])
            self.assertNotIn("chapterCast", compact)
            for change in compact["analysis"]["changes"]:
                self.assertEqual(change["value"], "left pocket")
                self.assertEqual(change["evidenceSentence"], 0)
            self.assertEqual(context["analysis"]["changes"][0]["reason"], sentence)

    def test_compaction_preserves_unmatched_evidence_and_distinct_cast(self):
        context = {
            "sentences": [{"index": 4, "text": "Michael wears a blue coat."}],
            "people": [{"id": "michael"}],
            "chapterCast": [{"id": "sarah"}],
            "changes": [
                {"sentence": 4, "reason": "blue coat", "value": "blue coat"},
                {"sentence": 4, "reason": "Unsupported evidence", "value": "red coat"},
            ],
        }
        result = compact_source_evidence(context)
        self.assertEqual(result["changes"][0]["evidenceSentence"], 4)
        self.assertEqual(result["changes"][1]["reason"], "Unsupported evidence")
        self.assertEqual(result["chapterCast"], context["chapterCast"])

    def test_overlarge_source_is_rejected_without_silently_truncating_story(self):
        with tempfile.TemporaryDirectory() as folder:
            director = LocalQwenDirector({}, Path(folder))
            director.start = lambda gate: None
            with patch("director_provider.request_json") as request:
                with self.assertRaisesRegex(ValueError, "safe local budget"):
                    director.call(
                        "Chapter director",
                        {"sentences": [{"text": "Story " * 4000}]},
                        obj({"summary": STR}),
                        lambda *args: None,
                    )
                request.assert_not_called()

    def test_continuity_without_objects_cannot_invent_repeated_held_objects(self):
        director = DirectorProvider()
        captured = []
        director.call = (
            lambda role, context, schema, gate: captured.append(schema) or {}
        )
        director.checkContinuity(
            {"objects": [], "sentences": [{"text": "Help!"}]}, lambda *args: None
        )
        field = captured[0]["properties"]["objectChanges"]
        validate_schema([], field)
        with self.assertRaisesRegex(ValueError, "Array length"):
            validate_schema([{}], field)

    def test_casting_evidence_cannot_grow_into_unbounded_sentence_numbers(self):
        director = DirectorProvider()
        captured = []
        director.call = lambda role, context, schema, gate: captured.append(
            (role, schema)
        ) or {"people": []}
        director.resolvePeople(
            {"chapterText": "The boss raised his hand."}, lambda *args: None
        )
        role, schema = captured[0]
        self.assertIn("NEVER list sentence numbers", role)
        evidence = schema["properties"]["people"]["items"]["properties"]["evidence"]
        validate_schema("The boss raised his hand.", evidence)
        with self.assertRaisesRegex(ValueError, "String length"):
            validate_schema("1, 3, 5, " * 100, evidence)

    def test_scene_and_shot_cast_ids_are_constrained(self):
        director = DirectorProvider()
        schemas = []
        director.call = lambda role, context, schema, gate: schemas.append(schema) or {}
        context = {"people": [{"id": "boss"}, {"id": "guard"}]}
        director.planChapter(context, lambda *args: None)
        director.planScenes(context, lambda *args: None)
        for schema in schemas:
            collection = next(iter(schema["properties"].values()))
            field = collection["items"]["properties"]["characters"]
            validate_schema(["boss", "guard"], field)
            with self.assertRaisesRegex(ValueError, "Invalid enum"):
                validate_schema(["invented-person"], field)

    def test_allocation_error_restarts_and_retries_only_the_failed_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            director = LocalQwenDirector({}, Path(folder))
            starts = []
            director.start = lambda gate: starts.append(True)
            error = urllib.error.HTTPError(
                "http://127.0.0.1:8766",
                500,
                "Internal Server Error",
                {},
                io.BytesIO(b'{"error":"bad allocation"}'),
            )
            valid = {
                "choices": [
                    {
                        "message": {"content": '{"summary":"complete"}'},
                        "finish_reason": "stop",
                    }
                ]
            }
            with patch("director_provider.request_json", side_effect=[error, valid]):
                self.assertEqual(
                    director.call(
                        "Story analyst", {}, obj({"summary": STR}), lambda *args: None
                    ),
                    {"summary": "complete"},
                )
            self.assertEqual(len(starts), 2)

    def test_text_runtime_uses_bounded_cache_auto_fit_and_lazy_vision(self):
        with tempfile.TemporaryDirectory() as folder:
            model = Path(folder) / "model.gguf"
            tool = Path(folder) / "server.exe"
            projector = Path(folder) / "vision.gguf"
            for file in (model, tool, projector):
                file.touch()
            director = LocalQwenDirector(
                {
                    "directorModel": str(model),
                    "directorExecutable": str(tool),
                    "directorProjector": str(projector),
                    "directorGpuLayers": 99,
                },
                Path(folder) / "logs",
            )
            with patch("director_provider.subprocess.Popen") as launch, patch(
                "director_provider.request_json", return_value={"status": "ok"}
            ):
                launch.return_value.poll.return_value = None
                director.start(lambda *args: None)
                args = launch.call_args.args[0]
                self.assertNotIn("--mmproj", args)
                self.assertEqual(args[args.index("-ngl") + 1], "auto")
                self.assertEqual(args[args.index("--cache-ram") + 1], "128")
                director.request_vision = True
                director.start(lambda *args: None)
                self.assertIn("--mmproj", launch.call_args.args[0])
                director.stop()

    def test_truncated_response_expands_budget_and_reuses_valid_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            director = LocalQwenDirector({}, Path(folder))
            director.start = lambda gate: None
            budgets, timings = [], []
            director.timing_callback = lambda *args: timings.append(args)

            def response(url, body, timeout):
                budgets.append(body["max_tokens"])
                if len(budgets) == 1:
                    return {
                        "choices": [
                            {
                                "message": {"content": '{"summary": "cut'},
                                "finish_reason": "length",
                            }
                        ],
                        "usage": {"prompt_tokens": 1400, "completion_tokens": 1800},
                    }
                return {
                    "choices": [
                        {
                            "message": {"content": '{"summary":"complete"}'},
                            "finish_reason": "stop",
                        }
                    ]
                }

            with patch("director_provider.request_json", response):
                result = director.call(
                    "Story analyst", {}, obj({"summary": STR}), lambda *args: None
                )
                self.assertEqual(result, {"summary": "complete"})
                self.assertEqual(budgets, [1800, 3600])
                self.assertEqual(len(timings), 2)
                self.assertEqual(
                    director.call(
                        "Story analyst", {}, obj({"summary": STR}), lambda *args: None
                    ),
                    result,
                )
                self.assertEqual(len(budgets), 2)
            diagnostic = json.loads(
                next((Path(folder) / "director-cache").glob("*.error.json")).read_text()
            )
            self.assertEqual(diagnostic["finishReason"], "length")

    def test_persistent_invalid_output_does_not_save_a_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            director = LocalQwenDirector({}, Path(folder))
            director.start = lambda gate: None
            response = {
                "choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}]
            }
            with patch(
                "director_provider.request_json", return_value=response
            ) as request:
                with self.assertRaisesRegex(ValueError, "three attempts"):
                    director.call(
                        "Story analyst", {}, obj({"summary": STR}), lambda *args: None
                    )
                self.assertEqual(request.call_count, 3)
            self.assertTrue(
                all(
                    p.name.endswith(".error.json")
                    for p in (Path(folder) / "director-cache").glob("*.json")
                )
            )


if __name__ == "__main__":
    unittest.main()
