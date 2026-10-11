import unittest
from studio_video_settings import capture

class ReceiptTests(unittest.TestCase):
 def test_actual_generated_settings_and_voice_win_over_later_project_choices(self):
  p={'settings':{'image':{'model':'new-choice','steps':20},'voice':'new-voice','budget':{'totalUSD':500},'director':{'apiKey':'secret'},'video':{'fps':30}},
     'chapters':[{'audio':{'voice':'am_michael','speed':1,'narrationDelivery':'cinematic','path':'private.wav'},'scenes':[{'shots':[{'generationSettings':{'steps':4,'guidance':1},'imageMetadata':{'model':'flux2-klein-4b','resolution':[1280,720],'requestedWorkflow':'auto','prompt':'private story','referenceImages':['private.png']}}]}]}]}
  r=capture(p);self.assertEqual(r['settings']['image']['steps'],4);self.assertEqual(r['settings']['image']['model'],'flux2-klein-4b');self.assertEqual(r['settings']['voice'],'am_michael')
  for forbidden in ('secret','private story','private.png','private.wav','budget'):self.assertNotIn(forbidden,str(r))
  self.assertNotIn('apiKey',r['settings']['director'])
 def test_mixed_models_or_voices_are_not_claimed_to_be_one_actual_recipe(self):
  p={'settings':{'image':{'model':'configured'}},'chapters':[{'audio':{'voice':'a'},'scenes':[{'shots':[{'imageMetadata':{'model':'first'}},{'imageMetadata':{'model':'second'}}]}]},{'audio':{'voice':'b'},'scenes':[]}]}
  r=capture(p);self.assertTrue(r['mixedImageSettings']);self.assertTrue(r['mixedVoices']);self.assertEqual(r['settings']['image']['model'],'configured')
 def test_receipt_is_detached_and_chapter_scope_excludes_other_chapters(self):
  ch={'audio':{'voice':'a'},'scenes':[]};p={'settings':{'video':{'fps':30}},'chapters':[ch,{'audio':{'voice':'b'},'scenes':[]}]}
  r=capture(p,ch);r['settings']['video']['fps']=60;self.assertEqual(p['settings']['video']['fps'],30);self.assertFalse(r['mixedVoices'])

if __name__=='__main__':unittest.main()
