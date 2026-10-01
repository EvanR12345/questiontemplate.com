import tempfile
import threading
import time
import unittest
from PIL import Image
from image_queue import ImageQueue

def wait_for(predicate, timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for queue transition')

class QueueTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.calls=[]
        self.entered=threading.Event()
        def generate(data, checkpoint):
            self.calls.append(data['seed'])
            for step in range(10):
                self.entered.set()
                checkpoint(step+1,10,'Denoising')
                time.sleep(.01)
            return {'pil':Image.new('RGB',(8,8),'red'),'seed':data['seed'],'seconds':.1}
        self.generate=generate
        self.queue=ImageQueue(self.tmp.name,generate,threading.Lock())

    def tearDown(self):
        self.queue.close()
        self.tmp.cleanup()

    def test_pause_resume_and_save(self):
        self.queue.enqueue({'jobs':[{'prompt':'apple','seed':0},{'prompt':'pear','seed':1}]})
        self.assertTrue(self.entered.wait(2))
        self.queue.control('pause')
        time.sleep(.05)
        step=self.queue.snapshot()['jobs'][0]['step']
        time.sleep(.08)
        self.assertEqual(self.queue.snapshot()['jobs'][0]['step'],step)
        self.queue.control('resume')
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==2)
        self.assertEqual(self.calls,[0,1])
        for job in self.queue.snapshot()['jobs']:
            self.assertTrue(self.queue.result_path(job['id']).exists())

    def test_cancel_all_while_paused_stops_everything(self):
        self.queue.enqueue({'jobs':[{'prompt':'test','seed':i} for i in range(1000)]})
        self.assertTrue(self.entered.wait(2))
        self.queue.control('pause')
        self.queue.control('cancel-all')
        wait_for(lambda:self.queue.snapshot()['counts']['canceled']==1000)
        self.assertEqual(len(self.calls),1)
        self.assertEqual(self.queue.snapshot()['counts']['completed'],0)

    def test_cancel_current_continues_queue(self):
        self.queue.enqueue({'jobs':[{'prompt':'test','seed':i} for i in range(3)]})
        self.assertTrue(self.entered.wait(2))
        self.queue.control('cancel-current')
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==2)
        self.assertEqual(self.queue.snapshot()['counts']['canceled'],1)

    def test_restart_restores_work_paused_and_seed_zero(self):
        self.queue.control('pause')
        self.queue.enqueue({'jobs':[{'prompt':'test','seed':0}]})
        self.queue.close()
        self.queue=ImageQueue(self.tmp.name,self.generate,threading.Lock())
        self.assertTrue(self.queue.snapshot()['paused'])
        self.queue.control('resume')
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        self.assertEqual(self.calls,[0])

    def test_audio_switch_preserves_current_job_and_resumes(self):
        self.queue.enqueue({'jobs':[{'prompt':'test','seed':15}]})
        self.assertTrue(self.entered.wait(2))
        self.queue.control('yield-audio')
        wait_for(lambda:self.queue.snapshot()['current'] is None)
        self.assertTrue(self.queue.snapshot()['paused'])
        self.assertEqual(self.queue.snapshot()['counts']['queued'],1)
        self.queue.control('resume')
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        self.assertEqual(self.calls,[15,15])

    def test_shutdown_preserves_unfinished_job(self):
        self.queue.enqueue({'jobs':[{'prompt':'test','seed':19}]})
        self.assertTrue(self.entered.wait(2))
        self.queue.close()
        self.queue=ImageQueue(self.tmp.name,self.generate,threading.Lock())
        self.assertTrue(self.queue.snapshot()['paused'])
        self.assertEqual(self.queue.snapshot()['counts']['queued'],1)

    def test_validation_is_atomic_and_limit_is_enforced(self):
        self.queue.control('pause')
        with self.assertRaises(ValueError): self.queue.enqueue({'jobs':[{'prompt':'ok'},{'prompt':''}]})
        self.assertFalse(self.queue.snapshot()['jobs'])
        self.queue.enqueue({'jobs':[{'prompt':'ok'} for _ in range(1000)]})
        with self.assertRaises(ValueError): self.queue.enqueue({'jobs':[{'prompt':'over capacity'}]})
        for value in [float('nan'),True,-1]:
            with self.assertRaises(ValueError): self.queue.enqueue({'jobs':[{'prompt':'ok','guidance':value}]})

    def test_repeated_failures_pause_the_queue(self):
        self.queue.close()
        def fail(data,checkpoint): raise RuntimeError('VAE invalid')
        self.queue=ImageQueue(self.tmp.name,fail,threading.Lock())
        self.queue.control('resume')
        self.queue.enqueue({'jobs':[{'prompt':'ok'} for _ in range(6)]})
        wait_for(lambda:self.queue.snapshot()['paused'])
        self.assertEqual(self.queue.snapshot()['counts']['failed'],3)
        self.assertEqual(self.queue.snapshot()['counts']['queued'],3)

if __name__=='__main__': unittest.main()
