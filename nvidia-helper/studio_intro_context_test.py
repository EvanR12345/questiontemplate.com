import tempfile, unittest
from types import SimpleNamespace
from PIL import Image
from studio_data import new_project,character,ProjectStore,shot_chapter
from studio_render_inputs import visual_spec_signature
from image_provider import select_references,reference_prompt

class IntroContextTest(unittest.TestCase):
 def test_intro_keeps_identity_references_after_its_original_chapter_is_removed(self):
  with tempfile.TemporaryDirectory() as root:
   p=new_project();store=ProjectStore(root);person=character('Jonathan')
   person['references']=[{'path':'face.png','kind':'face'}];p['characters']=[person]
   shot={'id':'intro-owned','chapterId':'removed-chapter','characters':[{'id':person['id'],'type':'main','appearanceState':{}}],
         'manual':{},'imageProvider':'comfyui','camera':{'angle':'front','shot':'close-up'},'prompt':'Jonathan enters the moonlit hall.'}
   p['intro']['shots']=[shot];p=store.save(p);Image.new('RGB',(16,16),'blue').save(store.asset(p['id'],'face.png'))
   provider=SimpleNamespace(getCapabilities=lambda:{'maxReferenceImages':3})
   refs,meta=select_references(p,shot,store,provider)
   self.assertEqual(len(refs),1);self.assertEqual(meta[0]['characterId'],person['id'])
   self.assertIn('Jonathan',reference_prompt(p,shot,meta))
   self.assertIsNone(shot_chapter(p,shot));self.assertTrue(visual_spec_signature(p,shot,store))
 def test_a_non_intro_shot_with_a_missing_chapter_still_fails(self):
  with self.assertRaises(StopIteration):shot_chapter(new_project(),{'id':'normal','chapterId':'missing'})

if __name__=='__main__':unittest.main()
