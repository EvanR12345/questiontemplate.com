import copy
import unittest
from unittest.mock import Mock, patch
from studio_automation import run_settings, project_cap_reason, preserve_manual_plan
from studio_qc import decision, automatic_repairs, validate_checks
from studio_data import get_chapter, get_shot
import full_video_test as full_fixture
import studio_qc_test as qc_fixture


class AutomationPolicyTest(unittest.TestCase):
    def test_run_policy_retains_caps_refs_model_and_quality(self):
        original={'budget':{'openaiUSD':.4,'totalUSD':2},'cloudWindow':{'gpuBudgetUSD':1.5},
            'image':{'provider':'comfyui','model':'flux2-klein-4b','width':1280,'height':720},
            'director':{'provider':'openai-luna'},'qcCheckLevel':'practical','maxImageRetries':1,
            'manual':{'voice':True},'appearanceHandling':'Review changes'}
        before=copy.deepcopy(original);result=run_settings(original)
        self.assertEqual(original,before)
        for field in ('budget','cloudWindow','image','director','manual'):self.assertEqual(result[field],original[field])
        self.assertTrue(result['automaticRepair']);self.assertEqual(result['maxImageRetries'],1)
        self.assertEqual(result['qcRepairBudgetMode'],'project-cap')

    def test_explicit_off_stays_off(self):
        result=run_settings({'qcCheckLevel':'off','automaticRepair':False,'maxImageRetries':0})
        self.assertFalse(result['automaticRepair']);self.assertEqual(result['maxImageRetries'],0)

    def test_paid_repairs_need_both_existing_caps(self):
        s={'director':{'provider':'openai-luna'},'image':{'provider':'comfyui'},
           'budget':{'openaiUSD':.5},'cloudWindow':{'gpuBudgetUSD':1}}
        self.assertIsNone(project_cap_reason(s))
        for group,key in [('budget','openaiUSD'),('cloudWindow','gpuBudgetUSD')]:
            for invalid in [None,0,-1,True,float('nan'),float('inf')]:
                bad=copy.deepcopy(s);bad[group][key]=invalid
                self.assertIsNotNone(project_cap_reason(bad))

    def test_invalid_mode_and_unattended_value_are_rejected(self):
        for s in [{'unattendedProduction':'true'},{'qcRepairBudgetMode':'unlimited'}]:
            with self.assertRaises(ValueError):validate_checks(s)

    def test_partitioned_caps_cannot_exceed_the_saved_total(self):
        settings={'director':{'provider':'openai-luna'},'image':{'provider':'comfyui'},
                  'budget':{'openaiUSD':.5,'totalUSD':1},'cloudWindow':{'gpuBudgetUSD':.5}}
        self.assertIsNone(project_cap_reason(settings))
        settings['cloudWindow']['gpuBudgetUSD']=.6
        self.assertIn('exceed',project_cap_reason(settings))
        for invalid in [0,True,float('nan')]:
            settings['budget']['totalUSD']=invalid
            self.assertIn('total',project_cap_reason(settings))

    def test_new_ai_layout_retains_manual_camera_prompt_and_original_asset(self):
        old=[{'id':'old-scene','shots':[{'id':'old-shot','narrationSegment':'Mira lifts the key.',
            'camera':{'shot':'close-up'},'prompt':'My exact prompt','imagePath':'original.png',
            'manual':{'camera':True,'prompt':True}}]}]
        new=[{'id':'new-scene','shots':[{'id':'new-shot','narrationSegment':'Mira lifts the key.',
            'camera':{'shot':'wide'},'prompt':'AI prompt','action':'Lift the key'}]}]
        before=copy.deepcopy(old);result=preserve_manual_plan(old,new)
        self.assertEqual(result[0]['shots'][0]['prompt'],'My exact prompt')
        self.assertEqual(result[0]['shots'][0]['camera'],{'shot':'close-up'})
        self.assertTrue(result[0]['shots'][0]['generationStale'])
        self.assertEqual(result[0]['shots'][0]['action'],'Lift the key');self.assertEqual(old,before)

    def test_unmatched_manual_story_edit_is_never_discarded(self):
        old=[{'shots':[{'id':'s','narrationSegment':'Original story','manual':{'action':True},'action':'Keep me'}]}]
        before=copy.deepcopy(old)
        with self.assertRaisesRegex(ValueError,'original edits retained'):
            preserve_manual_plan(old,[{'shots':[{'narrationSegment':'Different story'}]}])
        self.assertEqual(old,before)

    def test_repeated_narration_matches_a_unique_timing_without_guessing(self):
        old=[{'shots':[{'start':8,'end':10,'narrationSegment':'He nodded.',
                       'manual':{'prompt':True},'prompt':'Keep this second nod'}]}]
        new=[{'shots':[{'start':0,'end':2,'narrationSegment':'He nodded.','prompt':'First'},
                       {'start':8,'end':10,'narrationSegment':'He nodded.','prompt':'Second'}]}]
        result=preserve_manual_plan(old,new)
        self.assertEqual(result[0]['shots'][0]['prompt'],'First')
        self.assertEqual(result[0]['shots'][1]['prompt'],'Keep this second nod')


