"""Owned-lifetime and admission checks; no network or GPU operations."""
import unittest
from unittest.mock import patch

from image_provider import ImageAdmissionBlocked
from studio_image_workers import ComfyWorkerPool, make_comfy_provider


REPORT={'gpu':'NVIDIA GeForce RTX 5090',
        'workers':[{'pid':101,'port':8188},{'pid':102,'port':8189}]}


class OwnedPoolTest(unittest.TestCase):
    def pool(self):
        return ComfyWorkerPool({'comfyModelCopies':2,'comfyPersistentHTTP':False})

    def proof(self, token='rental-a'):
        return {'owned':True,'ready':True,'podId':'owned-pod',
                'lifetimeId':token,'endpoint':'http://127.0.0.1:8188'}

    def ready(self,pool,token='rental-a'):
        pool.confirm_workers(REPORT)
        pool.begin_worker_lifetime(self.proof(token))

    def test_actual_factory_preserves_four_pool_slots_and_external_single_slot(self):
        external=make_comfy_provider({'comfyPersistentHTTP':False})
        single=make_comfy_provider({'runpodManagedPodId':'owned-pod','comfyModelCopies':1,
                                   'comfyPersistentHTTP':False})
        pooled=make_comfy_provider({'runpodManagedPodId':'owned-pod','comfyModelCopies':2,
                                   'comfyPersistentHTTP':False})
        self.assertEqual(external.client_capacity,1)
        self.assertEqual(single.client_capacity,2)
        self.assertEqual(pooled.client_capacity,4)
        self.assertEqual([w.client_capacity for w in pooled.workers],[2,2])
        with self.assertRaises(ImageAdmissionBlocked):single.generateImage({},lambda *_:None)

    def test_readiness_metadata_and_same_token_cannot_clear_uncertainty(self):
        pool=self.pool()
        with self.assertRaisesRegex(RuntimeError,'startup'):pool.generateImage({},lambda *_:None)
        self.ready(pool)
        for worker in pool.workers:worker.block_new_dispatch()
        pool.quarantined=True
        pool.invalidate_backend_cache()
        self.assertTrue(all(w._uncertain.is_set() for w in pool.workers))
        pool.prepare_worker_lifetime()
        self.assertTrue(pool.quarantined)
        self.assertFalse(pool.lifetime_verified)
        pool.confirm_workers(REPORT)
        with self.assertRaisesRegex(RuntimeError,'same worker'):
            pool.begin_worker_lifetime(self.proof())
        pool.begin_worker_lifetime(self.proof('verified-fresh-rental'))
        self.assertTrue(pool.lifetime_verified)
        self.assertFalse(pool.quarantined)
        self.assertTrue(all(not w._uncertain.is_set() for w in pool.workers))
        self.assertEqual(pool.workers[1]._worker_lifetime,
                         ('owned-pod','verified-fresh-rental:worker:1'))

    def test_active_pool_calls_refuse_readiness_and_lifetime_reset(self):
        pool=self.pool();self.ready(pool)
        pool.active[1]=1
        for action in (pool.prepare_worker_lifetime,
                       lambda:pool.confirm_workers(REPORT),
                       lambda:pool.begin_worker_lifetime(self.proof('next-rental'))):
            with self.assertRaises(RuntimeError):action()
        self.assertEqual(pool._lifetime_id,'rental-a')
        self.assertEqual(pool.active,[0,1])

    def test_partial_child_reset_failure_never_reopens_pool_admission(self):
        pool=self.pool();self.ready(pool)
        with patch.object(pool.workers[1],'begin_worker_lifetime',side_effect=RuntimeError('busy child')):
            with self.assertRaisesRegex(RuntimeError,'busy child'):
                pool.begin_worker_lifetime(self.proof('next-rental'))
        self.assertTrue(pool.quarantined)
        self.assertFalse(pool.lifetime_verified)
        with self.assertRaisesRegex(RuntimeError,'startup'):pool.generateImage({},lambda *_:None)

    def test_unowned_or_wrong_endpoint_proof_cannot_activate_pool(self):
        pool=self.pool();pool.confirm_workers(REPORT)
        for change in ({'owned':False},{'ready':False},{'podId':''},
                       {'endpoint':'http://127.0.0.1:9999'},{'lifetimeId':''}):
            with self.assertRaises(RuntimeError):pool.begin_worker_lifetime({**self.proof(),**change})
        self.assertFalse(pool.lifetime_verified)


if __name__=='__main__':unittest.main()
