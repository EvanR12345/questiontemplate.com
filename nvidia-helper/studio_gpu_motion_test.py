import math
import unittest
from studio_gpu_motion import motion_coordinates


class MotionEndpointTest(unittest.TestCase):
    def test_zoom_out_never_crosses_identity_at_last_frame(self):
        # 1 + .16 - .16 previously rounded below 1 and failed native validation.
        for frames in (1, 2, 115, 180, 997):
            for easing in ('linear', 'smooth'):
                for amount in (0, .06, .16, .35):
                    for index in range(frames):
                        zoom, x, y = motion_coordinates('slow zoom out',1280,720,index,frames,amount,easing)
                        self.assertGreaterEqual(zoom,1)
                        self.assertTrue(all(math.isfinite(v) for v in (zoom,x,y)))
                    self.assertEqual(motion_coordinates('slow zoom out',1280,720,frames-1,frames,amount,easing),(1.,0.,0.))


if __name__=='__main__':unittest.main()
