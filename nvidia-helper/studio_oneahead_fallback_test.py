"""An unsubmitted endpoint block must not enter the image fallback buyer."""
import copy
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock, patch

from image_provider import ImageAdmissionBlocked, RemoteGenerationUncertain
from studio_service import StudioService


class FallbackBought(RuntimeError):
    pass


def fixture(error, retries=2):
    service = StudioService.__new__(StudioService)
    primary = SimpleNamespace(
        id='comfyui', unload=Mock(),
        getCapabilities=lambda: {'maxReferenceImages': 2},
        generateImage=Mock(side_effect=error))
    fallback = SimpleNamespace(
        id='native-flux', unload=Mock(),
        getCapabilities=lambda: {'maxReferenceImages': 2},
        getRecommendedSettings=lambda: {'model': 'fallback-model', 'steps': 4},
        generateImage=Mock(side_effect=FallbackBought('Fallback dispatched')))
    service.providers = {'comfyui': primary, 'native-flux': fallback,
                         'existing': SimpleNamespace(unload=Mock())}
    lookups = []
    def provider(name):
        lookups.append(name)
        return service.providers[name]
    service.provider = provider
    service.director = SimpleNamespace(stop=Mock())
    service.store = SimpleNamespace(asset=Mock(side_effect=AssertionError('No asset read needed')))
    service.trace = SimpleNamespace(span=lambda *_args, **_kwargs: nullcontext())
    service.visual_spec_signature = lambda *_: 'frozen-visual-input'
    service.prepare_image_device = Mock()
    service.status = Mock()
    service.gate = Mock()
    service.checkpoint = Mock()
    service.check_cloud_budget = Mock()
    shot = {'id': 'shot-test', 'imagePath': '', 'imageProvider': 'comfyui',
            'imageModel': 'flux2-klein-4b', 'workflow': 'flux2-klein-4b-auto',
            'prompt': 'A frozen manhwa scene', 'negativePrompt': '',
            'generationSettings': {'width': 1280, 'height': 720, 'steps': 4},
            'characters': []}
    project = {'id': 'project-test', 'settings': {
        'maxImageRetries': retries, 'voice': {'model': 'preserved-voice'},
        'image': {'fallbackEnabled': True, 'fallback': {
            'provider': 'native-flux', 'model': 'fallback-model', 'workflow': 'fallback'}}},
        'chapters': [{'id': 'chapter-test', 'name': 'A chapter', 'number': 1,
                      'scenes': [{'id': 'scene-test', 'shots': [shot]}]}]}
    return service, project, primary, fallback, lookups


class OneAheadFallbackTest(unittest.TestCase):
    def generate(self, service, project):
        with patch('studio_service.require_shot', return_value={'status': 'PASSED'}), \
             patch('studio_service.select_references', return_value=([], [])), \
             patch('studio_service.format_prompt', return_value=('Frozen fallback prompt', '')):
            return StudioService.generate(service, project, 'chapter-test', 'shot-test', 42, {})

    def test_unsubmitted_blocks_never_retry_or_use_configured_paid_fallback(self):
        for reason in ('Unresolved endpoint', 'Unverified owned worker', 'Changed worker epoch'):
            with self.subTest(reason=reason):
                error = ImageAdmissionBlocked(reason)
                service, project, primary, fallback, lookups = fixture(error)
                before = copy.deepcopy(project)
                with self.assertRaises(ImageAdmissionBlocked) as raised:
                    self.generate(service, project)
                self.assertIs(raised.exception, error)
                self.assertFalse(raised.exception.submitted)
                self.assertIsNone(raised.exception.prompt_id)
                self.assertEqual(primary.generateImage.call_count, 1)
                fallback.generateImage.assert_not_called()
                primary.unload.assert_not_called()
                self.assertEqual(lookups, ['comfyui'])
                self.assertEqual(service.check_cloud_budget.call_count, 1)
                self.assertEqual(project, before)

    def test_known_submitted_uncertainty_keeps_existing_no_fallback_policy(self):
        service, project, primary, fallback, _ = fixture(RemoteGenerationUncertain('owned-A'))
        with self.assertRaises(RemoteGenerationUncertain) as raised:
            self.generate(service, project)
        self.assertEqual(raised.exception.prompt_id, 'owned-A')
        self.assertEqual(primary.generateImage.call_count, 1)
        fallback.generateImage.assert_not_called()

    def test_positive_control_ordinary_backend_failure_can_reach_configured_fallback(self):
        # This proves the blocked-request assertions exercise the real fallback
        # branch instead of a fixture that could never submit a replacement.
        service, project, primary, fallback, lookups = fixture(RuntimeError('Known backend failure'), retries=0)
        with self.assertRaisesRegex(FallbackBought, 'Fallback dispatched'):
            self.generate(service, project)
        self.assertEqual(primary.generateImage.call_count, 1)
        self.assertEqual(fallback.generateImage.call_count, 1)
        self.assertEqual(lookups, ['comfyui', 'native-flux'])


if __name__ == '__main__':
    unittest.main()
