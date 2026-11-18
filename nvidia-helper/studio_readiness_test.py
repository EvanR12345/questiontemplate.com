"""Readiness must reveal saved production blockers without paid dispatch or edits."""
import copy, hashlib, json, shutil, unittest
from unittest.mock import Mock, patch
from studio_data import get_shot
from studio_render import VideoRenderer
from studio_service import StudioService
from studio_storage import R2Archive
import studio_qc_test as qc_fixtures


class ReadinessReviewTest(unittest.TestCase):
    def setUp(self):
        self.fixture=qc_fixtures.PracticalQCTest();self.fixture.setUp()
        self.store=self.fixture.store;self.pid=self.fixture.pid
        self.service=object.__new__(StudioService)
        self.service.store=self.store
        self.service.config={'ffmpeg':__file__}
        self.service.renderer=VideoRenderer(self.store,self.service.config)
        self.service.audio=Mock();self.service.audio.health.return_value={'backend':'fixture'}
        self.service.execution_journal=Mock();self.service.execution_journal.unresolved_project.return_value=[]
        self.service.storage=Mock();self.service.storage.status.return_value={'enabled':True,'provider':'Google Cloud Storage'}
        self.worker=Mock();self.worker.healthCheck.return_value={'installed':True}
        self.service.provider=Mock(return_value=self.worker)
        self.service.ensure_production_gpu=Mock(side_effect=AssertionError('Readiness started a GPU'))
        self.health=patch('studio_service.LocalQwenDirector.healthCheck',return_value={'installed':True})
        self.health.start()

    def tearDown(self):
        self.health.stop();self.fixture.tearDown()

    def change(self, **shot_patch):
        def apply(p):get_shot(p,self.fixture.chid,self.fixture.sid).update(shot_patch)
        self.store.mutate(self.pid,apply)

    def check(self, options=None):
        path=self.store.folder(self.pid)/'project.json'
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        result=self.service.production_readiness(self.pid,options)
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),before)
        self.assertEqual(result['paidRequestsSubmitted'],0)
        self.worker.generateImage.assert_not_called();self.service.ensure_production_gpu.assert_not_called()
        return result

    def major(self):
        self.change(qc={'pass':False,'status':'FAILED','action':'image_edit','issues':['wrong weapon']})

    def test_saved_major_error_blocks_before_queueing_without_erasing_findings(self):
        self.major();before=copy.deepcopy(self.store.load(self.pid))
        report=self.check();self.assertFalse(report['ready'])
        item=next(c for c in report['checks'] if c['name']=='saved-visual-reviews')
        self.assertTrue(item['blocking']);self.assertIn('1 saved images',item['message'])
        self.assertEqual(self.store.load(self.pid),before)

    def test_practical_advisory_is_not_promoted_to_a_blocking_error(self):
        self.assertTrue(self.check()['ready'])

    def test_explicit_off_retains_findings_and_allows_the_chosen_workflow(self):
        self.major();self.store.mutate(self.pid,lambda p:p['settings'].update(qcCheckLevel='off'))
        self.assertTrue(self.check()['ready'])
        self.assertFalse(get_shot(self.store.load(self.pid),self.fixture.chid,self.fixture.sid)['qc']['pass'])

    def test_valid_manual_acceptance_is_respected(self):
        self.major();self.fixture.accept();self.assertTrue(self.check()['ready'])

    def test_pending_review_can_resume_without_renting_a_new_image(self):
        self.change(qc={'pass':None,'status':'PENDING'})
        self.assertTrue(self.check()['ready']);self.service.provider.assert_not_called()
        self.change(qc={'pass':False,'status':'PENDING','issues':['wrong weapon']})
        self.assertTrue(self.check()['ready'])

    def test_stale_saved_image_can_regenerate_and_is_not_blocked_by_old_review(self):
        self.major();self.change(generationStale=True)
        self.assertTrue(self.check()['ready']);self.worker.healthCheck.assert_called_once()

    def test_missing_intro_image_checks_the_worker_and_does_not_restore_assets(self):
        def intro(p):
            s=copy.deepcopy(get_shot(p,self.fixture.chid,self.fixture.sid));s.update(id='intro-shot',imagePath='missing-intro.png')
            p['intro'].update(enabled=True,shots=[s])
        self.store.mutate(self.pid,intro)
        self.worker.healthCheck.return_value={'installed':False}
        with patch.object(self.store,'asset',side_effect=AssertionError('Readiness restored media')):
            report=self.check()
        self.assertFalse(report['ready']);self.worker.healthCheck.assert_called_once()
        self.assertFalse(next(c for c in report['checks'] if c['name']=='images')['ready'])

    def test_missing_single_intro_visual_also_checks_the_worker(self):
        self.store.mutate(self.pid,lambda p:p['intro'].update(enabled=True,shots=[],visualPath='missing-visual.png'))
        self.worker.healthCheck.return_value={'installed':False}
        self.assertFalse(self.check()['ready']);self.worker.healthCheck.assert_called_once()

    def test_disabled_intro_does_not_block_the_story_with_its_old_flags(self):
        def intro(p):
            s=copy.deepcopy(get_shot(p,self.fixture.chid,self.fixture.sid))
            s.update(id='intro-shot',qc={'pass':False,'action':'image_edit','issues':['wrong weapon']})
            p['intro'].update(enabled=False,shots=[s])
        self.store.mutate(self.pid,intro);self.assertTrue(self.check()['ready'])
        self.store.mutate(self.pid,lambda p:p['intro'].update(enabled=True));self.assertFalse(self.check()['ready'])

    def test_unverified_automatic_repair_budget_cannot_hide_the_review_block(self):
        self.major();self.store.mutate(self.pid,lambda p:p['settings'].update(automaticRepair=True))
        report=self.check();self.assertFalse(report['ready'])
        self.assertFalse(next(c for c in report['checks'] if c['name']=='automatic-repairs')['ready'])

    def test_unattended_preflight_projects_repairs_without_mutating_saved_policy(self):
        self.major();before=copy.deepcopy(self.store.load(self.pid))
        report=self.service.production_readiness(self.pid,{'unattended':True})
        row=next(c for c in report['checks'] if c['name']=='saved-visual-reviews')
        self.assertTrue(row['ready']);self.assertIn('repaired automatically',row['message'])
        self.assertEqual(self.store.load(self.pid),before)
        self.service.ensure_production_gpu.assert_not_called()

    def exhaust_saved_retry(self):
        p=self.store.load(self.pid)
        shot=get_shot(p,self.fixture.chid,self.fixture.sid)
        self.change(automaticRepairAttempts=1,automaticRepairSpec=self.service.visual_spec_signature(p,shot))

    def test_consumed_retry_blocks_until_a_new_spec_is_saved(self):
        self.major()
        self.store.mutate(self.pid,lambda p:p['settings'].update(maxImageRetries=1))
        self.exhaust_saved_retry()
        options={'unattended':True}
        report=self.check(options)
        row=next(c for c in report['checks'] if c['name']=='saved-visual-reviews')
        self.assertFalse(row['ready']);self.assertIn('1 saved images',row['message'])
        self.assertFalse(report['ready']);self.service.provider.assert_not_called()
        self.change(prompt='The scene has substantively changed.')
        self.assertTrue(self.check(options)['ready'])
        self.worker.healthCheck.assert_called_once()

    def test_manually_locked_major_finding_blocks_unless_explicitly_accepted(self):
        self.major()
        self.change(manual={'imagePath':True})
        options={'unattended':True}
        self.assertFalse(self.check(options)['ready'])
        self.service.provider.assert_not_called()
        self.fixture.accept()
        self.assertTrue(self.check(options)['ready'])
        self.service.provider.assert_not_called()

    def test_unattended_pending_quality_can_resume_without_an_image_worker(self):
        self.change(qc={'status':'PENDING','pass':None})
        self.assertTrue(self.check({'unattended':True})['ready'])
        self.service.provider.assert_not_called()

    def test_cloud_only_reference_digest_preserves_consumed_retry_without_restoring(self):
        self.major()
        self.store.mutate(self.pid,lambda p:p['settings'].update(maxImageRetries=1))
        self.change(manual={'referenceImages':True},referenceImages=[{'path':'face.png'}])
        ref=self.store.asset_local(self.pid,'face.png')
        ref.write_bytes(b'original reference')
        self.exhaust_saved_retry()
        digest=hashlib.sha256(ref.read_bytes()).hexdigest()
        length=ref.stat().st_size
        ref.unlink()
        objects=Mock(provider='Google Cloud Storage',publisher_compatible=False)
        objects.read.return_value=(json.dumps({'files':{'face.png':{
            'key':f'studio/assets/{self.pid}/{digest}/face.png','sha256':digest,'bytes':length}}}).encode(),'receipt')
        archive=R2Archive(self.store,self.store.root/'missing.json',objects=objects,start_worker=False)
        self.addCleanup(archive.close)
        with patch.object(self.store,'asset',side_effect=AssertionError('Readiness restored media')):
            report=self.check({'unattended':True})
        self.assertFalse(next(c for c in report['checks'] if c['name']=='saved-visual-reviews')['ready'])
        objects.read.assert_called_once()
        objects.download.assert_not_called()
        self.assertFalse(ref.exists())

    def test_local_route_reports_cloud_shot_and_fallback_conflicts_before_launch(self):
        self.service.config.update(gpuProviderOrder=['lium'],gpuImageVerifiedProviders=['lium'])
        self.change(imageProvider='comfyui',imagePath='',generationStale=True)
        report=self.check()
        row=next(c for c in report['checks'] if c['name']=='local-image-routing')
        self.assertFalse(report['ready']);self.assertIn('cloud image provider',row['message'])
        self.change(imageProvider='existing')
        self.store.mutate(self.pid,lambda p:p['settings']['image'].update(
            fallbackEnabled=True,fallback={'provider':'comfyui','model':'klein'}))
        row=next(c for c in self.check()['checks'] if c['name']=='local-image-routing')
        self.assertFalse(row['ready']);self.assertIn('cloud fallback',row['message'])
        self.store.mutate(self.pid,lambda p:p['settings']['image'].update(fallbackEnabled=False))
        self.assertTrue(self.check()['ready'])

    def test_zero_retry_preflight_keeps_checks_and_blocks_only_a_known_blocker(self):
        self.store.mutate(self.pid,lambda p:p['settings'].update(maxImageRetries=0))
        report = self.service.production_readiness(self.pid,{'unattended':True})
        self.assertTrue(report['ready'])
        self.major()
        report = self.service.production_readiness(self.pid,{'unattended':True})
        row = next(c for c in report['checks'] if c['name']=='saved-visual-reviews')
        self.assertFalse(row['ready'])
        self.assertIn('zero-retry',row['message'])
        self.assertFalse(report['ready'])
        self.assertEqual(report['paidRequestsSubmitted'],0)
        self.service.ensure_production_gpu.assert_not_called()

    def test_unattended_invalid_combined_cap_is_blocking_before_paid_work(self):
        def cloud(p):
            p['settings'].update(director={'provider':'openai-luna'},
                budget={'openaiUSD':1,'totalUSD':1},cloudWindow={'gpuBudgetUSD':1})
            p['settings']['image']['provider']='comfyui'
        self.store.mutate(self.pid,cloud)
        report=self.service.production_readiness(self.pid,{'unattended':True})
        row=next(c for c in report['checks'] if c['name']=='unattended-caps')
        self.assertFalse(row['ready']);self.assertTrue(row['blocking']);self.assertIn('exceed',row['message'])

    def test_cloud_archiving_does_not_claim_long_video_will_fit_small_disk(self):
        with patch('shutil.disk_usage',return_value=shutil._ntuple_diskusage(10*2**30,9*2**30,2**30)):
            report=self.check()
        self.assertTrue(report['ready'])
        warning=next(c for c in report['checks'] if c['name']=='scratch-headroom')
        self.assertFalse(warning['ready']);self.assertFalse(warning['blocking'])
        self.assertIn('not a guarantee',warning['message'])


if __name__=='__main__':unittest.main()
