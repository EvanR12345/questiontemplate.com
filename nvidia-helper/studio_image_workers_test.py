import threading,time,unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from studio_image_workers import ComfyWorkerPool,model_copies,worker_endpoints
from studio_worker_bootstrap import remote_script


class ImageWorkersTest(unittest.TestCase):
    def pool(self, generate=None):
        workers=[]
        def factory(config):
            index=len(workers)
            worker=SimpleNamespace(config=config,_uploads={},
                invalidate_backend_cache=lambda:None,healthCheck=lambda **kw:{'installed':True},
                unload=lambda:None,generateImage=lambda request,checkpoint:generate(index,request) if generate else {})
            workers.append(worker);return worker
        pool=ComfyWorkerPool({'comfyModelCopies':2},factory)
        pool.confirm_workers({'gpu':'NVIDIA GeForce RTX 5090','workers':[{'pid':100,'port':8188},{'pid':101,'port':8189}]})
        return pool

    def test_two_is_default_only_for_an_owned_5090(self):
        self.assertEqual(model_copies({}),1)
        self.assertEqual(model_copies({'runpodManagedPodId':'prepared','runpodRequiredGPUType':'NVIDIA GeForce RTX 5090'}),2)
        for value in (True,0,3,'2'):
            with self.assertRaises(ValueError):model_copies({'comfyModelCopies':value})
        self.assertEqual(worker_endpoints({'comfyModelCopies':2,'comfyEndpoint':'http://127.0.0.1:9188'}),['http://127.0.0.1:9188','http://127.0.0.1:9189'])
        with self.assertRaises(ValueError):worker_endpoints({'comfyModelCopies':2,'comfyEndpoint':'https://public.example'})

    def test_process_confirmation_is_required_before_any_image_request(self):
        pool=self.pool();pool.confirmation=None
        with self.assertRaisesRegex(RuntimeError,'startup'):pool.generateImage({},lambda *_:None)
        with self.assertRaisesRegex(RuntimeError,'verified'):pool.confirm_workers({'gpu':'NVIDIA GeForce RTX 5090','workers':[{'pid':1,'port':8188},{'pid':1,'port':8189}]})

    def test_four_slots_share_two_distinct_workers_without_changing_request_inputs(self):
        barrier=threading.Barrier(4);seen=[];lock=threading.Lock()
        def generate(index,request):
            with lock:seen.append((index,request['prompt'],request['settings']['seed'],request['referenceImages']))
            barrier.wait(timeout=3)
            request['_executionObserver']('accepted',{'operationId':'op'+str(request['settings']['seed']),'remoteId':'job'})
            return {'seed':request['settings']['seed']}
        pool=self.pool(generate);events=[]
        with ThreadPoolExecutor(max_workers=4) as threads:
            results=list(threads.map(lambda i:pool.generateImage({'prompt':'Grounded scene '+str(i),'settings':{'seed':i},'referenceImages':['exact-reference'],
                '_executionObserver':lambda event,details:events.append(details)},lambda *_:None),range(4)))
        self.assertEqual([index for index, *_ in seen].count(0),2);self.assertEqual([index for index,*_ in seen].count(1),2)
        self.assertEqual(sorted(r['seed'] for r in results),list(range(4)))
        self.assertEqual({e['workerIndex'] for e in events},{0,1});self.assertEqual(pool.active,[0,0])
        self.assertTrue(all(row[3]==['exact-reference'] for row in seen))

    def test_failed_request_releases_slot_without_automatic_replay(self):
        calls=[]
        def fail(index,request):calls.append(index);raise RuntimeError('Uncertain remote outcome')
        pool=self.pool(fail)
        with self.assertRaisesRegex(RuntimeError,'Uncertain'):pool.generateImage({},lambda *_:None)
        self.assertEqual(len(calls),1);self.assertEqual(pool.active,[0,0])

    def test_unknown_remote_outcome_blocks_new_dispatch_until_new_verified_lifetime(self):
        from image_provider import RemoteGenerationUncertain
        calls=[]
        def generate(index,request):calls.append(index);raise RemoteGenerationUncertain('owned-job')
        pool=self.pool(generate)
        with self.assertRaises(RemoteGenerationUncertain):pool.generateImage({},lambda *_:None)
        with self.assertRaisesRegex(RuntimeError,'unresolved'):pool.generateImage({},lambda *_:None)
        self.assertEqual(len(calls),1)
        pool.begin_worker_lifetime();self.assertIsNone(pool.confirmation)
        with self.assertRaisesRegex(RuntimeError,'startup'):pool.generateImage({},lambda *_:None)

    def test_cancelled_waiting_request_never_submits_or_interrupts_another_worker(self):
        pool=self.pool();pool.active=[2,2]
        with self.assertRaisesRegex(RuntimeError,'cancelled'):
            pool.generateImage({},lambda *_:(_ for _ in ()).throw(RuntimeError('cancelled')))
        self.assertEqual(pool.active,[2,2])

    def test_remote_bootstrap_keeps_weights_shared_and_ports_private(self):
        script=remote_script(2);compile(script,'remote-bootstrap','exec')
        self.assertIn('MODELS_VERIFIED',script);self.assertIn('WORKERS_VERIFIED',script)
        self.assertIn("'--listen','127.0.0.1'",script)
        self.assertNotIn('0.0.0.0',script)
        self.assertNotIn('WORKERS_VERIFIED',remote_script(1))


if __name__=='__main__':unittest.main()
