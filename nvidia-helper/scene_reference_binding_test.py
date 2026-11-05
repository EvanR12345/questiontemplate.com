import copy,unittest
from scene_reference_binding import compose
from studio_data import new_project,character
class SceneReferenceBinding(unittest.TestCase):
 def fixture(self):
  p=new_project();a=character('Mira');b=character('Leon');p['characters']=[a,b];s={'chapterId':p['chapters'][0]['id'],'characters':[{'id':a['id'],'appearanceState':{}},{'id':b['id'],'appearanceState':{}}],'manual':{},'prompt':'Original saved prose'};return p,s,[{'characterId':a['id']},{'characterId':b['id']}]
 def test_names_bind_to_actual_image_slots_even_when_narrative_order_is_reversed(self):
  p,s,refs=self.fixture();before=copy.deepcopy((p,s,refs));prompt=compose(p,s,'Leon points while Mira remains bound.',refs);self.assertIn('Leon (the person from image 2) points',prompt);self.assertIn('Mira (the person from image 1) remains bound',prompt);self.assertTrue(prompt.startswith('Use image 1'));self.assertEqual((p,s,refs),before)
 def test_location_slot_and_repeated_identity_do_not_shift_or_duplicate_binding(self):
  p,s,refs=self.fixture();refs=[{'locationId':'room'},refs[0],refs[0],refs[1]];prompt=compose(p,s,'Mira turns toward Leon. Mira stays seated.',refs);self.assertIn('Mira (the person from image 2)',prompt);self.assertIn('Leon (the person from image 4)',prompt);self.assertEqual(prompt.count('Mira (the person'),1)
 def test_manual_fields_bypass_and_no_reference_plan_remains_unchanged(self):
  p,s,refs=self.fixture()
  for key in ('prompt','referenceImages'):
   s['manual']={key:True};self.assertEqual(compose(p,s,'My exact prompt',refs),'My exact prompt')
  s['manual']={};self.assertEqual(compose(p,s,'Mira stays seated.',[]),'Mira stays seated.')
 def test_portrait_order_can_match_narration_without_losing_references_or_manual_choices(self):
  from scene_reference_binding import ordered_references
  p,s,refs=self.fixture();data=['Mira bytes','Leon bytes'];before=copy.deepcopy((p,s,data,refs));self.assertEqual(ordered_references(p,s,'Leon addresses Mira.',data,refs),(data,refs));p['settings']['narrativeReferenceOrder']=True
  images,metadata=ordered_references(p,s,'Leon addresses Mira.',data,refs);self.assertEqual(images,['Leon bytes','Mira bytes']);self.assertEqual(metadata,[refs[1],refs[0]]);self.assertIn('Leon (the person from image 1)',compose(p,s,'Leon addresses Mira.',metadata))
  for key in ('prompt','referenceImages'):
   s['manual']={key:True};self.assertEqual(ordered_references(p,s,'Leon addresses Mira.',data,refs),(data,refs))
  self.assertEqual(data,before[2]);self.assertEqual(refs,before[3])
if __name__=='__main__':unittest.main()
