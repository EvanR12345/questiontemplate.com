"""Marketplace startup ownership ordering with mocked subprocesses only."""
import json
import unittest
from unittest.mock import patch

from studio_cloud_comfy import CloudComfyRunner, COMFY_COMMIT
from studio_gpu_providers import GPUProviderError
from studio_image_workers import ComfyWorkerPool


WORKERS={'gpu':'NVIDIA GeForce RTX 5090',
         'workers':[{'pid':501,'port':8188},{'pid':502,'port':8189}]}


class Bootstrap:
    returncode=0
    def __init__(self,workers=None):self.workers=workers or WORKERS
    def communicate(self,**_):
        source={'commit':COMFY_COMMIT,'torchStack':{'torch':'2.10.0+cu128'}}
        return ('MODELS_VERIFIED\nWORKERS_VERIFIED '+json.dumps(self.workers)+
                '\nCOMFY_SOURCE_VERIFIED '+json.dumps(source),'')
    def poll(self):return 0


class Tunnel:
    def poll(self):return None


class MarketplaceLifetimeTest(unittest.TestCase):
    def runner(self,health=True,gate=None):
        pool=ComfyWorkerPool({'comfyModelCopies':2,'comfyPersistentHTTP':False})
        events=[]
        for name in ('prepare_worker_lifetime','confirm_workers','begin_worker_lifetime'):
            original=getattr(pool,name)
            def tracked(*args,_original=original,_name=name,**kwargs):
                events.append((_name,args))
                return _original(*args,**kwargs)
            setattr(pool,name,tracked)
        pool.healthCheck=lambda **_:events.append(('health',())) or {'installed':health}
        runner=CloudComfyRunner.__new__(CloudComfyRunner)
        runner.config={'comfyModelCopies':2,'comfyPersistentHTTP':False}
        runner.provider=pool;runner.ssh=['ssh','owned-host'];runner.process=None
        runner.tunnel=None;runner.report={};runner.gate=gate or (lambda *_:None)
        return runner,pool,events

    def prepare(self,runner,pod=None,workers=None):
        with patch('studio_cloud_comfy.remote_script',return_value='unchanged-model-script'), \
             patch('studio_cloud_comfy.install_script',return_value=''), \
             patch('studio_cloud_comfy.subprocess.Popen',side_effect=[Bootstrap(workers),Tunnel()]) as popen:
            runner.prepare(pod or {'provider':'vast','id':'owned-allocation'})
            return popen.call_count

    def test_owned_processes_and_health_precede_lifetime_activation(self):
        runner,pool,events=self.runner()
        self.assertEqual(self.prepare(runner),2)
        names=[name for name,_ in events]
        self.assertEqual(names,['prepare_worker_lifetime','confirm_workers','health','begin_worker_lifetime'])
        proof=events[-1][1][0]
        self.assertEqual(proof,{'owned':True,'ready':True,'podId':'owned-allocation',
            'lifetimeId':'vast:owned-allocation','endpoint':pool.url})
        self.assertTrue(pool.lifetime_verified)
        self.assertEqual([worker.client_capacity for worker in pool.workers],[2,2])

    def test_failed_health_never_clears_quarantine_or_activates_children(self):
        runner,pool,events=self.runner(health=False)
        pool.quarantined=True
        with patch('studio_cloud_comfy.time.monotonic',side_effect=[0,0,1,40]):
            with self.assertRaisesRegex(GPUProviderError,'health check'):
                self.prepare(runner)
        self.assertNotIn('begin_worker_lifetime',[name for name,_ in events])
        self.assertTrue(pool.quarantined)
        self.assertFalse(pool.lifetime_verified)
        self.assertTrue(all(worker._worker_lifetime is None for worker in pool.workers))

    def test_reconnection_with_same_remote_processes_cannot_forgive_unknown(self):
        runner,pool,events=self.runner();self.prepare(runner)
        for worker in pool.workers:worker.block_new_dispatch()
        pool.quarantined=True
        with self.assertRaisesRegex(RuntimeError,'same worker'):
            self.prepare(runner)
        self.assertTrue(pool.quarantined)
        self.assertFalse(pool.lifetime_verified)
        self.assertTrue(all(worker._uncertain.is_set() for worker in pool.workers))

    def test_missing_owned_identity_starts_no_subprocess(self):
        runner,pool,_=self.runner()
        with patch('studio_cloud_comfy.subprocess.Popen') as popen:
            with self.assertRaisesRegex(GPUProviderError,'identity'):
                runner.prepare({'provider':'vast'})
            popen.assert_not_called()
        self.assertFalse(pool.lifetime_verified)

    def test_process_replacement_on_same_allocation_cannot_forgive_unknown(self):
        for pids in ((501,503),(504,503)):
            with self.subTest(pids=pids):
                runner,pool,_=self.runner();self.prepare(runner)
                for worker in pool.workers:worker.block_new_dispatch()
                pool.quarantined=True
                report={'gpu':WORKERS['gpu'],'workers':[
                    {'pid':pid,'port':8188+index} for index,pid in enumerate(pids)]}
                with self.assertRaisesRegex(RuntimeError,'same worker'):
                    self.prepare(runner,workers=report)
                self.assertTrue(pool.quarantined)
                self.assertFalse(pool.lifetime_verified)
                self.assertTrue(all(worker._uncertain.is_set() for worker in pool.workers))

    def test_new_owned_allocation_can_retire_old_uncertainty(self):
        runner,pool,_=self.runner();self.prepare(runner)
        for worker in pool.workers:worker.block_new_dispatch()
        pool.quarantined=True
        self.prepare(runner,pod={'provider':'vast','id':'new-owned-allocation'})
        self.assertFalse(pool.quarantined)
        self.assertTrue(pool.lifetime_verified)
        self.assertFalse(any(worker._uncertain.is_set() for worker in pool.workers))


if __name__=='__main__':unittest.main()
