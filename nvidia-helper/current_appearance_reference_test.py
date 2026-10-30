"""Offline regressions for subject-bound state and outfit-compatible references."""
import copy
import tempfile
import unittest

from image_provider import reference_prompt, select_references
from studio_data import ProjectStore, character, new_project


class CurrentAppearanceReferences(unittest.TestCase):
    def fixture(self):
        project = new_project('Current scene')
        first, second = character('Christopher'), character('Jonathan')
        first['defaultAppearance']['outfit'] = 'dark suit'
        second['defaultAppearance']['outfit'] = 'white suit'
        project['characters'] = [first, second]
        shot = {
            'chapterId': project['chapters'][0]['id'], 'imageModel': 'klein',
            'characters': [
                {'id': first['id'], 'type': 'main', 'appearanceState': {
                    'outfit': 'changed outfit', 'posture': 'kneeling', 'restraint': 'wrists tied'}},
                {'id': second['id'], 'type': 'main', 'appearanceState': {
                    'outfit': 'white suit', 'posture': 'seated on a chair'}},
            ],
            'camera': {'shot': 'medium wide', 'angle': 'eye level'},
            # Global substring presence must not replace a subject binding.
            'prompt': 'Jonathan wears a white suit and is seated on a chair. Christopher is kneeling in a changed outfit; wrists tied.',
            'manual': {},
        }
        return project, shot, first, second

    def test_named_state_is_retained_even_when_all_values_exist_in_body(self):
        project, shot, first, second = self.fixture()
        before = copy.deepcopy((project, shot))
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}, {'characterId': second['id']}])
        self.assertIn('Current-shot state for Christopher: outfit: changed outfit; posture: kneeling; restraint: wrists tied.', prompt)
        self.assertIn('Current-shot state for Jonathan: outfit: white suit; posture: seated on a chair.', prompt)
        self.assertNotIn('Current-shot state for Christopher: outfit: white suit', prompt)
        self.assertIn('each named person matched to their own reference and current-shot state', prompt)
        self.assertEqual((project, shot), before)

    def test_same_outfit_values_stay_bound_to_different_poses(self):
        project, shot, first, second = self.fixture()
        shot['characters'][0]['appearanceState']['outfit'] = 'white suit'
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}, {'characterId': second['id']}])
        self.assertIn('Christopher: outfit: white suit; posture: kneeling; restraint: wrists tied.', prompt)
        self.assertIn('Jonathan: outfit: white suit; posture: seated on a chair.', prompt)

    def test_single_changed_outfit_keeps_its_named_state_near_reference(self):
        project, shot, first, _ = self.fixture()
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}])
        self.assertIn('Current-shot state for Christopher: outfit: changed outfit; posture: kneeling; restraint: wrists tied.', prompt)
        self.assertNotIn('Keep each named person', prompt)

    def test_single_unchanged_outfit_keeps_existing_no_duplicate_guidance(self):
        project, shot, first, _ = self.fixture()
        shot['characters'][0]['appearanceState']['outfit'] = '  DARK   suit '
        shot['prompt'] = 'Christopher wears a   dark   suit and is kneeling, wrists tied.'
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}])
        self.assertNotIn('Current-shot state for', prompt)
        self.assertNotIn('Current appearance:', prompt)

    def test_reference_order_and_edit_offset_do_not_swap_subject_states(self):
        project, shot, first, second = self.fixture()
        prompt = reference_prompt(project, shot, [{'characterId': second['id']}, {'characterId': first['id']}], 'edit')
        self.assertIn('Image 1 is the shot to edit', prompt)
        self.assertIn('image 2 as the identity reference for Jonathan', prompt)
        self.assertIn('image 3 as the identity reference for Christopher', prompt)
        self.assertIn('Current-shot state for Jonathan: outfit: white suit; posture: seated on a chair.', prompt)
        self.assertIn('Current-shot state for Christopher: outfit: changed outfit; posture: kneeling; restraint: wrists tied.', prompt)

    def test_manual_prompt_reference_guidance_keeps_previous_behavior(self):
        project, shot, first, second = self.fixture()
        shot['manual']['prompt'] = True
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}, {'characterId': second['id']}])
        self.assertNotIn('Current-shot state for', prompt)
        self.assertNotIn('Keep each named person', prompt)
        self.assertNotIn('Current appearance:', prompt)
        shot['prompt'] = 'Hand-authored prompt.'
        prompt = reference_prompt(project, shot, [{'characterId': first['id']}])
        self.assertIn('Current appearance: outfit: changed outfit; posture: kneeling; restraint: wrists tied.', prompt)

    def selection(self, project, shot, first, second):
        class Provider:
            def getModelCapabilities(self, model):
                return {'maxReferenceImages': 2, 'supportsStyleReference': False}
        with tempfile.TemporaryDirectory() as root:
            store = ProjectStore(root)
            for person in (first, second):
                person['references'] = [
                    {'path': person['id'] + '-body.png', 'kind': 'full-body'},
                    {'path': person['id'] + '-face.png', 'kind': 'face'},
                    {'path': person['id'] + '-side.png', 'kind': 'side'},
                    {'path': person['id'] + '-back.png', 'kind': 'back'},
                ]
            store.save(project)
            for person in (first, second):
                for ref in person['references']:
                    store.asset(project['id'], ref['path']).write_bytes(b'unchanged fixture reference')
            refs, metadata = select_references(project, shot, store, Provider())
            self.assertEqual(len(refs), 2)
            self.assertEqual([ref['characterId'] for ref in metadata], [first['id'], second['id']])
            return metadata

    def test_changed_outfit_prefers_face_and_unchanged_outfit_keeps_body(self):
        project, shot, first, second = self.fixture()
        metadata = self.selection(project, shot, first, second)
        self.assertEqual([ref['kind'] for ref in metadata], ['face', 'full-body'])

    def test_changed_outfit_keeps_profile_and_back_camera_references(self):
        for angle, expected in [('profile', 'side'), ('from behind', 'back')]:
            with self.subTest(angle=angle):
                project, shot, first, second = self.fixture()
                shot['camera']['angle'] = angle
                metadata = self.selection(project, shot, first, second)
                self.assertEqual([ref['kind'] for ref in metadata], [expected, expected])

    def test_missing_baseline_or_current_clothing_does_not_invent_change(self):
        for missing in ('baseline', 'current'):
            with self.subTest(missing=missing):
                project, shot, first, second = self.fixture()
                if missing == 'baseline':
                    first['defaultAppearance']['outfit'] = ''
                else:
                    shot['characters'][0]['appearanceState'].pop('outfit')
                metadata = self.selection(project, shot, first, second)
                self.assertEqual(metadata[0]['kind'], 'full-body')

    def test_manual_prompt_retains_reference_kind_priority(self):
        project, shot, first, second = self.fixture()
        shot['manual']['prompt'] = True
        metadata = self.selection(project, shot, first, second)
        self.assertEqual([ref['kind'] for ref in metadata], ['full-body', 'full-body'])


if __name__ == '__main__':
    unittest.main()
