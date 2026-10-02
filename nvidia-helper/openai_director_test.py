import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from director_provider import obj, STR
from openai_director import OpenAIDirector


def stream(value='{"summary":"complete"}', status="completed", **extra):
    response = {"status": status, "usage": {"input_tokens": 100, "output_tokens": 25}, **extra}
    events = [{"type": "response.output_text.delta", "delta": value},
              {"type": "response." + status, "response": response}]
    return io.BytesIO(b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events))


class LunaTest(unittest.TestCase):
    def director(self, path):
        director = OpenAIDirector({}, path)
        director.key = lambda: "sk-test-placeholder-not-a-real-key"
        return director

    def test_stream_contract_cache_and_usage(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            timings = []
            director.timing_callback = lambda *args: timings.append(args)
            with patch("openai_director.urllib.request.urlopen", return_value=stream()) as request:
                for _ in range(2):
                    self.assertEqual(director.call("Story analyst", {"sentences": []}, obj({"summary": STR}), lambda *args: None), {"summary": "complete"})
                body = json.loads(request.call_args.args[0].data)
                self.assertEqual(body["model"], "gpt-6-luna")
                self.assertFalse(body["store"])
                self.assertTrue(body["stream"])
                self.assertTrue(body["text"]["format"]["strict"])
                self.assertEqual(request.call_count, 1)
            self.assertEqual(timings[0][2]["inputTokens"], 100)
            self.assertTrue(timings[1][2]["reused"])
            self.assertEqual(timings[1][1], 0)
            self.assertIsNone(director.response)

    def test_missing_credentials_and_bad_schema_do_not_save_a_pass(self):
        with tempfile.TemporaryDirectory() as folder:
            director = OpenAIDirector({}, folder)
            with patch.dict("os.environ", {}, clear=True), patch("openai_director.urllib.request.urlopen") as request:
                with self.assertRaisesRegex(RuntimeError, "API key"):
                    director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
                request.assert_not_called()
            director = self.director(folder)
            with patch("openai_director.urllib.request.urlopen", return_value=stream('{"summary":123}')):
                with self.assertRaises(ValueError):
                    director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
            self.assertFalse(list(Path(folder).rglob("*.json")))

    def test_cancel_closes_stream_and_preserves_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            response = stream()
            def gate(message):
                if director.response is not None:
                    raise InterruptedError("cancelled")
            with patch("openai_director.urllib.request.urlopen", return_value=response):
                with self.assertRaises(InterruptedError):
                    director.call("Story analyst", {}, obj({"summary": STR}), gate)
            self.assertTrue(response.closed)
            self.assertFalse(list(Path(folder).rglob("*.json")))

    def test_token_limit_retries_only_this_request(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            timings = []
            director.timing_callback = lambda *args: timings.append(args)
            with patch("openai_director.urllib.request.urlopen", side_effect=[stream(status="incomplete", incomplete_details={"reason":"max_output_tokens"}), stream()]) as request:
                director.call("Story analyst", {}, obj({"summary": STR}), lambda *args: None)
                self.assertEqual(request.call_count, 2)
                self.assertEqual(json.loads(request.call_args.args[0].data)["max_output_tokens"], 24000)
                self.assertEqual(timings[0][2]["inputTokens"], 200)
                self.assertEqual(timings[0][2]["tokens"], 50)

    def test_http_error_never_exposes_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            director = self.director(folder)
            error = urllib.error.HTTPError("https://api.openai.com", 401, "unauthorized", {}, io.BytesIO(b"private"))
            with patch("openai_director.urllib.request.urlopen", side_effect=error):
                with self.assertRaisesRegex(RuntimeError, "HTTP 401") as result:
                    director.verify_key()
            self.assertNotIn("sk-", str(result.exception))


if __name__ == "__main__":
    unittest.main()
