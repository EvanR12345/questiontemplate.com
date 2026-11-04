"""Automatic correction must not anchor a confirmed wrong scene to its own pixels."""
import unittest,copy
from studio_data import new_project
from studio_automation import repair_operation
class AutomaticSceneRebuild(unittest.TestCase):
 def fixture(self,qc):
  p=new_project('Repair');p['settings'].update(sceneConditioning=False,qcClothingContinuity='advisory')
  qc=copy.deepcopy(qc);qc['shotSpecSignature']='same-spec'
  s={'automaticRepairAttempts':1,'automaticRepairSpec':'same-spec','id':'s','chapterId':p['chapters'][0]['id'],'imageModel':'flux2-klein-4b','characters':[],'qc':qc,'manual':{}}
  class Provider:
   id='comfyui'
   def getModelCapabilities(self,model):return {'supportsImageEditing':True,'maxReferenceImages':3}
  return p,s,Provider()
 def test_confirmed_wrong_action_regenerates_without_bad_source_anchor(self):
  qc={'pass':False,'action':'image_edit','issues':['The free captor, not the prisoner, uses the weapon.'],'findings':[{'issue':'The free captor, not the prisoner, uses the weapon.','severity':'major','clothingOnly':False}]};p,s,provider=self.fixture(qc);before=copy.deepcopy((p,s));self.assertEqual(repair_operation(p,s,provider),'generate');self.assertEqual((p,s),before)
 def test_critical_free_hand_regenerates_with_reference_candidate_disabled(self):
  p,s,provider=self.fixture({'pass':True,'action':'pass','criticalStoryBlockers':[{'code':'FREE_BOUND_HAND','issue':'A tied prisoner visibly gestures freely.'}]});self.assertEqual(repair_operation(p,s,provider),'generate')
 def test_localized_or_tolerated_clothing_retains_edit_capability(self):
  for severity,clothing in [('advisory',False),('major',True)]:
   p,s,provider=self.fixture({'pass':False,'action':'image_edit','issues':['A small garment detail differs.'],'findings':[{'issue':'A small garment detail differs.','severity':severity,'clothingOnly':clothing}]});self.assertEqual(repair_operation(p,s,provider),'edit')
 def test_first_or_stale_failure_keeps_local_edit(self):
  for previous,spec in ((0,'same-spec'),(1,'old-spec')):
   p,s,provider=self.fixture({'pass':False,'action':'image_edit','issues':['Wrong action.'],'findings':[{'issue':'Wrong action.','severity':'major','clothingOnly':False}]});s['automaticRepairAttempts']=previous;s['automaticRepairSpec']=spec;self.assertEqual(repair_operation(p,s,provider),'edit')
 def test_explicit_regeneration_recommendation_is_respected(self):
  p,s,provider=self.fixture({'pass':False,'action':'regenerate','issues':[],'findings':[]});self.assertEqual(repair_operation(p,s,provider),'generate')
if __name__=='__main__':unittest.main()
