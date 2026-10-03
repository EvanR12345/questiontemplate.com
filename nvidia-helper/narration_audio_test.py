import unittest
from narration_audio import speech_text, audio_segments, effect_pcm, pronunciation_text

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
            self.assertGreater(float(np.std(pcm)), .002)
            self.assertLessEqual(float(np.abs(pcm).max()), .121)
            self.assertEqual(pcm[-1], 0)

    def test_mixed_screams_and_actual_g2p_do_not_spell_letters(self):
        from kokoro import KPipeline
        pipeline = KPipeline(lang_code='a', repo_id='hexgrad/Kokoro-82M', model=False)
        for spelling in ('ahhh!', 'Aaaaahhh!', 'Haaahh!', 'Aggghhhh!', 'Aaah!'):
            prepared = audio_segments(spelling)[0]
            self.assertEqual(prepared['kind'], 'pause')
            self.assertNotIn('ttsText', prepared)
        self.assertEqual(pronunciation_text("Shah can't leave. NASA."), "Shah can't leave. NASA.")

    def test_unmarked_cues_are_effects_only_in_isolation(self):
        self.assertEqual(audio_segments('Thud.')[0]['effect'], 'thud')
        self.assertEqual(audio_segments('"Slash!"')[0]['effect'], 'slash')
        self.assertEqual(audio_segments('There was a thud. Slash the rope.')[0]['kind'], 'speech')
        self.assertEqual(audio_segments('He said, Ah, I cannot leave.')[1]['kind'], 'pause')
        self.assertEqual(audio_segments("Shah can't leave.")[0]['ttsText'], "Shah can't leave.")
        self.assertEqual(audio_segments('*Boom*', 'off')[0]['kind'], 'pause')

    def test_effect_level_tracks_quiet_narration(self):
        import numpy as np
        pcm = effect_pcm('slash', narration_rms=.01)
        self.assertLessEqual(float(np.sqrt(np.mean(pcm**2))), .0061)
        self.assertLessEqual(float(np.abs(pcm).max()), .081)

    def test_preview_streams_one_wav_without_synthesizing_cries_or_touching_chapter(self):
        import copy, tempfile, wave
        from pathlib import Path
        from unittest.mock import Mock, patch
        from studio_service import StudioService
        from studio_data import ProjectStore, new_project
        import numpy as np
        with tempfile.TemporaryDirectory() as folder:
            service = object.__new__(StudioService)
            service.store = ProjectStore(folder)
            service.audio = Mock()
            service.audio.model.vocab = {'a':1}
            service.audio.synthesize.return_value = np.full(2400,.02,dtype='<f4').tobytes()
            service.gate = lambda *_: None
            p = service.store.save(new_project())
            before = copy.deepcopy(p['chapters'])
            with patch('kokoro.KPipeline') as pipeline:
                pipeline.return_value.side_effect = lambda text:[Mock(phonemes='a')]
                service.voice_preview(p, {'text':"Ahhh!\nThud.\nI can't leave.", 'voice':'am_michael', 'speed':1})
                spoken = [call.args[0] for call in pipeline.return_value.call_args_list]
                self.assertEqual(spoken, ["I can't leave."])
            saved = service.store.load(p['id'])
            self.assertEqual(saved['chapters'], before)
            self.assertEqual(len(saved['voicePreviews']), 1)
            preview = saved['voicePreviews'][0]
            with wave.open(str(service.store.asset(p['id'],preview['path']))) as wav:
                self.assertGreater(wav.getnframes(),2400)
                self.assertEqual(wav.getframerate(),24000)

    def test_distinct_auditions_queue_while_duplicate_clicks_do_not(self):
        import tempfile, threading
        from unittest.mock import Mock
        from studio_service import StudioService
        from studio_data import new_project
        class IdleService(StudioService):
            def worker(self): pass
        with tempfile.TemporaryDirectory() as folder:
            service = IdleService(folder,Mock(gpu='test'),lambda:None,threading.Lock(),lambda:None,lambda:None,Mock())
            p = service.store.save(new_project())
            try:
                for voice in ('am_michael','am_fenrir','am_michael'):
                    service.enqueue(p['id'], p['chapters'][0]['id'], 'voice-preview', options={'voice':voice,'text':'Hello.'})
                jobs = [dict(j) for j in service.db.execute("SELECT * FROM jobs")]
                self.assertEqual(len(jobs),2)
                self.assertTrue(all(j['chapter'] is None for j in jobs))
            finally: service.close()

if __name__ == '__main__':
    unittest.main()
