"""No paid review can silently omit a required main identity reference."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from studio_data import new_project, character
from studio_service import StudioService


class VisionReferencesTest(unittest.TestCase):
    def fixture(self, count):
        project = new_project()
        project['characters'] = [character(f'Person {index}') for index in range(count)]
        shot = {'chapterId': project['chapters'][0]['id'], 'imageProvider': 'comfyui',
                'characters': [{'id': c['id'], 'type': 'main', 'appearanceState': {}} for c in project['characters']],
                'intentionalAppearanceChanges': []}
        director = SimpleNamespace(max_vision_images=3, reasoning='Fast', stop=Mock(),
                    inspectGeneratedImage=Mock(return_value={'pass': True, 'issues': [], 'action': 'pass'}))
        service = StudioService.__new__(StudioService)
        service.director = director
        service.select_director = lambda project: None
        service.provider = lambda provider_id: object()
        service.gate = lambda *args: None
        service.record_timing = Mock()
        service.store = Mock()
        return project, shot, service

    def test_three_required_identities_fail_before_paid_request(self):
        project, shot, service = self.fixture(3)
        metadata = [{'characterId': c['id']} for c in project['characters']]
        with patch('studio_service.select_references', return_value=(['ref1', 'ref2', 'ref3'], metadata)):
            with self.assertRaisesRegex(ValueError, 'no identity references were silently dropped'):
                service.visual_check(project, shot, Path('not-read.png'))
        service.director.inspectGeneratedImage.assert_not_called()

    def test_optional_location_cannot_displace_two_main_identity_references(self):
        project, shot, service = self.fixture(2)
        metadata = [{'locationId': 'hall'}, *[{'characterId': c['id']} for c in project['characters']]]
        with patch('studio_service.select_references', return_value=(['location', 'ref1', 'ref2'], metadata)), \
             patch('studio_service.data_url', return_value='generated-image'):
            service.visual_check(project, shot, Path('not-read.png'))
        context = service.director.inspectGeneratedImage.call_args.args[0]
        self.assertEqual(context['_images'], ['generated-image', 'ref1', 'ref2'])
        self.assertEqual([r['role'] for r in context['referenceRoles']],
                         ['generated-shot', 'character-identity', 'character-identity'])

    def test_location_is_explicitly_distinguished_from_character_identity(self):
        project, shot, service = self.fixture(1)
        project['locations'] = [{'id': 'hall', 'name': 'School hallway'}]
        metadata = [{'locationId': 'hall'}, {'characterId': project['characters'][0]['id']}]
        with patch('studio_service.select_references', return_value=(['location', 'ref1'], metadata)), \
             patch('studio_service.data_url', return_value='generated-image'):
            service.visual_check(project, shot, Path('not-read.png'))
        context = service.director.inspectGeneratedImage.call_args.args[0]
        self.assertEqual(context['_images'], ['generated-image', 'ref1', 'location'])
        self.assertEqual(context['referenceRoles'][2],
                         {'imageIndex': 2, 'role': 'location', 'name': 'School hallway'})

    def test_replaced_provider_records_this_shot_not_previous_callback(self):
        project, shot, service = self.fixture(0)
        shot['id'] = 'current-shot'
        previous_callback = Mock()
        service.director.timing_callback = previous_callback
        replacement = SimpleNamespace(max_vision_images=3, stop=Mock())
        def review(context, gate):
            replacement.timing_callback('Visual QC', 1.5, {'estimatedUSD': .001, 'shot': 'wrong'})
            return {'pass': True, 'issues': [], 'action': 'pass'}
        replacement.inspectGeneratedImage = review
        service.select_director = lambda p: setattr(service, 'director', replacement)
        with patch('studio_service.select_references', return_value=([], [])), \
             patch('studio_service.data_url', return_value='generated-image'):
            service.visual_check(project, shot, Path('not-read.png'))
        previous_callback.assert_not_called()
        service.record_timing.assert_called_once_with(project['id'], 'Visual QC', 1.5,
            {'estimatedUSD': .001, 'shot': 'current-shot', 'detail': True, 'chapter': 1})

    def test_intro_check_records_intro_without_previous_chapter_metadata(self):
        project, shot, service = self.fixture(0)
        shot.pop('chapterId')
        shot['id'] = 'intro-shot'
        def review(context, gate):
            service.director.timing_callback('Visual QC', 2, {'chapter': 99})
            return {'pass': True, 'issues': [], 'action': 'pass'}
        service.director.inspectGeneratedImage = review
        with patch('studio_service.select_references', return_value=([], [])), \
             patch('studio_service.data_url', return_value='generated-image'):
            service.visual_check(project, shot, Path('not-read.png'))
        self.assertTrue(service.record_timing.call_args.args[3]['intro'])
        self.assertEqual(service.record_timing.call_args.args[3]['shot'], 'intro-shot')
        self.assertNotIn('chapter', service.record_timing.call_args.args[3])


if __name__ == '__main__': unittest.main()
