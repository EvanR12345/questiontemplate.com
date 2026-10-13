import copy,json,tempfile,unittest
from pathlib import Path
from studio_data import new_project
from studio_planner_profile import planner_profile
from studio_performance import PerformanceStore,profile_for
from studio_qc import check_level

class PlannerProfileTest(unittest.TestCase):
    def test_configured_verified_quote_is_public_but_resource_identifiers_are_not(self):
        p=new_project();p['settings']['image']['provider']='comfyui'
        cfg={'runpodRequiredGPUType':'NVIDIA GeForce RTX 5090','runpodManagedPodId':'private-pod'}
        lease={'podId':'private-pod','workerCopies':2,'workersVerified':True,'hourlyUSD':.69,'started':100,'privateDetail':'secret'}
        result=planner_profile(p,config=cfg,rental=lease)
        self.assertEqual(result['imageExecution']['computeQuote']['hourlyUSD'],.69)
        self.assertNotIn('private-pod',json.dumps(result));self.assertNotIn('secret',json.dumps(result))
        for patch in ({'podId':'different-pod'},{'workersVerified':False},{'hourlyUSD':float('nan')}):
            self.assertIsNone(planner_profile(p,config=cfg,rental=lease|patch)['imageExecution']['computeQuote'])
    def test_new_projects_check_all_images_but_do_not_auto_repair(self):
        p=new_project();self.assertTrue(p['settings']['visionQC'])
        self.assertEqual(check_level(p['settings']),'practical');self.assertFalse(p['settings']['automaticRepair'])

    def test_private_content_and_spending_grants_are_not_exposed(self):
        p=new_project();p['chapters'][0]['sourceText']='PRIVATE CHAPTER'
        p['settings']['apiKey']='PRIVATE KEY';p['settings']['image']['references']=['private/photo.png']
        p['intro']['voiceText']='PRIVATE INTRO';before=copy.deepcopy(p)
        r=planner_profile(p);encoded=json.dumps(r)
        for word in ('PRIVATE CHAPTER','PRIVATE KEY','private/photo.png','PRIVATE INTRO','budget'):
            self.assertNotIn(word,encoded)
        self.assertEqual(r['settings']['qcCheckLevel'],'practical');self.assertEqual(p,before)

    def test_saved_off_and_legacy_behavior_are_preserved(self):
        p=new_project();p['settings']['qcCheckLevel']='off'
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'off')
        del p['settings']['qcCheckLevel'];p['settings']['visionQC']=False
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'off')
        p['settings']['generationMode']='MAX QUALITY'
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'practical')

    def test_required_gpu_name_is_exposed_without_worker_or_key_details(self):
        p=new_project();r=planner_profile(p,config={'runpodRequiredGPUType':'NVIDIA GeForce RTX 5090','runpodApiKey':'SECRET','runpodManagedPodId':'private-pod'})
        self.assertEqual(r['imageExecution']['requiredGPU'],'NVIDIA GeForce RTX 5090')
        self.assertNotIn('SECRET',str(r));self.assertNotIn('private-pod',str(r))

    def test_partial_narration_is_not_claimed_complete(self):
        from studio_service import StudioService
        p=new_project();c=p['chapters'][0];c['sourceText']='Hello'
        c['audio']={'duration':20,'path':'chapter.wav','signature':StudioService.narration_signature(p,c)}
        self.assertTrue(planner_profile(p)['narrationComplete'])
        c2=copy.deepcopy(c);c2['audio']={};p['chapters'].append(c2)
        self.assertFalse(planner_profile(p)['narrationComplete'])

    def test_old_audio_duration_is_not_used_after_editing_the_story_or_voice(self):
        from studio_service import StudioService
        p=new_project();c=p['chapters'][0];c['sourceText']='Hello'
        c['audio']={'duration':20,'path':'chapter.wav','signature':StudioService.narration_signature(p,c)}
        self.assertEqual(planner_profile(p)['narrationSeconds'],20)
        c['sourceText']='A much longer changed story.'
        r=planner_profile(p)
        self.assertFalse(r['narrationComplete']);self.assertEqual(r['narrationSeconds'],0)
        c['sourceText']='Hello';p['settings']['voice']='different-voice'
        self.assertFalse(planner_profile(p)['narrationComplete'])
        self.assertEqual(planner_profile(p)['narrationSeconds'],0)

    def test_renderer_follows_actual_helper_backend_including_cpu_fallback(self):
        p=new_project();p['settings']['video']['renderer']='auto'
        self.assertEqual(planner_profile(p,config={'renderBackend':'native'})['renderExecution']['backend'],'native')
        self.assertEqual(planner_profile(p,config={'renderBackend':'native'},renderer_backend='cpu')['renderExecution']['backend'],'cpu')

    def test_new_pipeline_admission_does_not_inherit_old_three_task_throughput(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project()
            p['settings']['director'].update(executionMode='staged-lean',parallelism=4)
            old=profile_for('Image pipeline',p,{}, {'imageTasks':3})
            db.record('old','run',old,{'kind':'images','value':1},9,{})
            r=planner_profile(p,db,{})
            self.assertEqual(r['imageTasks'],5)
            self.assertIsNone(r['estimates']['images']['seconds'])
            self.assertEqual(profile_for('Image pipeline',p,{})['imageTasks'],5)
            db.close()

    def test_history_only_matches_recorded_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project();cfg={}
            p['settings']['unattendedProduction']=False
            profile=profile_for('Visual QC',p,cfg)
            db.record('ok','run',profile,{'kind':'images','value':1},7,{'estimatedUSD':.0002})
            db.record('bad','run',profile,{'kind':'images','value':1},200,{'status':'FAILED'})
            r=planner_profile(p,db,cfg)['estimates']['visualQC']
            self.assertEqual(r['seconds'],7);self.assertEqual(r['samples'],1)
            p['settings']['image']['model']='different'
            self.assertIsNone(planner_profile(p,db,cfg)['estimates']['visualQC']['seconds']);db.close()

    def test_forecast_matches_automatic_full_video_policy_without_changing_saved_settings(self):
        p=new_project();before=copy.deepcopy(p)
        r=planner_profile(p)
        self.assertTrue(r['settings']['unattendedProduction'])
        self.assertTrue(r['settings']['automaticRepair'])
        self.assertEqual(r['settings']['qcRepairBudgetMode'],'project-cap')
        self.assertEqual(p,before)
        p['settings']['unattendedProduction']=False
        self.assertFalse(planner_profile(p)['settings']['automaticRepair'])

if __name__=='__main__':unittest.main()
