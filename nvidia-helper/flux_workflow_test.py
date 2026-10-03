import json
import tempfile
import unittest
from pathlib import Path
from image_provider import ComfyImageProvider
from flux_workflow import bundle, MODEL


class FluxWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        path = Path(self.temp.name) / 'workflows.json'
        path.write_text(json.dumps(bundle()))
        self.provider = ComfyImageProvider({'comfyWorkflow': str(path)})
        self.settings = {'model': MODEL, 'workflow': 'flux2-klein-4b-auto', 'seed': 71,
                         'width': 1344, 'height': 768, 'steps': 20}

    def tearDown(self):
        self.temp.cleanup()

    def test_reference_count_and_latent_chains(self):
        for count in range(4):
            graph, actual = self.provider.resolve_template({'referenceImages': ['face'] * count}, self.settings)
            self.assertEqual(graph['referenceCount'], count)
            self.assertEqual(actual['steps'], 4)
            self.assertEqual(actual['seed'], 71)
            for n in graph['prompt'].values():
                for v in n['inputs'].values():
                    if isinstance(v, list):
                        self.assertIn(v[0], graph['prompt'])
            self.assertEqual(len(graph['referenceNodes']), count)
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            self.provider.resolve_template({'referenceImages': ['face'] * 4}, self.settings)

    def test_edit_source_counts_and_character_reference_is_portrait(self):
        graph, _ = self.provider.resolve_template({'operation': 'edit', 'sourceImage': 'shot', 'referenceImages': ['face']}, self.settings)
        self.assertEqual(graph['referenceCount'], 2)
        graph, actual = self.provider.resolve_template({'purpose': 'character-reference'}, self.settings)
        self.assertEqual(graph['referenceCount'], 0)
        self.assertEqual((actual['width'], actual['height']), (1024, 1024))
        self.assertEqual(self.settings['width'], 1344)

    def test_existing_qwen_automatic_selection_is_preserved(self):
        qwen = self.settings | {'model': 'qwen-studio-auto', 'workflow': 'qwen-studio-auto'}
        graph, _ = self.provider.resolve_template({'referenceImages': ['face']}, qwen)
        self.assertEqual(graph['name'], 'qwen-reference-balanced-8')
        self.assertFalse(self.provider.getModelCapabilities(MODEL)['supportsNegativePrompt'])
        self.assertIn('flux2-klein-4b-auto', self.provider.planningCatalog()['workflow'])


if __name__ == '__main__':
    unittest.main()
