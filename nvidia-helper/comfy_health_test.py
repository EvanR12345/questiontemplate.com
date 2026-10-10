import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from image_provider import ComfyImageProvider

class SelectedWorkflowHealthTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();path=Path(self.folder.name)/'workflow.json'
        def variant(name,model,weight):return {'name':name,'model':model,
            'prompt':{'1':{'class_type':'Loader','inputs':{'unet_name':weight}}}}
        path.write_text(json.dumps({'model':'qwen-auto','name':'qwen-auto',
            'modelCapabilities':{'flux':{}},'automaticWorkflows':{'flux-auto':{'model':'flux'}},
            'variants':[variant('qwen-text','qwen','qwen-missing'),
                        variant('flux-text','flux','flux-present'),variant('flux-ref','flux','flux-present')]}))
        self.provider=ComfyImageProvider({'comfyWorkflow':str(path)})
        self.nodes={'Loader':{'input':{'required':{'unet_name':[['flux-present']]}}}}
    def tearDown(self):self.folder.cleanup()
    def test_removed_unused_qwen_does_not_block_selected_flux_or_ready_catalog(self):
        with patch('image_provider.request_json',return_value=self.nodes) as request:
            health=self.provider.healthCheck(model='flux',workflow='flux-auto')
            self.assertTrue(health['installed']);self.assertEqual(health['missingWeights'],[])
            generic=self.provider.healthCheck()
            self.assertTrue(generic['installed']);self.assertEqual(generic['models'],['flux'])
            self.assertIn('flux-auto',generic['workflow']);self.assertNotIn('qwen-auto',generic['workflow'])
            self.assertEqual(generic['unavailableWorkflows'],['qwen-text'])
            self.assertEqual(request.call_count,1)
    def test_selected_missing_workflow_still_fails_without_silent_model_substitution(self):
        with patch('image_provider.request_json',return_value=self.nodes):
            self.assertTrue(self.provider.healthCheck(model='flux',workflow='flux-text')['installed'])
            missing=self.provider.healthCheck(model='qwen',workflow='qwen-text')
            self.assertFalse(missing['installed']);self.assertEqual(missing['missingWeights'],['qwen-missing'])
            self.assertFalse(self.provider.healthCheck(model='unknown')['installed'])
