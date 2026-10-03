import unittest, tempfile, wave
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project
from studio_render import VideoRenderer, effective_motion


class MotionTest(unittest.TestCase):
    def shot(self, camera="medium", duration=10, **extra):
        return {"start": 0, "end": duration, "camera": {"shot": camera}, "motion": "static", **extra}

    def test_gentle_movement_follows_composition_and_preserves_manual_holds(self):
        video = {"motionMode": "gentle"}
        self.assertEqual(effective_motion(self.shot(), video), "slow zoom in")
        self.assertEqual(effective_motion(self.shot("close-up"), video), "slow zoom out")
        self.assertEqual(effective_motion(self.shot("establishing shot"), video), "pan right")
        self.assertEqual(effective_motion(self.shot("insert shot"), video), "static")
        self.assertEqual(effective_motion(self.shot(duration=1), video), "static")
        self.assertEqual(effective_motion(self.shot(manual={"motion": True}), video), "static")
        self.assertEqual(effective_motion(self.shot(motion="pan left"), video), "pan left")
        self.assertEqual(effective_motion(self.shot(), {}), "static")
        self.assertEqual(effective_motion(self.shot(motion="slow zoom in"), {"motionMode": "static"}), "static")

    def test_cover_never_adds_borders_and_zoom_reaches_its_end(self):
        renderer = VideoRenderer(None, {})
        video = {"width": 1280, "height": 720, "fps": 24, "imageFit": "cover", "motionMode": "gentle"}
        zoom_in = renderer.motion(self.shot(), video, 48)
        zoom_out = renderer.motion(self.shot("close-up"), video, 48)
        self.assertIn("force_original_aspect_ratio=increase,crop=2560:1440", zoom_in)
        self.assertNotIn("pad=", zoom_in)
        self.assertIn("1+0.06*on/47", zoom_in)
        self.assertIn("1.06-0.06*on/47", zoom_out)
        self.assertIn("pad=", renderer.motion(self.shot(), video | {"imageFit": "contain"}, 48))

    def test_prose_director_motion_receives_valid_gentle_fallback(self):
        video={'motionMode':'gentle'}
        self.assertEqual(effective_motion(self.shot(motion='A slow dramatic reaction'),video),'slow zoom in')
        self.assertEqual(effective_motion(self.shot('close-up',motion='A held moment of shock'),video),'slow zoom out')
        self.assertEqual(effective_motion(self.shot(motion='Unsupported action',manual={'motion':True}),video),'static')


class IntroTest(unittest.TestCase):
    def test_thirty_second_intro_moves_and_never_cuts_long_narration(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder)
            p=new_project('Opening')
            p['intro'].update(enabled=True,duration=30,motion='slow zoom in')
            p=store.save(p)
            renderer=VideoRenderer(store,{})
            with patch.object(renderer,'run') as render:
                renderer.intro(p,lambda *args:None)
                filters=render.call_args.args[0]
                self.assertIn("1+0.06*on/719",filters[filters.index('-vf')+1])
                self.assertNotIn("drawtext",filters[filters.index('-vf')+1])
                self.assertFalse((store.folder(p['id'])/'intro-title.txt').exists())
                self.assertEqual(filters[filters.index('-t')+1],'30.0')
            audio=store.folder(p['id'])/'intro.wav'
            with wave.open(str(audio),'wb') as output:
                output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
                output.writeframes(b'\0'*(31*24000*2))
            p['intro']['audioPath']='intro.wav'
            with self.assertRaisesRegex(ValueError,'will not be cut off'):
                renderer.intro(p,lambda *args:None)

    def test_intro_montage_uses_each_asset_and_rejects_missing_coverage(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder); p=new_project('Never burn this title')
            for name in ('a.png', 'b.png'):
                store.folder(p['id']).mkdir(exist_ok=True)
                (store.folder(p['id'])/name).write_bytes(b'fixture')
            p['intro'].update(duration=30,shots=[
                {'start':0,'end':12,'imagePath':'a.png','motion':'slow zoom in'},
                {'start':12,'end':30,'imagePath':'b.png','motion':'pan right'}])
            renderer=VideoRenderer(store,{})
            with patch.object(renderer,'run') as run:
                renderer.intro(p,lambda *args:None)
                self.assertEqual(run.call_count,4)
                self.assertTrue(all('drawtext' not in str(c) for c in run.call_args_list))
            p['intro']['shots'][1]['start']=13
            with self.assertRaisesRegex(ValueError,'gaps or overlaps'):
                with patch.object(renderer,'run'):
                    renderer.intro(p,lambda *args:None)

if __name__ == "__main__":
    unittest.main()
