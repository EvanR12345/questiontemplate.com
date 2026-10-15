import copy,json,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch
from studio_voice_provider import identity,readiness,validate,KaggleVoice,MODELS
from studio_data import new_project
from studio_service import StudioService
from studio_engagement import audio_signature

class VoiceProviderTest(unittest.TestCase):
    def test_all_models_are_explicit_and_kokoro_is_unchanged(self):
        self.assertEqual(len(MODELS),4)
        self.assertEqual(identity({}),{})
        self.assertEqual(identity({'voiceEngine':'kokoro-82m'}),{})
        with self.assertRaises(ValueError):validate({'voiceEngine':'invented'})

    def test_model_and_instructions_invalidate_audio_without_touching_originals(self):
        p=new_project();ch=p['chapters'][0];ch['sourceText']='A quiet gate.'
        before=StudioService.narration_signature(p,ch);intro=StudioService.intro_audio_signature(p);outro=audio_signature(p)
        p['settings']['voiceEngine']='qwen3-tts-1.7b'
        self.assertNotEqual(before,StudioService.narration_signature(p,ch))
        self.assertNotEqual(intro,StudioService.intro_audio_signature(p))
        self.assertNotEqual(outro,audio_signature(p))
        first=StudioService.narration_signature(p,ch);p['settings']['voiceInstruction']='Calmer.'
        self.assertNotEqual(first,StudioService.narration_signature(p,ch))

    def test_qwen_speaker_changes_cache_and_unknown_speakers_are_rejected(self):
        settings={'voiceEngine':'qwen3-tts-1.7b'}
        before=identity(settings)
        self.assertEqual(before['qwenSpeaker'],'Ryan')
        settings['qwenSpeaker']='Aiden';validate(settings)
        self.assertNotEqual(before,identity(settings))
        settings['qwenSpeaker']='invented'
        with self.assertRaisesRegex(ValueError,'supported Qwen'):validate(settings)

    def test_worker_honors_the_selected_qwen_speaker_in_single_and_batched_paths(self):
        worker=Path(__file__).with_name('studio_kaggle_voice_worker.py').read_text(encoding='utf-8')
        self.assertIn("speaker=request.get('qwenSpeaker','Ryan')",worker)
        self.assertIn("speaker=[request.get('qwenSpeaker','Ryan')]*len(batch)",worker)

    def test_single_audition_cancel_never_cancels_other_project_jobs(self):
        import sqlite3,threading
        from unittest.mock import Mock
        service=StudioService.__new__(StudioService)
        service.db=sqlite3.connect(':memory:');service.db.row_factory=sqlite3.Row
        service.db.executescript("CREATE TABLE jobs(id TEXT,project TEXT,status TEXT,message TEXT); CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); INSERT INTO jobs VALUES('mine','p1','QUEUED',''),('other','p2','QUEUED','');")
        service.cv=threading.Condition();service.paused=False;service.current=None;service.cancel=False
        service.sync_queue_states=Mock();service.snapshot=Mock(return_value={})
        with self.assertRaisesRegex(ValueError,'this project'):service.control('cancel-job','other','p1')
        service.control('cancel-job','mine','p1')
        self.assertEqual(service.db.execute("SELECT status FROM jobs WHERE id='mine'").fetchone()[0],'CANCELLED')
        self.assertEqual(service.db.execute("SELECT status FROM jobs WHERE id='other'").fetchone()[0],'QUEUED')
        self.assertFalse(service.cancel);self.assertFalse(service.paused)
        service.db.close()

    def test_unverified_models_cannot_start_production_and_fish_license_is_enforced(self):
        with patch.object(KaggleVoice,'health',return_value={'reason':''}):
            self.assertIn('audition',readiness({'voiceEngine':'qwen3-tts-1.7b'},{}) or '')
            self.assertIsNone(readiness({'voiceEngine':'qwen3-tts-1.7b'},{},preview=True))
            self.assertIn('commercial license',readiness({'voiceEngine':'fish-s2-pro'},{}) or '')
            self.assertIsNone(readiness({'voiceEngine':'fish-s2-pro'},{},preview=True))
            self.assertIsNone(readiness({'voiceEngine':'qwen3-tts-1.7b'},{'kaggleVerifiedModels':['qwen3-tts-1.7b']}))

    def test_uncertain_submission_is_durable_and_not_resubmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'audio.wav';calls=[];provider=KaggleVoice({'kaggleUsername':'manhwastory2'})
            def uncertain(args,**kw):calls.append(args);raise ValueError('Timed out')
            with patch.object(KaggleVoice,'health',return_value={'reason':''}),patch.object(provider,'_run',side_effect=uncertain):
                with self.assertRaisesRegex(ValueError,'Timed out'):provider.generate('A quiet gate.',target,{'voiceEngine':'dia-1.6b'},lambda m:None,preview=True)
                with self.assertRaisesRegex(ValueError,'reconciliation'):provider.generate('A quiet gate.',target,{'voiceEngine':'dia-1.6b'},lambda m:None,preview=True)
            self.assertEqual(len(calls),1)
            metadata=json.loads(next(Path(directory).glob('.kaggle-*/kernel-metadata.json')).read_text())
            self.assertIs(metadata['is_private'],True)
            self.assertEqual(metadata['title'].lower().replace(' ','-'),metadata['id'].split('/')[1])
            self.assertFalse(target.exists())

    def test_kokoro_cloud_is_separate_from_the_working_local_cache(self):
        self.assertEqual(identity({'voiceEngine':'kokoro-82m','voiceCompute':'local'}),{})
        self.assertEqual(identity({'voiceEngine':'kokoro-82m','voiceCompute':'kaggle'})['voiceCompute'],'kaggle')

    def test_result_mismatch_cannot_overwrite_saved_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'audio.wav';target.write_bytes(b'original')
            provider=KaggleVoice({'kaggleUsername':'manhwastory2'})
            def run(args,**kw):
                if args[1]=='status':return 'complete'
                if args[1]=='pull':
                    folder=Path(args[args.index('-p')+1]);(folder/'kernel-metadata.json').write_text(json.dumps({'is_private':True}))
                if args[1]=='output':
                    folder=Path(args[args.index('-p')+1]);(folder/'result.json').write_text(json.dumps({'signature':'wrong','model':'dia-1.6b'}))
                return 'Kernel version successfully pushed.'
            with patch.object(KaggleVoice,'health',return_value={'reason':''}),patch.object(provider,'_run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'does not match'):provider.generate('A quiet gate.',target,{'voiceEngine':'dia-1.6b'},lambda m:None,preview=True)
            self.assertEqual(target.read_bytes(),b'original')

    def test_unconfirmed_privacy_stops_before_audio_download(self):
        with tempfile.TemporaryDirectory() as directory:
            provider=KaggleVoice({'kaggleUsername':'manhwastory2'});calls=[]
            def run(args,**kw):
                calls.append(args)
                if args[1]=='pull':
                    folder=Path(args[args.index('-p')+1]);(folder/'kernel-metadata.json').write_text(json.dumps({'is_private':False}))
                return 'Kernel version successfully pushed.'
            with patch.object(KaggleVoice,'health',return_value={'reason':''}),patch.object(provider,'_run',side_effect=run):
                with self.assertRaisesRegex(ValueError,'private notebook visibility'):
                    provider.generate('A quiet gate.',Path(directory)/'audio.wav',{'voiceEngine':'dia-1.6b'},lambda m:None,preview=True)
            self.assertFalse(any(args[1]=='output' for args in calls))

if __name__=='__main__':unittest.main()
