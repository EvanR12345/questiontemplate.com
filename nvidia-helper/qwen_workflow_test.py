import tempfile
import unittest
import json
from pathlib import Path
from image_provider import ComfyImageProvider
from qwen_workflow import bundle

class QwenWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        path = Path(self.folder.name) / "workflows.json"
        path.write_text(json.dumps(bundle()))
        self.provider = ComfyImageProvider({"comfyWorkflow": str(path)})
        self.settings = {"model": "qwen-studio-auto", "width": 1344, "height": 768, "steps": 8, "seed": 12}

    def tearDown(self):
        self.folder.cleanup()

    def test_auto_is_explicit_and_resolves_actual_model_and_adapter(self):
        template, actual = self.provider.resolve_template({}, self.settings)
        self.assertEqual(template["model"], "qwen-image-2512")
        self.assertEqual(actual["steps"], 4)
        template, actual = self.provider.resolve_template({"referenceImages": ["face"]}, self.settings)
        self.assertEqual(template["model"], "qwen-image-edit-2511")
        self.assertEqual(actual["steps"], 8)
        self.assertIn("8steps", actual["loras"][0]["name"])
        self.assertEqual(actual["width"], 1344)

    def test_incompatible_explicit_selection_does_not_drop_references(self):
        with self.assertRaisesRegex(ValueError, "cannot condition"):
            self.provider.resolve_template({"workflow": "qwen-text-fast-4", "referenceImages": ["face"]}, self.settings)
        with self.assertRaisesRegex(ValueError, "needs a source"):
            self.provider.resolve_template({"workflow": "qwen-reference-balanced-8"}, self.settings)
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.provider.resolve_template({}, self.settings | {"model": "uninstalled-model"})

    def test_edit_source_selects_reference_workflow(self):
        template, actual = self.provider.resolve_template({"operation": "edit", "sourceImage": "source"}, self.settings)
        self.assertEqual(template["kind"], "reference")

    def test_explicit_quality_has_no_lightning_adapter(self):
        template, actual = self.provider.resolve_template({"workflow": "qwen-reference-quality-40", "referenceImages": ["face"]}, self.settings)
        self.assertEqual(actual["steps"], 40)
        self.assertEqual(actual["guidance"], 3)
        self.assertEqual(actual["loras"], [])
        self.assertNotIn("14", template["prompt"])

if __name__ == "__main__":
    unittest.main()
