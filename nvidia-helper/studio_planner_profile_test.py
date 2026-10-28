import copy,json,tempfile,time,unittest
from pathlib import Path
from studio_data import new_project
from studio_planner_profile import planner_profile
from studio_performance import PerformanceStore,profile_for,signature
from studio_qc import check_level

class PlannerProfileTest(unittest.TestCase):
    def test_qc_compatibility_is_policy_independent_but_matches_review_inputs(self):
        p=new_project();p['settings']['director']['provider']='openai-luna';cfg={'openaiModel':'gpt-6-luna'}
        original=copy.deepcopy(p);base=planner_profile(p,config=cfg)
        expected=profile_for('Visual QC',p,cfg)
        for field in ('policy','reviewAPISlots','processingTier'):expected.pop(field)
        self.assertEqual(base['qcCompatibilityHash'],signature(expected))
        for level in ('off','sampled','practical','strict'):
            changed=copy.deepcopy(p);changed['settings']['qcCheckLevel']=level
            self.assertEqual(planner_profile(changed,config=cfg)['qcCompatibilityHash'],base['qcCompatibilityHash'])
        for group,key,value in ((None,'continuityStrictness','High'),(None,'imageVisualConstraints','PRIVATE CONSTRAINT'),
                                ('director','qcReasoning','High'),('image','model','different-model'),('image','width',1920)):
            changed=copy.deepcopy(p);target=changed['settings'] if group is None else changed['settings'][group]
            target[key]=value
            self.assertNotEqual(planner_profile(changed,config=cfg)['qcCompatibilityHash'],base['qcCompatibilityHash'])
        self.assertNotEqual(planner_profile(p,config={'openaiModel':'different-model'})['qcCompatibilityHash'],base['qcCompatibilityHash'])
        self.assertEqual(p,original)

    def test_qc_tier_is_separate_and_private_review_constraints_are_not_exposed(self):
        p=new_project();p['settings']['imageVisualConstraints']='PRIVATE REVIEW CONSTRAINT'
        p['settings']['director']['processingTier']='default';base=planner_profile(p)
        changed=copy.deepcopy(p);changed['settings']['director']['processingTier']='flex'
        flex=planner_profile(changed)
        self.assertEqual(base['qcCompatibilityHash'],flex['qcCompatibilityHash'])
        self.assertEqual(base['qcProcessingTier'],'default');self.assertEqual(flex['qcProcessingTier'],'flex')
        changed['settings']['director']['parallelism']=8
        self.assertEqual(planner_profile(changed)['qcCompatibilityHash'],base['qcCompatibilityHash'])
        del changed['settings']['director']['processingTier']
        self.assertEqual(planner_profile(changed,config={'openaiServiceTier':'fast'})['qcProcessingTier'],'fast')
        self.assertNotIn('PRIVATE REVIEW CONSTRAINT',json.dumps(base))

    def test_qc_history_keeps_latest_timestamp_for_frontend_freshness_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project();cfg={}
            p['settings']['unattendedProduction']=False
            qc=profile_for('Visual QC',p,cfg);voice_before=profile_for('Narration',p,cfg)
            newest=time.time()-1
            db.record('earlier','earlier',qc,{'kind':'images','value':1},7,{'estimatedUSD':.0002},finished=newest-30)
            db.record('newer','newer',qc,{'kind':'images','value':1},9,{'estimatedUSD':.0004},finished=newest)
            result=planner_profile(p,db,cfg)
            self.assertEqual(result['estimates']['visualQC']['latest'],newest)
            self.assertEqual(result['estimates']['visualQC']['samples'],2)
            self.assertEqual(result['estimates']['visualQC']['runs'],2)
            self.assertEqual(result['estimates']['visualQC']['seconds'],8)
            self.assertAlmostEqual(result['estimates']['visualQC']['costUSD'],.0003)
            self.assertEqual(result['qcHistoryIdentity'],{'policy':qc['policy'],'apiSlots':qc['reviewAPISlots'],
                'processingTier':qc['processingTier'],'compatibilityHash':result['qcCompatibilityHash']})
            self.assertEqual(profile_for('Narration',p,cfg),voice_before)
            db.close()

    def test_legacy_and_other_slot_or_tier_qc_history_has_no_matching_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project();cfg={}
            p['settings']['unattendedProduction']=False
            p['settings']['director'].update(parallelism=4,processingTier='default')
            current=profile_for('Visual QC',p,cfg);legacy=dict(current)
            legacy.pop('reviewAPISlots');legacy.pop('processingTier')
            db.record('legacy','old',legacy,{'kind':'images','value':1},1,{})
            unknown=planner_profile(p,db,cfg)
            self.assertIsNone(unknown['estimates']['visualQC']['seconds']);self.assertIsNone(unknown['qcHistoryIdentity'])
            db.record('current','current',current,{'kind':'images','value':1},7,{'estimatedUSD':.0003})
            self.assertEqual(planner_profile(p,db,cfg)['qcHistoryIdentity']['apiSlots'],4)
            for patch in ({'parallelism':8},{'processingTier':'flex'}):
                changed=copy.deepcopy(p);changed['settings']['director'].update(patch)
                unmatched=planner_profile(changed,db,cfg)
                self.assertIsNone(unmatched['estimates']['visualQC']['seconds'])
                self.assertIsNone(unmatched['qcHistoryIdentity'])
                self.assertEqual(unmatched['qcCompatibilityHash'],unknown['qcCompatibilityHash'])
            self.assertEqual(db.estimate(legacy,{'kind':'images','value':1})['seconds'],1)
            self.assertEqual(db.estimate(current,{'kind':'images','value':1})['seconds'],7)
            db.close()

    def test_configured_verified_quote_is_public_but_resource_identifiers_are_not(self):
        p=new_project();p['settings']['image']['provider']='comfyui'
        cfg={'runpodRequiredGPUType':'NVIDIA GeForce RTX 5090','runpodManagedPodId':'private-pod'}
        lease={'podId':'private-pod','workerCopies':2,'workersVerified':True,'hourlyUSD':.69,'started':100,'privateDetail':'secret'}
        result=planner_profile(p,config=cfg,rental=lease)
        self.assertEqual(result['imageExecution']['computeQuote']['hourlyUSD'],.69)
        self.assertNotIn('private-pod',json.dumps(result));self.assertNotIn('secret',json.dumps(result))
        for patch in ({'podId':'different-pod'},{'workersVerified':False},{'hourlyUSD':float('nan')}):
            self.assertIsNone(planner_profile(p,config=cfg,rental=lease|patch)['imageExecution']['computeQuote'])
    def test_verified_marketplace_quote_keeps_disk_inclusion_and_hides_worker_identity(self):
        p=new_project();cfg={'gpuImageVerifiedProviders':['vast']}
        lease={'project':p['id'],'provider':'vast','podId':'PRIVATE-ID','workerGPU':'NVIDIA GeForce RTX 5090',
            'workersVerified':True,'workerCopies':2,'hourlyUSD':.69,'started':100,'workingDiskIncludedInQuote':True}
        quote=planner_profile(p,config=cfg,rental=lease)['imageExecution']['computeQuote']
        self.assertTrue(quote['workingDiskIncluded']);self.assertEqual(quote['provider'],'vast')
        self.assertNotIn('PRIVATE-ID',str(quote))
        self.assertIsNone(planner_profile(p,config=cfg,rental=lease | {'project':'other'})['imageExecution']['computeQuote'])

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
