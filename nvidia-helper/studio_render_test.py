import unittest
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


if __name__ == "__main__":
    unittest.main()
