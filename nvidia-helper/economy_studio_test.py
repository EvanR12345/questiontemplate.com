import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from economy_studio import pending_panels, cloud_cost

class EconomyResumeTest(unittest.TestCase):
    def test_partial_group_preserves_only_valid_saved_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'saved.png').write_bytes(b'image')
            good = dict(id='saved',imagePath='saved.png',status='COMPLETE',generationStale=False)
            missing = dict(id='missing',imagePath='lost.png',status='COMPLETE')
            stale = dict(good,id='stale',generationStale=True)
            waiting = dict(id='waiting',status='READY_FOR_IMAGES')
            self.assertEqual([s['id'] for s in pending_panels([good,missing,stale,waiting],root)],['missing','stale','waiting'])
            self.assertEqual(good['imagePath'],'saved.png')

    def test_budget_counts_previous_rental_and_stop_reserve(self):
        with patch('economy_studio.time.time',return_value=1100):
            self.assertAlmostEqual(cloud_cost(1000,2.111,.156,25),.156+125*2.111/3600)

if __name__ == '__main__':
    unittest.main()
