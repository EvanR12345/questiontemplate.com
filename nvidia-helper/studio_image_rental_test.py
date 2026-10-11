"""Standalone cloud edits own one guarded rental without touching audio."""
import threading,unittest,tempfile,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio_service import StudioService

class ImageRentalTest(unittest.TestCase):
    def test_reconnect_catalogue_skips_stopped_worker_without_claiming_live_readiness(self):
        with tempfile.TemporaryDirectory() as folder:
            service=SimpleNamespace(config={'runpodManagedPodId':'test-pod'},store=SimpleNamespace(root=Path(folder)))
            provider=Mock();provider.planningCatalog.return_value={'installed':False,'configured':True,'models':['klein']}
            for status in (None,'STOPPED','UNKNOWN'):
                if status:(Path(folder)/'.managed-rental.json').write_text(json.dumps({'status':status}))
                result=StudioService.provider_status(service,'comfyui',provider)
                self.assertFalse(result['installed']);self.assertTrue(result['liveProbeSkipped']);self.assertTrue(result['requiresLiveValidation'])
            provider.healthCheck.assert_not_called()
            (Path(folder)/'.managed-rental.json').write_text(json.dumps({'status':'RUNNING'}))
            provider.healthCheck.return_value={'installed':True}
            self.assertEqual(StudioService.provider_status(service,'comfyui',provider),{'installed':True})
            provider.healthCheck.assert_called_once()

    def setUp(self):
        self.job={'project':'test','chapter':'chapter','shot':'shot','kind':'image'}
        self.project={'id':'test','settings':{'cloudWindow':{'gpuBudgetUSD':1}},'chapters':[{'id':'chapter','people':[]}],'characters':[]}
        self.shot={'id':'shot','chapterId':'chapter','start':0,'end':1,'prompt':'A clear drawn scene.','characters':[],
            'imageProvider':'comfyui','imageModel':'klein','workflow':'klein','qc':{}}
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
    def test_invalid_prompt_never_buys_cold_startup(self):
        self.shot['prompt']=''
        with patch('studio_runpod.ProductionGPU') as factory:
            with self.assertRaisesRegex(ValueError,'preflight'):
                StudioService.begin_queued_image_rental(self.service,self.project,self.job,{'shotSnapshot':self.shot})
            factory.assert_not_called()
