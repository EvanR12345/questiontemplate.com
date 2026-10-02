import tempfile
import threading
import time
import unittest
from PIL import Image
from image_queue import ImageQueue, validate_job

def wait_for(predicate, timeout=4):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate(): return
        time.sleep(.01)
    raise AssertionError('Timed out waiting for queue transition')

class QueueTest(unittest.TestCase):
    def test_scene_anchor_is_resolved_after_first_queued_image_finishes(self):
        payloads = []
        original = self.queue.generate
        def capture(data, checkpoint):
            payloads.append(data)
            return original(data, checkpoint)
        self.queue.generate = capture
        self.queue.enqueue({'project':'story', 'jobs':[
            {'prompt':'first scene image','target':'anchor','kind':'panel','seed':0},
            {'prompt':'next action','target':'next','kind':'panel','seed':1,'continuity_target':'anchor'},
        ]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==2)
        self.assertEqual(payloads[0]['reference_images'], [])
        self.assertTrue(payloads[1]['continuity_used'])
        self.assertTrue(payloads[1]['reference_images'][0].startswith('data:image/png;base64,'))
        self.assertLessEqual(payloads[1]['reference_strength'], .25)

    def test_background_anchor_is_skipped_for_a_people_action(self):
        self.queue.enqueue({'project':'story', 'jobs':[{'prompt':'An empty room.', 'target':'anchor','kind':'panel'}]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        data=self.queue._expand({'prompt':'A woman watches a man falling.', 'reference_images':[], 'continuity_target':'anchor'},'story')
        self.assertEqual(data['reference_images'],[])
        self.assertIn('foreground subjects',data['continuity_skipped'])

    def test_legacy_sound_anchor_is_skipped_without_removing_its_saved_image(self):
        self.queue.enqueue({'project':'story', 'jobs':[{'prompt':'An empty room.', 'target':'anchor','kind':'panel'}]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        with self.queue.cv:
            self.queue.db.execute('UPDATE jobs SET payload=?', ('{"prompt":"Bang. in the room."}',))
            self.queue.db.commit()
        data=self.queue._expand({'prompt':'A man falls.', 'reference_images':[], 'continuity_target':'anchor'},'story')
        self.assertEqual(data['reference_images'],[])
        self.assertIn('sound effect',data['continuity_skipped'])
        self.assertTrue(self.queue.result_path(self.queue.snapshot()['jobs'][0]['id']).exists())

    def test_sound_only_action_rejected_but_visible_screaming_action_accepted(self):
        with self.assertRaisesRegex(ValueError,'sound effect'):
            validate_job({'kind':'panel','prompt':'Bang. in the room.'})
        validate_job({'kind':'panel','prompt':'A woman screams as a man falls. in the room.'})

    def test_scene_reference_does_not_raise_a_lower_strength(self):
        self.queue.enqueue({'project':'story','jobs':[{'prompt':'A man walks.','target':'anchor','kind':'panel'}]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        data=self.queue._expand({'prompt':'A man stands.', 'reference_images':[],'reference_strength':.1,'continuity_target':'anchor'},'story')
        self.assertEqual(data['reference_strength'],.1)

    def test_named_cast_retains_continuity_when_action_uses_a_pronoun(self):
        self.queue.enqueue({'project':'story','jobs':[{'prompt':'A woman named Mira stands.','cast_ids':['m'],'target':'anchor','kind':'panel'}]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        data=self.queue._expand({'prompt':'She raises a sword.', 'cast_ids':['m'],'reference_images':[],'continuity_target':'anchor'},'story')
        self.assertTrue(data['continuity_used'])

    def test_scene_anchor_cannot_reference_a_different_project(self):
        self.queue.enqueue({'project':'other', 'jobs':[{'prompt':'first','target':'anchor','kind':'panel'}]})
        wait_for(lambda:self.queue.snapshot()['counts']['completed']==1)
        data = self.queue._expand({'reference_images':[], 'continuity_target':'anchor'}, 'story')
        self.assertEqual(data['reference_images'], [])

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
