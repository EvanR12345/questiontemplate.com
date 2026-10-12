"""Cache/forecast and intro publication fixtures; all synthesis is fake."""
import copy,tempfile,unittest
from unittest.mock import Mock,patch
from studio_service import StudioService
from studio_data import ProjectStore,new_project
import full_video_test as fixtures

class NarrationCacheTest(unittest.TestCase):
 def setUp(self):
  self.f=fixtures.FullVideoTest();self.f.setUp();self.s=self.f.service
  self.p=self.s.store.load(self.f.project['id']);self.c=self.p['chapters'][0]
  self.path='saved.wav';self.s.store.asset(self.p['id'],self.path).write_bytes(b'audio')
  self.c['audio']={'path':self.path,'duration':4,'signature':self.s.narration_signature(self.p,self.c)}
  self.p=self.s.store.save(self.p);self.c=self.p['chapters'][0]
 def tearDown(self):self.f.tearDown()
 def test_cached_local_and_cloud_audio_need_no_materialization(self):
  with patch.object(self.s.store,'asset',side_effect=AssertionError('Unneeded restore')):
   self.assertTrue(self.s.saved_narration_ready(self.p,self.c));StudioService.narration(self.s,self.p,self.c)
  self.s.store.asset_local(self.p['id'],self.path).unlink()
  with patch.object(self.s.store,'has_asset',return_value=True),patch.object(self.s.store,'asset',side_effect=AssertionError('Restore')):
   self.assertTrue(self.s.saved_narration_ready(self.p,self.c))
 def test_all_speech_inputs_invalidate_matching_audio(self):
  for key,value in [('voice','bm_george'),('speed',1.2),('soundEffects','off'),('narrationDelivery','cinematic')]:
   p=copy.deepcopy(self.p);p['settings'][key]=value
   self.assertFalse(self.s.saved_narration_ready(p,p['chapters'][0]),key)
  p=copy.deepcopy(self.p);p['chapters'][0]['sourceText']='A new story.'
  self.assertFalse(self.s.saved_narration_ready(p,p['chapters'][0]))
  p['chapters'][0].update(narrationMode='manual',cleanNarrationText='Explicit manual script.')
  self.assertFalse(self.s.saved_narration_ready(p,p['chapters'][0]))
 def test_saved_analysis_does_not_hide_pending_audio_after_voice_changes(self):
  self.s.store.mutate(self.p['id'],lambda p:(p['settings'].update(voice='bm_george'),p['chapters'][0].update(scenes=[{'id':'sc-cache-fixture','shots':[]}])) )
  self.s.analysis_is_current=Mock(return_value=True)
  self.s.performance.estimate=Mock(return_value={'seconds':.01,'rangeSeconds':[.01,.01],'costUSD':0,'status':'MATCHED'})
  r=self.s.performance_report(self.p['id'])
  self.assertTrue(any(x['stage']=='Narration' for x in r['forecast']['profiles']))
 def test_stale_review_forecast_counts_checking_saved_art_not_regeneration(self):
  from studio_qc import review_settings_signature
  p=copy.deepcopy(self.p);p['chapters']=p['chapters'][:1];ch=p['chapters'][0]
  old=copy.deepcopy(p['settings']);old['qcCheckLevel']='practical';p['settings']['qcCheckLevel']='strict'
  shot={'id':'review-fixture','chapterId':ch['id'],'characters':[],'imagePath':'art.png','qc':{'pass':True,'reviewSettingsSignature':review_settings_signature(old)}}
  ch['scenes']=[{'id':'scene-review','shots':[shot]}];self.s.store.asset(p['id'],'art.png').write_bytes(b'preserved art')
  self.s.saved_image_ready=Mock(return_value=False)
  self.s.performance.estimate=Mock(return_value={'seconds':6,'rangeSeconds':[6,6],'costUSD':.0003,'status':'MATCHED'})
  report=self.s.performance_forecast(p,{ch['id']})
  self.assertEqual([r['stage'] for r in report['profiles']],['Visual QC'])
  self.assertEqual(report['readyImageSeconds'],6)

 def test_forecast_counts_stale_file_using_current_cleaned_text(self):
  p=copy.deepcopy(self.p);p['chapters'][0].update(sourceText='Chapter 1\n\nA fresh story.',cleanNarrationText='old');p['chapters']=p['chapters'][:1]
  self.s.performance.estimate=Mock(return_value={'seconds':.01,'rangeSeconds':[.01,.01],'costUSD':0,'status':'MATCHED'})
  r=self.s.performance_forecast(p);row=next(x for x in r['profiles'] if x['stage']=='Narration')
  self.assertEqual(row['units']['value'],len('A fresh story.'));self.assertGreater(r['planningSeconds'],0)

class IntroPublicationTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.s=StudioService.__new__(StudioService);self.s.store=ProjectStore(self.tmp.name)
  p=new_project();p['intro'].update(voiceText='Deliberate hook.',audioPath='intro.wav');self.p=self.s.store.save(p)
  self.old=self.s.store.asset(self.p['id'],'intro.wav');self.old.write_bytes(b'original master')
  def synth(text,voice,speed,target,*args):
   target.write_bytes(b'new master');return {'duration':12,'sentences':[]}
  self.s.create_audio=Mock(side_effect=synth)
 def tearDown(self):self.tmp.cleanup()
 def test_explicit_regeneration_is_versioned_with_originals_preserved(self):
  self.s.intro_audio(self.p);a=self.s.store.load(self.p['id']);self.s.intro_audio(a);b=self.s.store.load(self.p['id'])
  self.assertNotEqual(a['intro']['audioPath'],b['intro']['audioPath']);self.assertEqual(self.old.read_bytes(),b'original master')
  self.assertTrue(self.s.store.asset(self.p['id'],a['intro']['audioPath']).is_file())
  self.assertEqual(b['intro']['audioHistory'][-1]['audioPath'],a['intro']['audioPath'])
 def test_script_or_manual_selection_changed_mid_synthesis_is_not_overwritten(self):
  original=self.s.create_audio.side_effect
  def edited(*args):
   result=original(*args);self.s.store.asset(self.p['id'],'manual.wav').write_bytes(b'manual')
   self.s.store.mutate(self.p['id'],lambda p:p['intro'].update(voiceText='New manual hook.',audioPath='manual.wav'));return result
  self.s.create_audio.side_effect=edited
  with self.assertRaisesRegex(ValueError,'retained'):self.s.intro_audio(self.p)
  intro=self.s.store.load(self.p['id'])['intro'];self.assertEqual(intro['voiceText'],'New manual hook.')
  self.assertEqual(intro['audioPath'],'manual.wav');self.assertFalse(intro['audioHistory'][-1]['selected'])
 def test_voice_change_makes_intro_not_ready_even_when_script_and_file_stay_the_same(self):
  self.s.intro_audio(self.p);p=self.s.store.load(self.p['id'])
  self.assertTrue(self.s.saved_intro_audio_ready(p))
  p['settings']['voice']='bm_george'
  self.assertFalse(self.s.saved_intro_audio_ready(p))
 def test_delivery_flow_change_invalidates_intro_cache(self):
  before=self.s.intro_audio_signature(self.p)
  with patch('studio_service.FLOW_VERSION',999):self.assertNotEqual(before,self.s.intro_audio_signature(self.p))

if __name__=='__main__':unittest.main()
