import json, tempfile, unittest, io, urllib.error
from pathlib import Path
from unittest.mock import patch
from director_provider import LocalQwenDirector, obj, STR


class DirectorRecoveryTest(unittest.TestCase):
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
