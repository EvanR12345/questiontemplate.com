"""Standalone cloud edits own one guarded rental without touching audio."""
import threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio_service import StudioService

class ImageRentalTest(unittest.TestCase):
    def setUp(self):
        self.job={'project':'test','chapter':'chapter','shot':'shot','kind':'image'}
        self.project={'id':'test','settings':{'cloudWindow':{'gpuBudgetUSD':1}}}
        self.shot={'imageProvider':'comfyui','imageModel':'klein','workflow':'klein','qc':{}}
        self.service=SimpleNamespace(config={'runpodManagedPodId':'test-pod'},
            store=SimpleNamespace(root=Path('unused-fixture')),providers={'comfyui':Mock()},
            cv=threading.Condition(),db=Mock(),gate=Mock(),record_gpu_state=Mock(),
            paused=False,cancel=False,closed=False)
        self.rental=Mock(project='test')
    def test_contiguous_images_share_rental_then_stop_before_other_project(self):
        with patch('studio_runpod.ProductionGPU',return_value=self.rental) as factory:
            StudioService.begin_queued_image_rental(self.service,self.project,self.job,{'shotSnapshot':self.shot})
            self.service.db.execute.return_value.fetchone.return_value={'project':'test','kind':'image'}
            StudioService.finish_queued_image_rental(self.service,self.job,'COMPLETE')
            self.rental.stop.assert_not_called()
            StudioService.begin_queued_image_rental(self.service,self.project,self.job,{'shotSnapshot':self.shot})
            self.assertEqual(factory.call_count,1)
            self.service.db.execute.return_value.fetchone.return_value={'project':'other','kind':'image'}
            StudioService.finish_queued_image_rental(self.service,self.job,'COMPLETE')
            self.rental.stop.assert_called_once();self.assertIsNone(self.service._queued_image_rental)
    def test_failure_and_pause_stop_even_with_more_images(self):
        for status,paused in [('FAILED',False),('COMPLETE',True),('CANCELLED',False)]:
            self.service._queued_image_rental=self.rental;self.service.paused=paused
            self.service.db.execute.return_value.fetchone.return_value={'project':'test','kind':'image'}
            StudioService.finish_queued_image_rental(self.service,self.job,status)
            self.assertIsNone(self.service._queued_image_rental)
        self.assertEqual(self.rental.stop.call_count,3)
    def test_unbudgeted_repair_never_rents_gpu(self):
        self.shot.update(imagePath='saved.png',qc={'pass':False})
        with patch('studio_runpod.ProductionGPU') as factory:
            with self.assertRaisesRegex(ValueError,'unverified'):
                StudioService.begin_queued_image_rental(self.service,self.project,self.job,{'shotSnapshot':self.shot})
            factory.assert_not_called()
