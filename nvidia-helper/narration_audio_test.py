import unittest
from narration_audio import speech_text, audio_segments, effect_pcm

class NarrationDeliveryTest(unittest.TestCase):
    def test_shouts_and_contractions(self):
        self.assertEqual(speech_text('"NOOOOO!!!!"'), 'No!')
        self.assertEqual(speech_text('"Aaaaacckkkkkkk!!"'), 'Ah!')
        self.assertEqual(speech_text("'Boss' can't leave. NASA is here."), "Boss can't leave. NASA is here.")
        self.assertEqual(speech_text('"H-He is dead!!"'), 'H-He is dead!')

    def test_effects_and_emphasis_keep_order(self):
        parts = audio_segments('He shouted "NOOOOO!!!!". **BOOOOOOOOOOOOOOOOOOOM** *Bang* **very brave**')
        self.assertEqual([p['kind'] for p in parts], ['speech', 'effect', 'effect', 'speech'])
        self.assertEqual([p['effect'] for p in parts if p['kind'] == 'effect'], ['boom', 'bang'])
        self.assertEqual(parts[-1]['text'], 'very brave')
        self.assertEqual(len(audio_segments('*Bang* *Bang* *Bang*')), 3)
        self.assertEqual(audio_segments('"…"'), [])

    def test_effect_is_finite_visible_audio_not_letters(self):
        import numpy as np
        for name in ('bang', 'boom', 'beep', 'slash'):
            pcm = effect_pcm(name)
            self.assertTrue(np.isfinite(pcm).all())
            self.assertGreater(float(np.std(pcm)), .01)
            self.assertLessEqual(float(np.abs(pcm).max()), .5)
            self.assertEqual(pcm[-1], 0)

if __name__ == '__main__':
    unittest.main()
