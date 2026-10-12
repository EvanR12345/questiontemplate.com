"""Readiness must reveal saved production blockers without paid dispatch or edits."""
import copy, hashlib, shutil, unittest
from pathlib import Path
from unittest.mock import Mock, patch
from studio_data import get_shot
from studio_service import StudioService
import studio_qc_test as qc_fixtures


class ReadinessReviewTest(unittest.TestCase):
    def setUp(self):
        self.fixture=qc_fixtures.PracticalQCTest();self.fixture.setUp()
        self.store=self.fixture.store;self.pid=self.fixture.pid
        self.service=object.__new__(StudioService)
        self.service.store=self.store
        self.service.config={'ffmpeg':__file__}
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

    def check(self):
        path=self.store.folder(self.pid)/'project.json'
        before=hashlib.sha256(path.read_bytes()).hexdigest()
        result=self.service.production_readiness(self.pid)
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

    def test_cloud_archiving_does_not_claim_long_video_will_fit_small_disk(self):
        with patch('shutil.disk_usage',return_value=shutil._ntuple_diskusage(10*2**30,9*2**30,2**30)):
            report=self.check()
        self.assertTrue(report['ready'])
        warning=next(c for c in report['checks'] if c['name']=='scratch-headroom')
        self.assertFalse(warning['ready']);self.assertFalse(warning['blocking'])
        self.assertIn('not a guarantee',warning['message'])


if __name__=='__main__':unittest.main()
