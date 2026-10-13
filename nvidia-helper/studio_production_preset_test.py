import copy,unittest
from studio_data import new_project
from studio_production_preset import recommended_settings

class ProductionPresetTest(unittest.TestCase):
 def test_recommended_director_preserves_voice_model_cadence_and_caps(self):
  p=new_project();s=p['settings'];s['budget']={'openaiUSD':1};s['image']['provider']='comfyui';s['director'].update(provider='openai-luna',visualPacking='large',compactCuts=True,sourceVisualOverlap=True,cadencePerMinute=6)
  before=copy.deepcopy(s);result=recommended_settings(s)
  self.assertEqual(result['director']['visualPacking'],'small');self.assertFalse(result['director']['compactCuts']);self.assertFalse(result['director']['sourceVisualOverlap'])
  self.assertEqual(result['director']['parallelism'],4);self.assertEqual(result['director']['cadencePerMinute'],6)
  for field in ('image','voice','speed','style','customLayout','budget'):self.assertEqual(before[field],result[field])
  self.assertEqual(s,before)
 def test_explicit_visual_checks_off_is_preserved(self):
  s=new_project()['settings'];s['qcCheckLevel']='off'
  self.assertFalse(recommended_settings(s)['automaticRepair'])

if __name__=='__main__':unittest.main()
