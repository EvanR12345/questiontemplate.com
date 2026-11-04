import copy,unittest
import scene_repair_test
from scene_first_pass import context,bind,selected,required

class FirstPass(unittest.TestCase):
 def fixture(self):
  p,s,q=scene_repair_test.CorrectiveScene().fixture();p['settings']['firstPassScenePlanning']=True;return p,s
 def test_old_actions_and_qc_never_enter_first_pass_context(self):
  p,s=self.fixture();s['qc']={'issues':['Old pixels are wrong']};before=copy.deepcopy((p,s));value=context(p,s)
  self.assertNotIn(s['prompt'],str(value));self.assertNotIn('Old pixels',str(value));self.assertNotIn('confirmedDefects',value);self.assertEqual((p,s),before)
 def test_stale_policy_prompt_and_scene_are_rejected_before_dispatch(self):
  p,s=self.fixture();s['firstPassScenePlan']=bind('Mira crouches under a table.',context(p,s),'model','Balanced');self.assertEqual(required(p,s),'Mira crouches under a table.')
  for target,key,value in [(s,'prompt','Edited original'),(s,'action','New action'),(p['settings'],'qcClothingContinuity','required')]:
   prior=target.get(key);target[key]=value
   with self.assertRaises(ValueError):required(p,s)
   target[key]=prior
 def test_manual_prompt_and_references_bypass_new_planning(self):
  for key in ('prompt','referenceImages'):
   p,s=self.fixture();s['manual'][key]=True;self.assertIsNone(context(p,s));self.assertIsNone(required(p,s))
 def test_complete_plan_is_submitted_on_first_generation_without_overwriting_saved_prompt(self):
  from full_video_test import FullVideoTest
  from studio_service import StudioService
  from studio_data import get_shot
  from PIL import Image
  from unittest.mock import Mock
  f=FullVideoTest();f.setUp()
  try:
   ch=f.project['chapters'][0];f.service.analyze(f.project,ch['id'],{'managedPipeline':True});p=f.service.store.load(f.project['id']);s=p['chapters'][0]['scenes'][0]['shots'][0];old=s['prompt'];p['settings']['firstPassScenePlanning']=True;s['firstPassScenePlan']=bind('ONE CURRENT SCENE',context(p,s),'model','Balanced');f.service.store.save(p);f.service.providers['native-flux']=Mock();requests=[]
   def generate(request,checkpoint):requests.append(copy.deepcopy(request));return {'pil':Image.new('RGB',(8,8),'red'),'settings':request['settings'],'model':'sd15'}
   f.service.providers['existing'].generateImage=generate;StudioService.generate(f.service,p,ch['id'],s['id'],123,{'operation':'generate','deferQC':True,'deferAutomaticRepair':True})
   self.assertEqual(requests[0]['prompt'],'ONE CURRENT SCENE');latest=get_shot(f.service.store.load(p['id']),ch['id'],s['id']);self.assertEqual(latest['prompt'],old);self.assertEqual(latest.get('automaticRepairAttempts',0),0)
  finally:f.tearDown()

 def test_automatic_fresh_repair_keeps_the_complete_first_pass_scene(self):
  from full_video_test import FullVideoTest
  from studio_service import StudioService
  from studio_data import get_shot
  from PIL import Image
  from unittest.mock import Mock
  f=FullVideoTest();f.setUp()
  try:
   ch=f.project['chapters'][0];f.service.analyze(f.project,ch['id'],{'managedPipeline':True});p=f.service.store.load(f.project['id']);s=p['chapters'][0]['scenes'][0]['shots'][0];old=s['prompt'];p['settings'].update(firstPassScenePlanning=True,unattendedProduction=True,maxImageRetries=1,qcRepairBudgetMode='project-cap');p['settings']['budget']={'totalUSD':2,'openaiUSD':1,'runpodUSD':1};p['settings']['cloudWindow']={'gpuBudgetUSD':1};Image.new('RGB',(8,8),'blue').save(f.service.store.asset(p['id'],'bad.png'));s.update(imagePath='bad.png',generationStale=False);spec=f.service.visual_spec_signature(p,s);s['qc']={'pass':False,'action':'regenerate','shotSpecSignature':spec,'issues':['Wrong position.'],'findings':[{'issue':'Wrong position.','severity':'major'}]};s['firstPassScenePlan']=bind('COMPLETE CURRENT SCENE',context(p,s),'model','Balanced');f.service.store.save(p);f.service.providers['native-flux']=Mock();requests=[]
   def generate(request,checkpoint):requests.append(copy.deepcopy(request));return {'pil':Image.new('RGB',(8,8),'red'),'settings':request['settings'],'model':'sd15'}
   f.service.providers['existing'].generateImage=generate;StudioService.generate(f.service,p,ch['id'],s['id'],456,{'operation':'generate','budgetedQCRepair':True,'deferQC':True,'_repairPrompt':'Correct the position.'});self.assertIn('COMPLETE CURRENT SCENE',requests[0]['prompt']);self.assertNotIn(old,requests[0]['prompt']);self.assertEqual(get_shot(f.service.store.load(p['id']),ch['id'],s['id'])['prompt'],old)
  finally:f.tearDown()

 def test_nonoverlap_production_prepares_before_first_gpu_ensure(self):
  from full_video_test import FullVideoTest
  from studio_service import StudioService
  from types import SimpleNamespace
  f=FullVideoTest();f.setUp()
  try:
   ch=f.project['chapters'][0];f.service.analyze(f.project,ch['id'],{'managedPipeline':True});f.service.store.mutate(f.project['id'],lambda p:p['settings'].update(firstPassScenePlanning=True));f.service.select_director=lambda *_:None;events=[]
   f.service.director=SimpleNamespace(reasoning='Fast',model='model',stop=lambda:None,call=lambda *a:(events.append('plan') or {'prompt':'A SINGLE CURRENT SCENE'}))
   def ensure():
    current=f.service.store.load(f.project['id']);shots=[s for sc in current['chapters'][0]['scenes'] for s in sc['shots']];self.assertTrue(all(selected(current,s) for s in shots));events.append('rent')
   f.service.ensure_production_gpu=ensure;StudioService.generate_production_chapter_images(f.service,f.project['id'],ch['id'],0,[ch['id']]);self.assertLess(events.index('plan'),events.index('rent'))
  finally:f.tearDown()

 def test_queued_replacement_prepares_and_refreshes_frozen_input_before_rental(self):
  from full_video_test import FullVideoTest
  from studio_service import StudioService
  from studio_data import get_shot
  from types import SimpleNamespace
  from unittest.mock import Mock,patch
  from PIL import Image
  f=FullVideoTest();f.setUp()
  try:
   ch=f.project['chapters'][0];f.service.analyze(f.project,ch['id'],{'managedPipeline':True});p=f.service.store.load(f.project['id']);s=p['chapters'][0]['scenes'][0]['shots'][0];p['settings']['firstPassScenePlanning']=True;s.update(imageProvider='comfyui',imagePath='old.png',generationStale=False);f.service.store.save(p);Image.new('RGB',(8,8),'blue').save(f.service.store.asset(p['id'],'old.png'));f.service.select_director=lambda *_:None;calls=[]
   f.service.director=SimpleNamespace(reasoning='Fast',model='model',stop=lambda:None,call=lambda *a:(calls.append('plan') or {'prompt':'A SINGLE NEW SCENE'}))
   provider=Mock(id='comfyui');provider.getCapabilities.return_value={'maxReferenceImages':0};f.service.providers['comfyui']=provider
   options={'shotSnapshot':copy.deepcopy(s),'operation':'generate','deferQC':True,'deferAutomaticRepair':True};rental=Mock();rental.ensure.side_effect=lambda:calls.append('rent');job={'project':p['id'],'chapter':ch['id'],'shot':s['id']}
   with patch('studio_gpu_broker.gpu_configured',return_value=True),patch('studio_gpu_broker.GPUReservation',return_value=rental),patch('studio_service.require_shot',return_value={'status':'PASSED'}):
    fresh=StudioService.begin_queued_image_rental(f.service,p,job,options)
    self.assertEqual(calls,['plan','rent']);self.assertEqual(required(fresh,options['shotSnapshot']),'A SINGLE NEW SCENE');self.assertEqual(options['shotSnapshot']['prompt'],s['prompt']);self.assertEqual(required(fresh,get_shot(fresh,ch['id'],s['id'])),'A SINGLE NEW SCENE')
  finally:f.tearDown()

 def test_atomic_plan_completion_skips_duplicate_dispatch_and_unknown_remains_fenced(self):
  from full_video_test import FullVideoTest
  from studio_data import get_shot
  from types import SimpleNamespace
  from unittest.mock import Mock
  f=FullVideoTest();f.setUp()
  try:
   ch=f.project['chapters'][0];f.service.analyze(f.project,ch['id'],{'managedPipeline':True});p=f.service.store.load(f.project['id']);s=p['chapters'][0]['scenes'][0]['shots'][0];p['settings']['firstPassScenePlanning']=True;f.service.store.save(p);f.service.select_director=Mock();f.service.director=SimpleNamespace(reasoning='Fast',model='model',stop=lambda:None,call=Mock());mutate=f.service.store.mutate;inserted=[False]
   def raced(pid,callback,**kwargs):
    if not inserted[0]:
     inserted[0]=True
     def complete(q):
      current=get_shot(q,ch['id'],s['id']);current['firstPassScenePlan']=bind('Completed by the other caller.',context(q,current),'model','Balanced');current['firstPassScenePlanIntent']={'status':'COMPLETE'}
     mutate(pid,complete)
    return mutate(pid,callback,**kwargs)
   f.service.store.mutate=raced;result=f.service.prepare_first_pass_scenes(p['id'],ch['id'],s['id']);f.service.director.call.assert_not_called();self.assertEqual(required(result,get_shot(result,ch['id'],s['id'])),'Completed by the other caller.')
   mutate(p['id'],lambda q:get_shot(q,ch['id'],s['id']).update(firstPassScenePlanIntent={'status':'SUBMITTING_UNKNOWN'}))
   with self.assertRaisesRegex(RuntimeError,'reconciliation'):f.service.prepare_first_pass_scenes(p['id'],ch['id'],s['id'])
  finally:f.tearDown()

if __name__=='__main__':unittest.main()
