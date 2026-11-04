import copy,tempfile,hashlib,unittest
from pathlib import Path
from scene_repair import planning_context,bind,selected_plan
from studio_data import new_project,character,get_shot
class CorrectiveScene(unittest.TestCase):
 def fixture(self):
  p=new_project();person=character('Mira');p['characters']=[person];p['settings'].update(automaticSceneRestaging=True,qcClothingContinuity='advisory');p['settings']['director']['reasoning']='Balanced'
  s={'id':'s','chapterId':p['chapters'][0]['id'],'prompt':'OLD BAKED PROMPT WITH STALE ACTION','characters':[{'id':person['id'],'type':'main','appearanceState':{'outfit':'red coat'}}],'narrationSegment':'Mira crouches behind the table.','action':'Mira takes cover.','camera':{'shot':'medium'},'manual':{}}
  qc={'pass':False,'issues':['Wrong action.','Wrong cuff.'],'findings':[{'issue':'Wrong action.','severity':'major','clothingOnly':False},{'issue':'Wrong cuff.','severity':'major','clothingOnly':True}]};return p,s,qc
 def test_only_current_scene_and_confirmed_relevant_defects_are_planned(self):
  p,s,qc=self.fixture();before=copy.deepcopy((p,s,qc));context=planning_context(p,s,qc);self.assertEqual(context['currentNarration'],s['narrationSegment']);self.assertEqual(context['confirmedDefects'],['Wrong action.']);self.assertNotIn(s['prompt'],str(context));self.assertEqual((p,s,qc),before)
 def test_plan_binding_requires_same_image_and_spec_and_respects_manual_prompt(self):
  p,s,qc=self.fixture();plan=bind('Mira crouches behind the table.','sha','spec',planning_context(p,s,qc),'gpt-6-luna','Balanced');s['qc']=copy.deepcopy(qc) | {'productionSceneRepair':plan};self.assertEqual(selected_plan(p,s,'spec','sha'),plan['prompt']);self.assertIsNone(selected_plan(p,s,'changed','sha'));self.assertIsNone(selected_plan(p,s,'spec','replaced'));s['manual']['prompt']=True;self.assertIsNone(selected_plan(p,s,'spec','sha'));self.assertIsNone(planning_context(p,s,qc))
 def test_planning_policy_or_current_defects_change_invalidates_old_plan(self):
  p,s,qc=self.fixture();s['qc']=copy.deepcopy(qc);s['qc']['productionSceneRepair']=bind('Corrected scene','sha','spec',planning_context(p,s,qc),'model','Balanced')
  self.assertIsNotNone(selected_plan(p,s,'spec','sha'));p['settings']['qcClothingContinuity']='required';self.assertIsNone(selected_plan(p,s,'spec','sha'));p['settings']['qcClothingContinuity']='advisory';p['settings']['director']['reasoning']='High';self.assertIsNone(selected_plan(p,s,'spec','sha'))
 def test_empty_or_unbounded_plan_is_rejected(self):
  for prompt in ('', 'x'*2601):
   with self.assertRaises(ValueError):bind(prompt,'sha','spec',{},'model','Balanced')
 def test_complete_scene_avoids_duplicate_scene_contract_but_keeps_identity_binding(self):
  from image_provider import reference_prompt
  p,s,qc=self.fixture();p['settings']['sceneConditioning']=True;meta=[{'characterId':p['characters'][0]['id']}];normal=reference_prompt(p,s,meta);short=reference_prompt(p,s,meta,include_scene_facts=False);self.assertIn('Subject-bound current scene facts',normal);self.assertNotIn('Subject-bound current scene facts',short);self.assertIn('Mira',short)
 def test_actual_planning_method_uses_saved_planning_effort_and_restores_qc_effort(self):
  from studio_service import StudioService
  p,s,qc=self.fixture();calls=[]
  class Director:
   reasoning='Fast'
   def call(self,role,context,schema,gate):calls.append((self.reasoning,context));return {'prompt':'Mira crouches behind the table in a red coat.'}
   def stop(self):pass
  service=StudioService.__new__(StudioService);service.director=Director();service.select_director=lambda p:setattr(service.director,'reasoning',p['settings']['director']['reasoning']);service.visual_spec_signature=lambda p,s:'spec';service.gate=lambda *_:None
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'image.png';path.write_bytes(b'image');plan=service.prepare_corrective_scene(p,s,qc,path)
  self.assertEqual(calls[0][0],'Balanced');self.assertEqual(service.director.reasoning,'Fast');self.assertEqual(plan['reasoning'],'Balanced');self.assertEqual(plan['sourceImageSHA256'],hashlib.sha256(b'image').hexdigest())
 def test_actual_generation_uses_bound_complete_prompt_and_retains_original_and_counter(self):
  from full_video_test import FullVideoTest
  from studio_service import StudioService
  from PIL import Image
  from unittest.mock import Mock
  f=FullVideoTest();f.setUp()
  try:
   chapter=f.project['chapters'][0];f.service.analyze(f.project,chapter['id'],{'managedPipeline':True});p=f.service.store.load(f.project['id']);s=p['chapters'][0]['scenes'][0]['shots'][0];old=s['prompt'];path=f.service.store.asset(p['id'],'source.png');Image.new('RGB',(8,8),'blue').save(path);s['imagePath']='source.png';p['settings'].update(automaticSceneRestaging=True,unattendedProduction=True,maxImageRetries=2,qcRepairBudgetMode='project-cap');p['settings']['budget']={'totalUSD':2,'openaiUSD':1,'runpodUSD':1};p['settings']['cloudWindow']={'gpuBudgetUSD':1};spec=f.service.visual_spec_signature(p,s);s.update(automaticRepairSpec=spec,automaticRepairAttempts=1);s['qc']={'pass':False,'action':'regenerate','shotSpecSignature':spec,'issues':['Mira is not carrying the key.'],'findings':[{'issue':'Mira is not carrying the key.','severity':'major'}]};s['qc']['productionSceneRepair']=bind('COMPLETE CORRECTED SCENE',hashlib.sha256(path.read_bytes()).hexdigest(),spec,planning_context(p,s,s['qc']),'model','Balanced');f.service.store.save(p);f.service.providers['native-flux']=Mock();requests=[]
   def generate(request,checkpoint):requests.append(request);return {'pil':Image.new('RGB',(8,8),'red'),'model':'sd15','settings':request['settings']}
   f.service.providers['existing'].generateImage=generate;StudioService.generate(f.service,p,chapter['id'],s['id'],123,{'operation':'generate','budgetedQCRepair':True,'deferQC':True,'_repairPrompt':'Old narrow edit cue'})
   self.assertEqual(requests[0]['prompt'],'COMPLETE CORRECTED SCENE');self.assertNotIn('sourceImage',requests[0]);latest=get_shot(f.service.store.load(p['id']),chapter['id'],s['id']);self.assertEqual(latest['prompt'],old);self.assertEqual(latest['automaticRepairAttempts'],2)
  finally:f.tearDown()
if __name__=='__main__':unittest.main()
