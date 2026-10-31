import tempfile
import unittest
import json
import copy
from pathlib import Path
from unittest.mock import patch
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
        self.assertEqual(actual["steps"], 40)

    def test_character_reference_uses_quality_portrait_without_changing_shot_resolution(self):
        template, actual = self.provider.resolve_template({"purpose": "character-reference"}, self.settings)
        self.assertEqual(template["name"], "qwen-text-quality-40")
        self.assertEqual((actual["width"], actual["height"]), (1024, 1024))
        self.assertEqual(self.settings["width"], 1344)

    def test_explicit_quality_has_no_lightning_adapter(self):
        template, actual = self.provider.resolve_template({"workflow": "qwen-reference-quality-40", "referenceImages": ["face"]}, self.settings)
        self.assertEqual(actual["steps"], 40)
        self.assertEqual(actual["guidance"], 3)
        self.assertEqual(actual["loras"], [])
        self.assertNotIn("14", template["prompt"])

    def compile_until_submission(self, workflow, references=(), guidance=1, resolved_guidance=None):
        """Exercise real binding/trimming/compiler, stopping before any POST."""
        import urllib.error
        class Captured(urllib.error.HTTPError):
            def __init__(self):
                super().__init__('http://127.0.0.1:8188/prompt', 400, 'Offline graph capture; no HTTP dispatched', {}, None)
        captured={}
        settings=self.settings | {"workflow":workflow,"guidance":guidance}
        request={"workflow":workflow,"settings":settings,"prompt":"Exact positive prose",
                 "negativePrompt":"Exact negative prose","referenceImages":list(references)}
        def stop(url,body=None,timeout=180):
            captured.update(body["prompt"])
            raise Captured()
        original_resolve=self.provider.resolve_template
        def resolve(r,s):
            template,actual=original_resolve(r,s)
            return template,actual if resolved_guidance is None else actual | {"guidance":resolved_guidance}
        self.provider._uploads["frozen-reference"]="unchanged-reference.png"
        with patch.object(self.provider,"validateSettings",lambda value:value), \
             patch.object(self.provider,"healthCheck",lambda **kw:{"installed":True}), \
             patch.object(self.provider,"resolve_template",resolve), \
             patch.object(self.provider,"_request_json",stop), \
             patch("image_provider._image_encoding_cache.reference_bytes",lambda ref:(b"unchanged", "frozen-reference")):
            with self.assertRaises(Captured):self.provider.generateImage(request,lambda *args:None)
        return captured

    @staticmethod
    def output_dependencies(graph):
        seen=set()
        def visit(key):
            if key in seen:return
            seen.add(key)
            for value in graph[key]["inputs"].values():
                if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and value[0] in graph:
                    visit(value[0])
        visit("11")
        return seen

    def test_resolved_cfg1_compiler_skips_negative_after_reference_trimming(self):
        graph=self.compile_until_submission("qwen-reference-fast-4",["face"])
        self.assertEqual(graph["9"]["inputs"]["negative"],graph["9"]["inputs"]["positive"])
        self.assertEqual(graph["6"]["inputs"]["prompt"],"Exact positive prose")
        self.assertEqual(graph["7"]["inputs"]["prompt"],"Exact negative prose")
        self.assertEqual(graph["20"]["inputs"]["image"],"unchanged-reference.png")
        self.assertNotIn("21",graph);self.assertNotIn("22",graph)
        self.assertIn("7",graph);self.assertIn("13",graph)
        self.assertIn("7",bundle()["variants"][2]["prompt"])
        dependencies=self.output_dependencies(graph)
        self.assertNotIn("7",dependencies);self.assertNotIn("13",dependencies)
        self.assertTrue({"6","12","20"} <= dependencies)
        self.assertEqual(graph["9"]["inputs"]["seed"],12)
        self.assertEqual((graph["8"]["inputs"]["width"],graph["8"]["inputs"]["height"]),(1344,768))

    def test_resolved_cfg4_keeps_distinct_negative_dependencies(self):
        graph=self.compile_until_submission("qwen-text-quality-40")
        self.assertEqual(graph["9"]["inputs"]["cfg"],4)
        self.assertNotEqual(graph["9"]["inputs"]["negative"],graph["9"]["inputs"]["positive"])
        self.assertIn("7",self.output_dependencies(graph))

    def test_actual_guidance_override_above_one_keeps_negative(self):
        graph=self.compile_until_submission("qwen-reference-fast-4",["face"],resolved_guidance=4)
        self.assertEqual(graph["9"]["inputs"]["cfg"],4)
        self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])
        self.assertTrue({"7","13"} <= self.output_dependencies(graph))

    def test_request_guidance_still_obeys_existing_lightning_preset(self):
        graph=self.compile_until_submission("qwen-reference-fast-4",["face"],guidance=4)
        self.assertEqual(graph["9"]["inputs"]["cfg"],1)
        self.assertEqual(graph["9"]["inputs"]["negative"],["12",0])

    def test_manual_single_export_is_untouched(self):
        manual=copy.deepcopy(bundle()["variants"][2]);manual["capabilities"]=bundle()["capabilities"]
        Path(self.provider.config["comfyWorkflow"]).write_text(json.dumps(manual))
        self.settings["model"]=manual["model"]
        graph=self.compile_until_submission(manual["name"],["face"])
        self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])

    def test_modified_custom_bundle_is_untouched(self):
        custom=bundle();custom["variants"][2]["prompt"]["5"]["inputs"]["pre_cfg"]=True
        Path(self.provider.config["comfyWorkflow"]).write_text(json.dumps(custom))
        graph=self.compile_until_submission("qwen-reference-fast-4",["face"])
        self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])

    def test_extra_custom_output_retains_negative_branch(self):
        custom=bundle();custom["variants"][2]["prompt"]["99"]={"class_type":"CustomReceipt","inputs":{"conditioning":["7",0]}}
        Path(self.provider.config["comfyWorkflow"]).write_text(json.dumps(custom))
        graph=self.compile_until_submission("qwen-reference-fast-4",["face"])
        self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])

    def test_builtin_mixed_bundle_optimizes_only_qwen(self):
        from flux_workflow import bundle as mixed_bundle
        data=mixed_bundle();Path(self.provider.config["comfyWorkflow"]).write_text(json.dumps(data))
        graph=self.compile_until_submission("qwen-text-fast-4")
        self.assertEqual(graph["9"]["inputs"]["negative"],["6",0])
        flux=next(t for t in data["variants"] if t["name"]=="flux2-klein-text-4")
        untouched=copy.deepcopy(flux["prompt"])
        self.assertFalse(self.provider._skip_builtin_qwen_cfg1_negative_encoding(flux,untouched))
        self.assertEqual(untouched,flux["prompt"])

    def test_only_exact_cfg1_and_default_native_post_norm_are_eligible(self):
        template=bundle()["variants"][2]
        for changes in ({"cfg":True},{"cfg":1.00000001},{"cfg":0}):
            graph=copy.deepcopy(template["prompt"]);graph["9"]["inputs"].update(changes)
            self.assertFalse(self.provider._skip_builtin_qwen_cfg1_negative_encoding(template,graph))
            self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])
        for node,field,value in (("9","class_type","CustomSampler"),("5","pre_cfg",True),("5","strength",.5)):
            graph=copy.deepcopy(template["prompt"])
            if field=="class_type":graph[node][field]=value
            else:graph[node]["inputs"][field]=value
            self.assertFalse(self.provider._skip_builtin_qwen_cfg1_negative_encoding(template,graph))
            self.assertEqual(graph["9"]["inputs"]["negative"],["13",0])

if __name__ == "__main__":
    unittest.main()
