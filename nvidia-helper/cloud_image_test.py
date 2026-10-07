"""Cloud image checks that do not rent a GPU or download weights."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from image_provider import ComfyImageProvider, reference_prompt, select_references, visible_appearance, visual_continuity
from studio_data import ProjectStore, character, new_project


class CloudImageTest(unittest.TestCase):
    def test_continuity_uses_human_names_without_database_identifier_fields(self):
        state = {'location': 'loc-123', 'locationId': 'loc-123', 'grid': 'room layout',
                 'characters': {'char-123': {'characterId': 'char-123', 'outfit': 'blue coat',
                                            'currentAppearance': {'phone': 'on floor', 'reference_ids': ['asset-123']}}}}
        before = copy.deepcopy(state)
        visible = visual_continuity(state, {'loc-123': 'School hall', 'char-123': 'Mira'})
        self.assertEqual(visible, {'location': 'School hall', 'grid': 'room layout',
                         'characters': {'Mira': {'outfit': 'blue coat', 'currentAppearance': {'phone': 'on floor'}}}})
        self.assertEqual(state, before)

    def test_list_continuity_keeps_physical_facts_and_removes_record_ids(self):
        value = {'objects': [{'id': 'object-123', 'name': 'silver watch', 'position': 'left wrist'}],
                 'weather': 'rain', 'clothing': 'new uniform'}
        self.assertEqual(visual_continuity(value, {}), {'objects': [{'name': 'silver watch', 'position': 'left wrist'}],
                        'weather': 'rain', 'clothing': 'new uniform'})

    def test_generic_raised_weapon_retains_one_known_held_prop(self):
        shot = {'action':'Christopher remains ready to fight.', 'narrationSegment':'He was still fighting.',
                'pose':'Weapon raised toward his opponents.', 'camera':{}}
        selected = {'appearanceState':{'outfit':'dark jacket','gun':'Holding a gun in his hand','hammer':'carried'}}
        self.assertEqual(visible_appearance(shot, selected), {'outfit':'dark jacket','gun':'Holding a gun in his hand'})
        self.assertEqual(selected['appearanceState']['hammer'], 'carried')
        selected['appearanceState']['hammer'] = 'held in right hand'
        self.assertEqual(visible_appearance(shot, selected), {'outfit':'dark jacket'})
        selected['appearanceState']['hammer'] = 'carried'
        shot['pose'] = 'Unarmed, without any weapon.'
        self.assertEqual(visible_appearance(shot, selected), {'outfit':'dark jacket'})
        shot['pose'] = 'Weapon raised toward his opponents.'
        selected['appearanceState']['gun'] = 'No longer holding it; dropped on the floor'
        self.assertEqual(visible_appearance(shot, selected), {'outfit':'dark jacket'})

    def test_historical_weapons_do_not_override_current_shot_or_erase_story_state(self):
        project, shot, metadata = self.project_and_shot()
        selected = shot['characters'][0]
        selected['appearanceState'].update(gun='right hand', sword='carried')
        shot.update(action='Michael comforts Sarah after hearing distant gunfire.',
                    narrationSegment='The room finally fell silent.')
        prompt = reference_prompt(project, shot, metadata)
        self.assertNotIn('"gun"', prompt)
        self.assertNotIn('"sword"', prompt)
        self.assertEqual(selected['appearanceState']['gun'], 'right hand')
        shot['camera']['composition'] = 'Sword visible at his belt; Sarah beside him.'
        self.assertEqual(visible_appearance(shot, selected)['sword'], 'carried')
        self.assertNotIn('gun', visible_appearance(shot, selected))

    def test_visual_qc_distinguishes_offscreen_inventory_from_composition(self):
        from studio_service import StudioService
        project, shot, _ = self.project_and_shot()
        shot.update(action='Michael groans and clutches his shoulder.', narrationSegment='He groaned in pain.',
                    imageProvider='comfyui', intentionalAppearanceChanges=[])
        shot['characters'][0]['appearanceState'].update(gun='carried', hammer='carried')
        class Director:
            def inspectGeneratedImage(self, context, gate):
                self.context = context
                return {'pass':True,'issues':[],'action':'accept','repairPrompt':''}
            def stop(self): pass
        with tempfile.TemporaryDirectory() as root:
            service = StudioService.__new__(StudioService)
            service.store = ProjectStore(root)
            service.store.save(project)
            image = service.store.asset(project['id'],'shot.png')
            image.write_bytes(b'png')
            service.director = Director()
            service.select_director = lambda p:None
            service.provider = lambda _:type('Provider',(),{'getCapabilities':lambda _: {'maxReferenceImages':0}})()
            service.gate = lambda *args:None
            service.visual_check(project, shot, image)
            expected = service.director.context['expectedShot']
            self.assertNotIn('gun', expected['characters'][0]['appearanceState'])
            self.assertEqual(expected['inventoryContext'][shot['characters'][0]['id']]['hammer'], 'carried')
            self.assertIn('close-up',expected['propVisibilityPolicy'])

    def test_offline_planning_defers_hardware_check_but_generation_validation_does_not(self):
        provider = self.provider()
        with patch('image_provider.request_json', side_effect=OSError('worker stopped')) as network:
            settings = provider.validateSettings({'width':1344,'height':768,'steps':8,'seed':42}, check_hardware=False)
            self.assertEqual(settings['seed'],42)
            network.assert_not_called()
            with self.assertRaisesRegex(RuntimeError,'worker stopped'):
                provider.validateSettings(settings)

    def provider(self):
        provider = ComfyImageProvider({})
        provider.template = lambda: {'modelCapabilities': {}}
        provider.getCapabilities = lambda: {
            "hardwareRequirements": {"vramGB": 24},
            "maxResolution": 2048,
            "maxReferenceImages": 3,
            "supportsLoRA": True,
            "supportsControlNet": False,
            "supportsIPAdapter": False,
        }
        return provider

    def test_selected_klein_hardware_requirement_does_not_inherit_qwen_40gb(self):
        provider = self.provider()
        provider.template = lambda: {'modelCapabilities': {'flux2-klein-4b': {'hardwareRequirements': {'vramGB':16}}}}
        with patch('image_provider.request_json', return_value={'devices':[{'type':'cuda','vram_total':24*2**30}]}):
            settings = provider.validateSettings({'model':'flux2-klein-4b','width':1344,'height':768,'steps':4,'seed':7})
            self.assertEqual(settings['model'], 'flux2-klein-4b')

    def test_remote_32gb_is_valid_even_when_laptop_has_4gb(self):
        provider = self.provider()
        with patch("image_provider.gpu_memory_gb", return_value=4), patch(
            "image_provider.request_json",
            return_value={"devices": [{"type": "cuda", "vram_total": 32 * 2**30}]},
        ) as server:
            for _ in range(2):
                result = provider.validateSettings(
                    {"width": 1344, "height": 768, "steps": 40, "seed": 41}
                )
                self.assertEqual(result["seed"], 41)
            server.assert_called_once_with(
                "http://127.0.0.1:8188/system_stats", timeout=3
            )

    def test_server_hardware_not_laptop_determines_availability(self):
        with patch("image_provider.gpu_memory_gb", return_value=80), patch(
            "image_provider.request_json",
            return_value={"devices": [{"vram_total": 16 * 2**30}]},
        ):
            with self.assertRaisesRegex(ValueError, "16.0 GiB"):
                self.provider().validateSettings(
                    {"width": 1024, "height": 1024, "steps": 40, "seed": 41}
                )

    def test_unreachable_server_is_an_error_not_a_local_gpu_fallback(self):
        with patch("image_provider.request_json", side_effect=OSError("tunnel closed")):
            with self.assertRaisesRegex(RuntimeError, "SSH tunnel.*tunnel closed"):
                self.provider().available_vram_gb()

    def test_cpu_server_cannot_claim_gpu_workflow(self):
        with patch("image_provider.request_json", return_value={"devices": []}):
            with self.assertRaisesRegex(ValueError, "0.0 GiB"):
                self.provider().validateSettings(
                    {"width": 1024, "height": 1024, "steps": 40, "seed": 41}
                )

    def project_and_shot(self):
        project = new_project()
        first, second = character("Michael"), character("Sarah")
        first["defaultAppearance"]["outfit"] = "black jacket"
        project["characters"] = [first, second]
        shot = {
            "chapterId": project["chapters"][0]["id"],
            "characters": [
                {"id": first["id"], "type": "main", "appearanceState": {"outfit": "school uniform", "watch": "left wrist"}},
                {"id": second["id"], "type": "main", "appearanceState": {"hair": "tied back", "outfit": "red coat"}},
            ],
            "camera": {"shot": "close-up", "angle": "eye level"},
        }
        metadata = [{"characterId": first["id"]}, {"characterId": second["id"]}]
        return project, shot, metadata

    def test_edit_references_have_correct_order_and_current_appearance(self):
        project, shot, metadata = self.project_and_shot()
        prompt = reference_prompt(project, shot, metadata, "edit")
        self.assertIn("Image 1 is the shot to edit", prompt)
        self.assertIn("image 2 as the identity reference for Michael", prompt)
        self.assertIn("image 3 as the identity reference for Sarah", prompt)
        self.assertIn("school uniform", prompt)
        self.assertIn("tied back", prompt)
        self.assertIn("left wrist", prompt)
        self.assertNotIn("black jacket", prompt)

    def test_edit_source_reserves_a_reference_slot(self):
        project, shot, _ = self.project_and_shot()
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder)
            store.save(project)
            for i, person in enumerate(project["characters"]):
                path = f"reference-{i}.png"
                store.asset(project["id"], path).write_bytes(b"reference")
                person["references"] = [{"path": path, "kind": "face"}]
            style_path = "style.png"
            store.asset(project["id"], style_path).write_bytes(b"style")
            project["styleReferences"] = [{"path": style_path}]
            provider = self.provider()
            provider.getCapabilities = lambda: {"maxReferenceImages": 3, "supportsStyleReference": True}
            refs, metadata = select_references(project, shot, store, provider, reserved_slots=1)
            self.assertEqual(len(refs), 2)
            self.assertEqual([m["characterId"] for m in metadata], [p["id"] for p in project["characters"]])

    def test_reference_cannot_be_uploaded_and_ignored_by_workflow(self):
        provider = self.provider()
        provider.validateSettings = lambda value: value
        provider.healthCheck = lambda: {"installed": True}
        provider.template = lambda: {"model": "qwen", "prompt": {}, "bindings": {}}
        with patch("image_provider.urllib.request.urlopen") as network:
            with self.assertRaisesRegex(ValueError, "no binding for reference 1"):
                provider.generateImage({"settings": {"model": "qwen", "seed": 1, "steps": 40}, "prompt": "Two people", "referenceImages": ["data:image/png;base64,eA=="]}, lambda *args: None)
            network.assert_not_called()

    def test_source_and_references_cannot_exceed_workflow_limit(self):
        provider = self.provider()
        provider.validateSettings = lambda value: value
        provider.healthCheck = lambda: {"installed": True}
        provider.template = lambda: {"model": "qwen", "prompt": {}, "bindings": {}}
        with self.assertRaisesRegex(ValueError, "Too many references"):
            provider.generateImage({"settings": {"model": "qwen", "seed": 1, "steps": 40}, "prompt": "Repair watch", "operation": "edit", "sourceImage": "source", "referenceImages": ["first", "second", "third"]}, lambda *args: None)

    def test_main_identity_references_cannot_be_silently_truncated(self):
        project, shot, _ = self.project_and_shot()
        for person in project['characters']:
            person['references'] = [{'path': 'not-read.png', 'kind': 'face'}]
        provider = self.provider()
        provider.getCapabilities = lambda: {'maxReferenceImages': 1}
        with patch('image_provider.data_url') as read:
            with self.assertRaisesRegex(ValueError, 'identity references for 2 main characters'):
                select_references(project, shot, None, provider)
            read.assert_not_called()

    def test_manual_references_require_explicit_selection_change_when_over_limit(self):
        project, shot, _ = self.project_and_shot()
        shot.update(manual={'referenceImages': True}, referenceImages=[{'path': 'first'}, {'path': 'second'}])
        provider = self.provider()
        provider.getCapabilities = lambda: {'maxReferenceImages': 1}
        with patch('image_provider.data_url') as read:
            with self.assertRaisesRegex(ValueError, 'no references were silently dropped'):
                select_references(project, shot, None, provider)
            read.assert_not_called()
        self.assertEqual(len(shot['referenceImages']), 2)

    def test_model_specific_reference_limit_is_respected_before_reading_assets(self):
        project, shot, _ = self.project_and_shot()
        for person in project['characters']:
            person['references'] = [{'path': 'not-read.png', 'kind': 'face'}]
        provider = self.provider()
        provider.getCapabilities = lambda: {'maxReferenceImages': 3}
        provider.getModelCapabilities = lambda model: {'maxReferenceImages': 1}
        shot['imageModel'] = 'one-reference-model'
        with patch('image_provider.data_url') as read:
            with self.assertRaisesRegex(ValueError, '1 available reference slots'):
                select_references(project, shot, None, provider)
            read.assert_not_called()

    def test_edit_source_cannot_displace_the_only_main_identity_reference(self):
        project, shot, _ = self.project_and_shot()
        shot['characters'] = shot['characters'][:1]
        project['characters'][0]['references'] = [{'path': 'not-read.png', 'kind': 'face'}]
        provider = self.provider()
        provider.getCapabilities = lambda: {'maxReferenceImages': 1}
        with patch('image_provider.data_url') as read:
            with self.assertRaisesRegex(ValueError, '0 available reference slots'):
                select_references(project, shot, None, provider, reserved_slots=1)
            read.assert_not_called()

    def test_generator_without_reference_support_does_not_read_unused_assets(self):
        project, shot, _ = self.project_and_shot()
        for person in project['characters']:
            person['references'] = [{'path': 'not-read.png', 'kind': 'face'}]
        provider = self.provider()
        provider.getCapabilities = lambda: {'maxReferenceImages': 0}
        with patch('image_provider.data_url') as read:
            self.assertEqual(select_references(project, shot, None, provider), ([], []))
            read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
