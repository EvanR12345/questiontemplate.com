import unittest,copy
from body_topology import context_for,attach
class Topology(unittest.TestCase):
 def test_only_crowded_focal_cast_uses_one_generated_image(self):
  facts=[{'subjectId':str(i),'name':str(i),'description':''} for i in range(3)];c={'expectedShot':{'subjectBoundFacts':facts,'camera':{'shot':'wide'}},'_images':['generated','identity-a','identity-b']};before=copy.deepcopy(c);out=context_for(c)
  self.assertEqual(out['_images'],['generated']);self.assertEqual(c,before);self.assertEqual(out['expectedShot']['subjectBoundFacts'],facts)
  c['expectedShot']['subjectBoundFacts']=facts[:2];self.assertIsNone(context_for(c))
 def test_confirmed_layout_blocks_without_rewriting_original_findings(self):
  from studio_qc import decision
  from scene_conditioning import structural_repair
  from studio_data import new_project
  raw={'pass':True,'issues':[],'findings':[],'repairPrompt':'','action':'pass'};before=copy.deepcopy(raw);result=copy.deepcopy(raw);attach(result,{'assessment':'confirmed_defect','visibleHeadCount':3,'evidence':['Bare legs merge into a different suited torso.'],'repairPrompt':'Give the lap woman her own connected torso and head.'})
  self.assertEqual({k:result[k] for k in before},before);self.assertTrue(structural_repair(result));p=new_project('Layout');p['settings']['unattendedProduction']=True;s={'id':'test','imagePath':'a.png','qc':result};self.assertTrue(decision(p,s,None)['blocking'])
 def test_clear_or_ambiguous_does_not_invent_a_blocker(self):
  for assessment in ('clear','uncertain'):
   result={'pass':True,'issues':[]};attach(result,{'assessment':assessment,'visibleHeadCount':2,'evidence':['Lower body is genuinely occluded.'],'repairPrompt':''});self.assertNotIn('criticalStoryBlockers',result);self.assertTrue(result['pass'])
 def test_empty_defect_fails_closed(self):
  with self.assertRaises(ValueError):attach({'pass':True},{'assessment':'confirmed_defect','visibleHeadCount':2,'evidence':[],'repairPrompt':''})
 def test_public_director_keeps_secondary_evidence_and_same_call_contract(self):
  from director_provider import DirectorProvider
  class Fake(DirectorProvider):
   vision_available=True
   def call(self,role,context,schema,gate,vision=False):
    calls.append((role,context,vision))
    if len(calls)==1:return {'pass':True,'issues':[],'findings':[],'repairPrompt':'','action':'pass','subjectObservations':[{'subjectId':str(i),'restraint':'not_visible','hands':'not_visible','body':'connected'} for i in range(3)]}
    return {'assessment':'confirmed_defect','visibleHeadCount':2,'evidence':['A visible body has no connected head.'],'repairPrompt':'Restore a separate complete person.'}
  calls=[];facts=[{'subjectId':str(i),'name':str(i),'appearance':{}} for i in range(3)];result=Fake().inspectGeneratedImage({'expectedShot':{'subjectBoundFacts':facts},'_images':['source','face1','face2']},lambda *_:None)
  self.assertEqual(len(calls),2);self.assertEqual(calls[1][1]['_images'],['source']);self.assertTrue(result['pass']);self.assertTrue(result['criticalStoryBlockers']);self.assertEqual(result['issues'],[])
if __name__=='__main__':unittest.main()
