"""Bounded repair request strategy and fresh generation context; no model calls."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from PIL import Image
from studio_automation import repair_operation,repair_prompt
from studio_service import StudioService
import studio_settings_test as fixtures

class RepairStrategyTest(unittest.TestCase):
 def fixture(self,count):
  p={'characters':[{'id':str(i),'references':[{'path':'ref.png'}]} for i in range(count)],'chapters':[{'id':'ch','people':[]}]}
  s={'chapterId':'ch','characters':[{'id':str(i),'type':'main'} for i in range(count)],'manual':{},'prompt':'Three characters enter a moonlit hall; medium-wide framing.'}
  provider=SimpleNamespace(id='comfyui',getCapabilities=lambda:{'supportsImageEditing':True,'maxReferenceImages':3})
  return p,s,provider
 def test_source_image_never_displaces_a_required_third_character_reference(self):
  p,s,provider=self.fixture(3);self.assertEqual(repair_operation(p,s,provider),'generate')
  p,s,provider=self.fixture(2);self.assertEqual(repair_operation(p,s,provider),'edit')
 def test_intro_repair_uses_project_identities_without_a_chapter(self):
  p,s,provider=self.fixture(2);s.pop('chapterId')
  self.assertEqual(repair_operation(p,s,provider),'edit')
 def test_manual_reference_selection_is_preserved_by_choosing_full_generation(self):
  p,s,provider=self.fixture(1);s.update(manual={'referenceImages':True},referenceImages=[{}, {}, {}])
  self.assertEqual(repair_operation(p,s,provider),'generate')
 def test_fresh_repair_prompts_retain_scene_facts_camera_and_correction_without_duplicates(self):
  p,s,provider=self.fixture(3);text=repair_prompt(s,'Use the correct pistol.','generate')
  self.assertTrue(text.startswith(s['prompt']));self.assertIn('correct pistol',text)
  self.assertEqual(repair_prompt(s,s['prompt'],'generate'),s['prompt'])
  self.assertEqual(repair_prompt(s,'Correct only the hand.','edit'),'Correct only the hand.')
 def test_actual_repair_dispatch_keeps_the_original_scene_prompt(self):
  f=fixtures.DispatchSettingsTest();f.setUp()
  try:
   p,ch,shot,provider=f.repair_fixture();f.service.visual_check=Mock(return_value={'pass':True,'issues':[],'action':'accept'})
   StudioService.generate(f.service,p,ch['id'],shot['id'],123,{'operation':'generate','budgetedQCRepair':True,'_repairPrompt':'Use the correct pistol.','_collectReview':True})
   request=provider.generateImage.call_args.args[0]
   self.assertIn(shot['prompt'],request['prompt']);self.assertIn('correct pistol',request['prompt'])
   self.assertNotIn('sourceImage',request)
  finally:f.tearDown()

if __name__=='__main__':unittest.main()
