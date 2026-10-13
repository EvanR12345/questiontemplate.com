import threading,unittest
from unittest.mock import patch
import studio_overlap_test as fixtures
from studio_data import get_chapter
from studio_overlap import CloudStoryOverlap


class PhaseOverlapTest(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown
    response=fixtures.CloudOverlapTest.response
    run_story=fixtures.CloudOverlapTest.run_story

    def test_saved_api_capacity_is_honored_even_below_three(self):
        for capacity in (1,2,4):
            with self.subTest(capacity=capacity):
                project=self.service.store.load(self.project['id']);project['settings']['director'].update(parallelism=capacity,executionMode='staged-lean')
                overlap=CloudStoryOverlap(self.service,project,{'overlap':True})
                try:self.assertEqual(overlap.api_lane.capacity,capacity)
                finally:overlap.close('CANCELLED','Capacity fixture complete')

    def test_next_narration_runs_while_current_chapter_direction_is_active(self):
        entered=threading.Event();narrated=threading.Event()
        original_audio=self.service.__class__.narration
        original_direction=self.service.__class__.analyze
        self.service.wait_for_overlap=False
        def audio(service,p,ch):
            if ch['number']==2:
                self.assertTrue(entered.wait(4),'Chapter1 direction did not start')
                result=original_audio(service,p,ch);narrated.set();return result
            return original_audio(service,p,ch)
        def direct(service,p,chid,options):
            if get_chapter(p,chid)['number']==1:
                entered.set();self.assertTrue(narrated.wait(4),'Next narration did not overlap current direction')
            return original_direction(service,p,chid,options)
        with patch.object(self.service.__class__,'narration',audio),patch.object(self.service.__class__,'analyze',direct):
            self.run_story()
        self.assertTrue(narrated.is_set())
        self.assertEqual(self.service.store.load(self.project['id'])['production']['status'],'COMPLETE')

    def test_ready_chapter_renders_before_the_next_chapter_image_finishes(self):
        rendered=threading.Event();original_generate=self.provider.generateImage
        original_render=self.service.renderer.chapter
        def generate(request,checkpoint):
            result=original_generate(request,checkpoint)
            if len(self.provider.requests)==2:
                self.assertTrue(rendered.wait(4),'Ready chapter did not render during later image work')
            return result
        def render(p,ch,gate):
            result=original_render(p,ch,gate)
            if ch['number']==1:rendered.set()
            return result
        self.provider.generateImage=generate;self.service.renderer.chapter=render
        self.run_story()
        self.assertTrue(rendered.is_set())
        self.assertEqual(self.service.store.load(self.project['id'])['production']['status'],'COMPLETE')


if __name__=='__main__':unittest.main()