class AutomaticQCTest(unittest.TestCase):
    def setUp(self):
        self.fixture=qc_fixture.PracticalQCTest();self.fixture.setUp()
        self.p,self.shot,self.store=self.fixture.p,self.fixture.shot,self.fixture.store
        self.p['settings']=run_settings(self.p['settings'])
    def tearDown(self):self.fixture.tearDown()

    def test_uncertain_findings_remain_warnings_without_fake_pass(self):
        self.shot['qc']['issues']=['The important detail could not be verified.']
        before=copy.deepcopy(self.shot['qc'])
        self.assertFalse(decision(self.p,self.shot,self.store)['blocking'])
        self.assertEqual(self.shot['qc'],before);self.assertFalse(self.shot['qc']['pass'])

    def test_confirmed_major_review_is_repairable_and_still_blocks(self):
        self.shot['qc'].update(action='review',issues=['wrong weapon'])
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])
        self.assertTrue(automatic_repairs(self.p['settings'],self.shot['qc']))

    def test_manual_mode_keeps_the_existing_unknown_review_rule(self):
        self.p['settings']['unattendedProduction']=False
        self.shot['qc']['issues']=['The important detail could not be verified.']
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])

    def test_strict_unattended_mode_does_not_accept_an_unresolved_major_review(self):
        self.p['settings']['qcCheckLevel']='strict'
        self.shot['qc'].update(action='review',issues=['wrong weapon'])
        self.assertTrue(decision(self.p,self.shot,self.store)['blocking'])
        self.assertTrue(automatic_repairs(self.p['settings'],self.shot['qc']))


class Archive:
    enabled=True
    def __init__(self,service):self.service=service;self.enqueued=[];self.error=''
    def enqueue(self,pid):self.enqueued.append(pid)
    def status(self,pid=None):
        return {'error':self.error,'projects':{pid:{'status':'SYNCED','revision':self.service.store.load(pid)['revision']}}}
    def close(self):pass


