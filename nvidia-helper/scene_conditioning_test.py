"""Public-contract regressions: reference clothing, subject binding and unsafe edits."""
import copy,tempfile,unittest
from pathlib import Path
from PIL import Image
from studio_data import ProjectStore,new_project,character
from scene_conditioning import prepare_identity_regions,conditioned_reference,scene_contract,structural_repair,critical_story_blockers,scene_facts

class SceneConditioning(unittest.TestCase):
 def test_visible_role_contradiction_blocks_even_if_model_calls_it_advisory(self):
  from studio_qc import decision
  p,s,a,b=self.fixture();facts=scene_facts(p,s);observations=[{'subjectId':a['id'],'restraint':'visible','hands':'not_visible','body':'connected'},{'subjectId':b['id'],'restraint':'visible','hands':'not_visible','body':'connected'}]
  blockers=critical_story_blockers(facts,observations)
  self.assertEqual([x['code'] for x in blockers],['RESTRAINT_TRANSFER'])
  raw={'pass':False,'issues':['Jonathan is tied around the waist.'],'action':'review','findings':[{'issue':'Jonathan is tied around the waist.','severity':'advisory'}]};before=copy.deepcopy(raw);s.update(imagePath='test.png',qc=raw|{'criticalStoryBlockers':blockers});p['settings'].update(unattendedProduction=True,qcPolicy='practical')
  self.assertTrue(decision(p,s,None)['blocking']);self.assertEqual(raw,before)
 def test_occlusion_and_ankle_only_restraints_do_not_invent_hand_violations(self):
  facts=[{'subjectId':'a','name':'A','appearance':{'restraint':'ankles tied'}}]
  obs=[{'subjectId':'a','restraint':'visible','hands':'visibly_free','body':'connected'}]
  self.assertEqual(critical_story_blockers(facts,obs),[])
  facts[0]['appearance']['restraint']='wrists tied behind back';obs[0]['hands']='not_visible';self.assertEqual(critical_story_blockers(facts,obs),[])
  facts[0]['appearance']['restraint']='left wrist tied to chair';obs[0]['hands']='visibly_free';self.assertEqual(critical_story_blockers(facts,obs),[])
 def test_complex_review_has_full_detail_without_increasing_effort_or_identity_images(self):
  from openai_director import review_image_details
  context={'expectedShot':{'subjectBoundFacts':[{'appearance':{'restraint':'wrists tied'}},{'appearance':{}}]}}
  self.assertEqual(review_image_details('Visual quality reviewer:',context,3),['high','low','low'])
  self.assertEqual(review_image_details('Identity reference region locator:',context,1),['low'])
  self.assertEqual(review_image_details('Visual quality reviewer:',{},3),['low','low','low'])
 def fixture(self):
  project=new_project('Scene');a,b=character('Christopher'),character('Jonathan')
  a['defaultAppearance']['outfit']='dark jacket';b['defaultAppearance']['outfit']='white suit'
  a['references']=[{'path':'references/a.png','kind':'face'}];b['references']=[{'path':'references/b.png','kind':'face'}];project['characters']=[a,b]
  shot={'chapterId':project['chapters'][0]['id'],'characters':[{'id':a['id'],'type':'main','appearanceState':{'outfit':'bare shoulders','restraint':'wrists tied behind his back'}},{'id':b['id'],'type':'main','appearanceState':{'outfit':'white suit'}}],'manual':{},'action':'Jonathan points at Christopher.','camera':{'shot':'medium','angle':'eye level','composition':'Jonathan behind Christopher.'}}
  return project,shot,a,b
 def test_only_named_prisoner_gets_restraints_and_all_cast_is_named(self):
  p,s,a,b=self.fixture();ch=p['chapters'][0];ch['people']=[{'id':'woman','name':'Wine woman','description':'adult woman','type':'temporary'}];s['characters'].append({'id':'woman','type':'temporary','appearanceState':{'glass of wine':'held in her hand'}})
  text=scene_contract(p,s)
  self.assertIn('Christopher: outfit=bare shoulders; restraint=wrists tied behind his back',text)
  self.assertIn('Jonathan: outfit=white suit',text);self.assertIn('Wine woman',text);self.assertIn('Only Christopher has specified restraints',text)
  self.assertNotIn(a['id'],text);self.assertNotIn('Jonathan: outfit=bare',text)
 def test_crops_preserve_source_pixels_and_source_assets_and_are_reused(self):
  p,s,a,b=self.fixture();calls=[]
  class Vision:
   def locateIdentityRegion(self,context,gate):
    calls.append(context);return {'usable':True,'confidence':'high','box':[250,0,750,500],'reason':'One complete head including beard.'}
  with tempfile.TemporaryDirectory() as root:
   store=ProjectStore(root);store.save(p)
   for person in (a,b):
    path=store.asset(p['id'],person['references'][0]['path']);path.parent.mkdir(exist_ok=True);Image.new('RGB',(200,200),'red').save(path)
   before=copy.deepcopy((p,s));source=store.asset(p['id'],a['references'][0]['path']).read_bytes()
   prepare_identity_regions(p,s,store,Vision(),lambda *a:None)
   result,meta=conditioned_reference(p,a['references'][0],store)
   self.assertTrue(result.startswith('data:image/png;base64,'));self.assertEqual(meta['identityRegion']['sourceSHA256'],__import__('hashlib').sha256(source).hexdigest())
   self.assertEqual(meta['identityRegion']['pixelBox'],[50,0,150,100]);self.assertEqual(meta['identityRegion']['size'],[100,100]);self.assertEqual(store.asset(p['id'],a['references'][0]['path']).read_bytes(),source)
   prepare_identity_regions(p,s,store,Vision(),lambda *a:None);self.assertEqual(len(calls),1) # equal source bytes share region, not subject state
   self.assertEqual((p,s),before)
 def test_uncertain_or_invalid_region_retains_original_without_guessing(self):
  p,s,a,b=self.fixture()
  class Vision:
   def locateIdentityRegion(self,c,g):return {'usable':True,'confidence':'high','box':[500,0,400,1000],'reason':'bad coordinates'}
  with tempfile.TemporaryDirectory() as root:
   store=ProjectStore(root);store.save(p)
   for person in (a,b):
    path=store.asset(p['id'],person['references'][0]['path']);path.parent.mkdir(exist_ok=True);Image.new('RGB',(200,200),'red').save(path)
   prepare_identity_regions(p,s,store,Vision(),lambda *a:None);_,meta=conditioned_reference(p,a['references'][0],store)
   self.assertNotIn('identityRegion',meta)
 def test_manual_prompts_and_reference_choices_are_not_prepared(self):
  p,s,_,_=self.fixture()
  class Never:
   def locateIdentityRegion(self,*a):raise AssertionError('Manual reference changed')
  for flag in ('prompt','referenceImages'):
   s['manual']={flag:True};prepare_identity_regions(p,s,None,Never(),lambda *a:None)
 def test_structural_repair_does_not_reuse_broken_anatomy(self):
  self.assertTrue(structural_repair({'issues':['Two fused bodies share a torso.'],'findings':[{'issue':'Two fused bodies share a torso.','severity':'major'}]}))
  self.assertFalse(structural_repair({'issues':['Tiny scar unclear.'],'findings':[{'issue':'Tiny scar unclear.','severity':'uncertain'}]}))
  self.assertFalse(structural_repair({'issues':['No fused bodies; only a missing small scar.'],'findings':[{'issue':'No fused bodies; only a missing small scar.','severity':'advisory'}]}))
 def test_a_verified_free_bound_hand_requires_new_geometry(self):
  self.assertTrue(structural_repair({'criticalStoryBlockers':[{'code':'FREE_BOUND_HAND','issue':'A visibly gestures with a free hand despite both wrists tied.'}]}))
  self.assertFalse(structural_repair({'findings':[{'issue':'Hands are behind the body; the knot cannot be inspected.','severity':'uncertain'}]}))
 def test_concurrent_jobs_prepare_identical_region_once(self):
  from concurrent.futures import ThreadPoolExecutor
  import time
  p,s,a,b=self.fixture();calls=[]
  class Vision:
   def locateIdentityRegion(self,c,g):
    calls.append(1);time.sleep(.08);return {'usable':True,'confidence':'high','box':[250,0,750,500],'reason':'one head'}
  with tempfile.TemporaryDirectory() as root:
   store=ProjectStore(root);store.save(p)
   for person in (a,b):
    path=store.asset(p['id'],person['references'][0]['path']);path.parent.mkdir(exist_ok=True);Image.new('RGB',(200,200),'red').save(path)
   with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda _:prepare_identity_regions(p,s,store,Vision(),lambda *a:None),range(4)))
   self.assertEqual(len(calls),1)
 def test_interrupted_region_request_is_not_replayed(self):
  p,s,a,b=self.fixture();calls=[]
  class Vision:
   def locateIdentityRegion(self,c,g):calls.append(1);raise RuntimeError('unresolved model request')
  with tempfile.TemporaryDirectory() as root:
   store=ProjectStore(root);store.save(p)
   path=store.asset(p['id'],a['references'][0]['path']);path.parent.mkdir(exist_ok=True);Image.new('RGB',(200,200),'red').save(path)
   for _ in range(2):
    with self.assertRaises(RuntimeError):prepare_identity_regions(p,s,store,Vision(),lambda *a:None)
   self.assertEqual(len(calls),1)
 def test_reference_edit_during_vision_cannot_poison_original_digest_crop(self):
  p,s,a,b=self.fixture()
  with tempfile.TemporaryDirectory() as root:
   store=ProjectStore(root);store.save(p);source=store.asset(p['id'],a['references'][0]['path']);source.parent.mkdir(exist_ok=True);Image.new('RGB',(200,200),'red').save(source)
   class Vision:
    def locateIdentityRegion(self,c,g):
     Image.new('RGB',(200,200),'blue').save(source)
     return {'usable':True,'confidence':'high','box':[250,0,750,500],'reason':'one head'}
   prepare_identity_regions(p,s,store,Vision(),lambda *a:None)
   crop=next((store.folder(p['id'])/'reference-conditioning').glob('*.png'))
   with Image.open(crop) as image:self.assertEqual(image.getpixel((0,0)),(255,0,0))
   with Image.open(source) as image:self.assertEqual(image.getpixel((0,0)),(0,0,255))

if __name__=='__main__':unittest.main()
