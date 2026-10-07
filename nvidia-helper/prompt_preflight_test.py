import copy
import unittest
from studio_data import new_project, character
from prompt_preflight import check_shot, require_shot


class PreflightTest(unittest.TestCase):
    def fixture(self):
        project = new_project()
        person = character('Mira')
        person['references'] = [{'path': 'face.png'}]
        project['characters'].append(person)
        shot = {'chapterId': project['chapters'][0]['id'], 'start': 0, 'end': 5,
                'prompt': 'Mira raises her sword.', 'action': 'Mira raises her sword.',
                'characters': [{'id': person['id'], 'type': 'main', 'appearanceState': {'sword': 'right hand'}}]}
        return project, shot

    def test_invalid_request_blocks_before_generation_without_modifying_source(self):
        project, shot = self.fixture()
        shot.update(prompt='', end=0)
        before = copy.deepcopy((project, shot))
        with self.assertRaisesRegex(ValueError, 'Image preflight'):
            require_shot(project, shot)
        self.assertEqual((project, shot), before)

    def test_unknown_and_duplicate_cast_is_an_error(self):
        project, shot = self.fixture()
        shot['characters'] *= 2
        self.assertFalse(check_shot(project, shot)['passed'])
        shot['characters'] = [{'id': 'missing', 'type': 'temporary'}]
        self.assertFalse(check_shot(project, shot)['passed'])

    def test_missing_visible_prop_warns_without_inventing_an_action(self):
        project, shot = self.fixture()
        shot['prompt'] = 'Mira raises a weapon.'
        result = check_shot(project, shot)
        self.assertTrue(result['passed'])
        self.assertEqual([i['code'] for i in result['issues']], ['VISIBLE_PROP_MISSING'])
        shot['action'] = 'Mira talks quietly.'
        self.assertEqual(check_shot(project, shot)['issues'], [])

    def test_story_clothing_change_and_reference_missing_are_not_identity_failures(self):
        project, shot = self.fixture()
        project['characters'][0]['references'] = []
        shot['characters'][0]['appearanceState']['outfit'] = 'new school uniform'
        result = check_shot(project, shot)
        self.assertTrue(result['passed'])
        self.assertEqual(result['issues'][0]['severity'], 'warning')


if __name__ == '__main__': unittest.main()