class AutomaticProductionTest(unittest.TestCase):
    def setUp(self):
        self.fixture=full_fixture.FullVideoTest();self.fixture.setUp()
        self.service,self.project=self.fixture.service,self.fixture.project
        self.service.storage.close();self.service.storage=Archive(self.service)
    def tearDown(self):self.fixture.tearDown()

    def test_two_chapters_finish_and_save_without_review_actions(self):
        self.service.produce_story(self.project['id'],{'unattended':True})
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertEqual(p['production']['stage'],'Full video ready in cloud storage')
        self.assertEqual(p['render']['path'],'story.mp4')
        self.assertGreaterEqual(len(self.service.storage.enqueued),2)
        self.assertEqual(sum(c[0]=='analysis' for c in self.service.calls),2)

    def test_missing_archive_does_not_claim_completed_cloud_video(self):
        self.service.storage.enabled=False
        with self.assertRaisesRegex(RuntimeError,'Cloud archive is unavailable'):
            self.service.produce_story(self.project['id'],{'unattended':True})
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'ARCHIVING')
        self.assertEqual(p['render']['path'],'story.mp4')

    def test_archive_error_retains_the_render(self):
        self.service.storage.error='fixture failure'
        with self.assertRaisesRegex(RuntimeError,'Cloud save failed'):
            self.service.produce_story(self.project['id'],{'unattended':True})
        self.assertTrue(self.service.store.asset(self.project['id'],'story.mp4').is_file())

    def test_cloud_barrier_waits_for_the_requested_revision(self):
        p=self.service.store.load(self.project['id']);seen=[]
        self.service.storage.status=Mock(side_effect=[{'projects':{p['id']:{'status':'SYNCED','revision':p['revision']-1}}},
            {'projects':{p['id']:{'status':'SYNCED','revision':p['revision']}}}])
        with patch('studio_service.time.sleep',side_effect=lambda seconds:seen.append(seconds)):
            self.service.wait_for_cloud_save(p['id'])
        self.assertEqual(seen,[.25]);self.assertEqual(self.service.storage.status.call_count,2)

    def test_transient_archive_failure_requeues_and_then_completes(self):
        p=self.service.store.load(self.project['id'])
        self.service.storage.status=Mock(side_effect=[{'projects':{p['id']:{'error':'Upload failed','status':'FAILED'}}},
            {'projects':{p['id']:{'status':'SYNCED','revision':p['revision']}}}])
        self.service.wait_for_cloud_save(p['id'])
        self.assertEqual(self.service.storage.enqueued,[p['id'],p['id']])

    def test_repeated_archive_failures_stop_after_two_retries(self):
        p=self.project
        self.service.storage.status=Mock(return_value={'projects':{p['id']:{'error':'Upload failed','status':'FAILED'}}})
        with self.assertRaisesRegex(RuntimeError,'retries exhausted'):
            self.service.wait_for_cloud_save(p['id'])
        self.assertEqual(len(self.service.storage.enqueued),3)

    def test_archive_wait_honors_cancel_without_retrying(self):
        from studio_service import JobCancelled
        self.service.cancel=True
        with self.assertRaises(JobCancelled):self.service.wait_for_cloud_save(self.project['id'])
        self.assertEqual(len(self.service.storage.enqueued),1)

    def test_archive_wait_has_a_bounded_deadline(self):
        self.service.storage.status=Mock(return_value={'projects':{}})
        with patch('studio_service.time.monotonic',side_effect=[0,31]), self.assertRaisesRegex(RuntimeError,'deadline'):
            self.service.config['cloudSaveTimeoutSeconds']=30
            self.service.wait_for_cloud_save(self.project['id'])

    def test_repair_count_cannot_reset_when_generate_is_pressed_again(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];shot=ch['scenes'][0]['shots'][0]
        self.service.store.mutate(p['id'],lambda latest:latest['settings'].update(maxImageRetries=1))
        self.service.reserve_automatic_repair(p['id'],ch['id'],shot['id'],'same-spec')
        with self.assertRaisesRegex(ValueError,'repair limit'):
            self.service.reserve_automatic_repair(p['id'],ch['id'],shot['id'],'same-spec')
        self.assertEqual(get_shot(self.service.store.load(p['id']),ch['id'],shot['id'])['automaticRepairAttempts'],1)

    def test_changed_shot_has_a_separate_bounded_repair_counter(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];shot=ch['scenes'][0]['shots'][0]
        self.service.store.mutate(p['id'],lambda latest:latest['settings'].update(maxImageRetries=1))
        self.service.reserve_automatic_repair(p['id'],ch['id'],shot['id'],'old-spec')
        self.service.reserve_automatic_repair(p['id'],ch['id'],shot['id'],'new-spec')
        result=get_shot(self.service.store.load(p['id']),ch['id'],shot['id'])
        self.assertEqual(result['automaticRepairAttempts'],1);self.assertEqual(result['automaticRepairSpec'],'new-spec')

    def test_major_repair_uses_at_most_the_durable_remaining_attempts(self):
        from studio_service import StudioService
        from PIL import Image
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];shot=ch['scenes'][0]['shots'][0]
        p['settings']=run_settings(p['settings']);p['settings']['maxImageRetries']=2
        p=self.service.store.save(p)
        self.service.providers['native-flux']=Mock()
        provider=self.service.providers['existing']
        provider.getCapabilities=lambda:{'maxReferenceImages':1,'supportsImageEditing':False}
        provider.generateImage=Mock(side_effect=lambda request,gate:{'pil':Image.new('RGB',(8,8),'red'),
            'model':'sd15','settings':request['settings']})
        self.service.visual_check=Mock(return_value={'pass':False,'issues':['wrong weapon'],
            'action':'review','repairPrompt':'Use the pistol from the story.'})
        options={'budgetedQCRepair':True,'_collectReview':True}
        StudioService.generate(self.service,p,ch['id'],shot['id'],123,options)
        self.assertEqual(provider.generateImage.call_count,2)
        current=self.service.store.load(p['id'])
        self.assertEqual(get_shot(current,ch['id'],shot['id'])['automaticRepairAttempts'],2)
        with self.assertRaisesRegex(ValueError,'zero-retry|repair limit'):
            StudioService.generate(self.service,current,ch['id'],shot['id'],123,options)
        self.assertEqual(provider.generateImage.call_count,2)

    def test_exhausted_intro_repair_never_starts_a_gpu(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);shot=copy.deepcopy(p['chapters'][0]['scenes'][0]['shots'][0])
        p['settings']=run_settings(p['settings']);p['settings']['maxImageRetries']=1
        p['intro'].update(enabled=True,shots=[shot]);p=self.service.store.save(p)
        spec=self.service.visual_spec_signature(p,shot)
        self.service.reserve_automatic_repair(p['id'],None,shot['id'],spec,intro=True)
        self.service.ensure_production_gpu=Mock(side_effect=AssertionError('GPU was started'))
        with self.assertRaisesRegex(ValueError,'no rental started'):
            self.service.intro_image(self.service.store.load(p['id']),{'repairShot':shot['id']})
        self.service.ensure_production_gpu.assert_not_called()

    def test_intro_script_change_invalidates_repair_and_review_specification(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);shot=copy.deepcopy(p['chapters'][0]['scenes'][0]['shots'][0])
        p['intro'].update(enabled=True,shots=[shot],voiceText='The key opened a gate.')
        before=self.service.visual_spec_signature(p,shot)
        p['intro']['voiceText']='The gate remained locked.'
        self.assertNotEqual(before,self.service.visual_spec_signature(p,shot))

    def intro_review_fixture(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);shot=copy.deepcopy(p['chapters'][0]['scenes'][0]['shots'][0])
        self.service.store.asset(p['id'],'intro-fixture.png').write_bytes(b'saved intro bytes')
        shot.update(imagePath='intro-fixture.png',qc={'pass':True,'status':'PASSED'})
        p['settings']=run_settings(p['settings'])
        p['intro'].update(enabled=True,shots=[shot],voiceText='The key opened a gate.')
        return self.service.store.save(p),shot

    def test_saved_intro_pass_is_refreshed_once_then_reused_until_inputs_change(self):
        p,shot=self.intro_review_fixture()
        self.service.visual_check=Mock(return_value={'pass':True,'issues':[],'action':'accept'})
        self.service.inspect_intro(p,sampled_only=True)
        self.service.inspect_intro(self.service.store.load(p['id']),sampled_only=True)
        self.assertEqual(self.service.visual_check.call_count,1)
        self.service.store.mutate(p['id'],lambda q:q['intro'].update(voiceText='The gate remained locked.'))
        self.service.inspect_intro(self.service.store.load(p['id']),sampled_only=True)
        self.assertEqual(self.service.visual_check.call_count,2)

    def test_intro_review_for_old_input_cannot_certify_new_manual_selection(self):
        p,shot=self.intro_review_fixture()
        self.service.store.asset(p['id'],'manual-intro.png').write_bytes(b'new manual bytes')
        def check(*args):
            self.service.store.mutate(p['id'],lambda q:q['intro']['shots'][0].update(imagePath='manual-intro.png'))
            return {'pass':True,'issues':[],'action':'accept'}
        self.service.visual_check=Mock(side_effect=check)
        with self.assertRaisesRegex(RuntimeError,'current edits'):
            self.service.inspect_intro(p,sampled_only=True)
        current=self.service.store.load(p['id'])['intro']['shots'][0]
        self.assertEqual(current['imagePath'],'manual-intro.png')
        self.assertEqual(current['qc']['status'],'PENDING')
        self.assertFalse(current['qcHistory'][-1]['selected'])
