import copy,unittest
from scene_reference_binding import compose
from studio_data import new_project,character
class SceneReferenceBinding(unittest.TestCase):
 def test_short_names_bind_to_the_actual_reference_before_the_later_cast_description(self):
  p,s,refs=self.fixture();p['characters'][0]['name']='Mira Haven';p['characters'][1]['name']='Leon Vale'
  text='Leon grips Mira\'s collar. Characters: Mira Haven / Leon Vale.'
  result=compose(p,s,text,refs)
  self.assertIn('Leon Vale (the person from image 2) grips Mira Haven (the person from image 1)',result)
  self.assertEqual(result.count('Mira Haven (the person'),1)
 def test_full_names_win_and_ambiguous_aliases_and_hyphenated_common_words_stay_unbound(self):
  p,s,refs=self.fixture();p['characters'][0]['name']='Vaan Astra';p['characters'][1]['name']='Astra Elysia'
  result=compose(p,s,'Astra watches Vaan. Astra Elysia turns.',refs)
  self.assertIn('Astra watches Vaan Astra (the person from image 1)',result)
  self.assertIn('Astra Elysia (the person from image 2) turns',result)
  p['characters'][0]['name']='Rose Haven'
  result=compose(p,s,'Rose-colored lighting. rose petals. Rose looks up.',refs)
  self.assertIn('Rose-colored lighting. rose petals. Rose Haven (the person from image 1) looks up.',result)
 def test_automatic_binding_is_opt_in_multi_identity_and_manual_safe(self):
  from scene_reference_binding import bind_automatic
  p,s,refs=self.fixture();text='Leon points at Mira.';before=copy.deepcopy((p,s,refs))
  self.assertIsNone(bind_automatic(p,s,text,refs));p['settings']['automaticReferenceBinding']=True
  self.assertIn('Leon (the person from image 2)',bind_automatic(p,s,text,refs))
  self.assertIsNone(bind_automatic(p,s,text,refs[:1]))
  for key in ('prompt','referenceImages'):
   s['manual']={key:True};self.assertIsNone(bind_automatic(p,s,text,refs))
  self.assertEqual(refs,before[2])
 def test_single_word_full_name_does_not_consume_a_lowercase_common_noun(self):
  p,s,refs=self.fixture();p['characters'][0]['name']='Rose';p['characters'][1]['name']='Leon Vale'
  result=compose(p,s,'A rose lies on the desk. Leon greets Rose.',refs)
  self.assertIn('A rose lies on the desk. Leon Vale (the person from image 2) greets Rose (the person from image 1).',result)
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
