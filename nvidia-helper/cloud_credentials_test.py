"""Private cloud keys survive independent updates and never enter public config."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from studio_service import StudioService


class CloudCredentialsTest(unittest.TestCase):
    def service(self, folder):
        service = StudioService.__new__(StudioService)
        service.cv = threading.Condition()
        service.current = None
        service.config = {}
        service.config_path = Path(folder) / "studio-config.json"
        service.unload_models = lambda: None
        service.health = lambda: {"studioProtocol": 1}
        service.director = SimpleNamespace(config={})
        service.renderer = SimpleNamespace(config={})
        service.providers = {"native-flux": SimpleNamespace(config={})}
        return service

    def test_independent_key_updates_preserve_other_saved_key(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.service(folder)
            openai = "sk-" + "a" * 30
            runpod = "rpa_" + "b" * 30
            service.configure({"openaiApiKey": openai})
            service.configure({"runpodApiKey": runpod})
            saved = json.loads((Path(folder) / ".studio-secrets.json").read_text())
            self.assertEqual(saved, {"openaiApiKey": openai, "runpodApiKey": runpod})
            service.configure({"openaiApiKey": "sk-" + "c" * 30})
            self.assertEqual(json.loads((Path(folder) / ".studio-secrets.json").read_text())["runpodApiKey"], runpod)
            public = service.config_path.read_text()
            self.assertNotIn(openai, public)
            self.assertNotIn(runpod, public)
            self.assertFalse((Path(folder) / ".studio-secrets.json.tmp").exists())

    def test_invalid_second_key_does_not_modify_existing_secrets(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.service(folder)
            service.configure({"openaiApiKey": "sk-" + "a" * 30})
            secret_path = Path(folder) / ".studio-secrets.json"
            before = secret_path.read_bytes()
            with self.assertRaisesRegex(ValueError, "Runpod"):
                service.configure({"openaiApiKey": "sk-" + "c" * 30, "runpodApiKey": "short"})
            self.assertEqual(before, secret_path.read_bytes())

    def test_runtime_changes_are_rejected_while_generating(self):
        with tempfile.TemporaryDirectory() as folder:
            service = self.service(folder)
            service.current = "running-job"
            with self.assertRaisesRegex(ValueError, "current operation"):
                service.configure({"runpodApiKey": "rpa_" + "b" * 30})
            self.assertFalse(service.config_path.exists())


if __name__ == "__main__":
    unittest.main()
