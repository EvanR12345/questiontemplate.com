import tempfile
import unittest
import threading
from pathlib import Path
from unittest.mock import patch
from economy_studio import pending_panels, cloud_cost
from studio_service import StudioService, CloudBudgetPaused
from studio_data import ProjectStore, new_project

class EconomyResumeTest(unittest.TestCase):
    def test_cloud_guard_pauses_queue_and_persists_actionable_billing_warning(self):
        with tempfile.TemporaryDirectory() as directory:
            service = StudioService.__new__(StudioService)
            service.cv = threading.Condition()
            service.paused = False
            service.store = ProjectStore(directory)
            p = new_project()
            p['settings']['cloudWindow'] = {'cloudStartedAt':1000, 'gpuHourlyUSD':2,
                                           'gpuBudgetUSD':.01, 'imageReserveSeconds':15}
            service.store.save(p)
            provider = type('Provider', (), {'id':'comfyui'})()
            with patch('economy_studio.time.time', return_value=1100):
                with self.assertRaisesRegex(CloudBudgetPaused, 'does not stop rental billing'):
                    service.check_cloud_budget(p, provider)
            self.assertTrue(service.paused)
            self.assertTrue(service.store.load(p['id'])['production']['budgetBlocked'])

    def test_partial_group_preserves_only_valid_saved_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'saved.png').write_bytes(b'image')
            good = dict(id='saved',imagePath='saved.png',status='COMPLETE',generationStale=False)
            missing = dict(id='missing',imagePath='lost.png',status='COMPLETE')
            stale = dict(good,id='stale',generationStale=True)
            waiting = dict(id='waiting',status='READY_FOR_IMAGES')
            self.assertEqual([s['id'] for s in pending_panels([good,missing,stale,waiting],root)],['missing','stale','waiting'])
            self.assertEqual(good['imagePath'],'saved.png')

    def test_budget_counts_previous_rental_and_stop_reserve(self):
        with patch('economy_studio.time.time',return_value=1100):
            self.assertAlmostEqual(cloud_cost(1000,2.111,.156,25),.156+125*2.111/3600)

if __name__ == '__main__':
    unittest.main()
