import copy,json,tempfile,unittest,wave
from pathlib import Path
from unittest.mock import patch, Mock
from studio_voice_provider import identity,readiness,validate,KaggleVoice,MODELS,KaggleQuotaExhausted,gpu_quota_exhausted
from studio_data import new_project
from studio_service import StudioService
from studio_engagement import audio_signature

class VoiceProviderTest(unittest.TestCase):
    def fallback_service(self):
        service=StudioService.__new__(StudioService)
        service.config={};service.gate=Mock();service.unload_models=Mock();service.before_audio=Mock()
        service.create_audio=Mock(side_effect=lambda *args:{'path':str(args[3]),'duration':1})
        return service

    def test_quota_fallback_is_whole_narration_and_reuses_the_local_lane(self):
        from contextlib import contextmanager
        from types import SimpleNamespace
        service=self.fallback_service();events=[]
        @contextmanager
        def lane(*args,**kwargs):
            events.append('entered')
            yield
            events.append('left')
        service._overlap=SimpleNamespace(root=SimpleNamespace(),local_lane=SimpleNamespace(acquire=lane))
        service.before_audio.side_effect=lambda:events.append('prepare')
        service.create_audio.side_effect=lambda *args:events.append('synthesize') or {'duration':1}
        settings={'voiceEngine':'qwen3-tts-1.7b','voiceLocalFallback':True}
        with patch.object(KaggleVoice,'generate',side_effect=KaggleQuotaExhausted('quota')) as remote:
            first=service.create_selected_audio('The entire chapter.','am_michael',1,Path('audio.wav'),settings=settings)
            service.create_selected_audio('Next complete chapter.','am_michael',1,Path('next.wav'),settings=settings)
        self.assertEqual(remote.call_count,1)
        self.assertEqual(events,['entered','prepare','synthesize','left']*2)
        self.assertEqual(service.create_audio.call_args_list[0].args[0],'The entire chapter.')
        self.assertEqual(first['model'],'kokoro-82m')
        self.assertTrue(first['voiceFallback']['wholeNarration'])
        self.assertTrue(first['voiceFallback']['voiceChanged'])

    def test_auditions_disabled_fallback_and_nonquota_errors_never_substitute(self):
        cases=[(KaggleQuotaExhausted('quota'),True,True),
               (KaggleQuotaExhausted('quota'),False,False),
               (ValueError('Authentication failed'),False,True),
               (ValueError('Submission timed out'),False,True)]
        for error,preview,enabled in cases:
            with self.subTest(error=str(error),preview=preview,enabled=enabled):
                service=self.fallback_service()
                with patch.object(KaggleVoice,'generate',side_effect=error):
                    with self.assertRaises(ValueError):
                        service.create_selected_audio('Story.','am_michael',1,Path('audio.wav'),
                            settings={'voiceEngine':'qwen3-tts-1.7b','voiceLocalFallback':enabled},preview=preview)
                service.create_audio.assert_not_called()
                service.before_audio.assert_not_called()

    def test_quota_classifier_does_not_confuse_stock_concurrency_and_auth(self):
        for text in ('GPU quota exhausted','Not enough GPU quota left','You exceeded your weekly GPU time quota'):
            self.assertTrue(gpu_quota_exhausted(text),text)
        for text in ('GPU quota/concurrent sessions may need attention','GPU not available','401 authentication failed','Maximum concurrent GPU sessions exceeded','request timed out'):
            self.assertFalse(gpu_quota_exhausted(text),text)
        with self.assertRaisesRegex(ValueError,'on or off'):validate({'voiceLocalFallback':'yes'})

    def test_confirmed_quota_rejection_is_journaled_as_unadmitted(self):
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'audio.wav';provider=KaggleVoice({'kaggleUsername':'manhwastory2'})
            with patch.object(KaggleVoice,'health',return_value={'reason':''}),patch.object(provider,'_run',side_effect=KaggleQuotaExhausted('quota')):
                with self.assertRaises(KaggleQuotaExhausted):
                    provider.generate('A quiet gate.',target,{'voiceEngine':'dia-1.6b'},lambda *_:None,preview=True)
            self.assertEqual(json.loads(target.with_name(target.name+'.kaggle.json').read_text())['state'],'REJECTED')
            self.assertFalse(target.exists())

    def test_local_fallback_cannot_train_kaggle_speed_estimates(self):
        from studio_data import ProjectStore
        with tempfile.TemporaryDirectory() as directory:
            service=self.fallback_service();service.store=ProjectStore(directory)
            service.pause_epoch=0;service.paused=False;service.record_timing=Mock()
            p=service.store.save(new_project());ch=p['chapters'][0]
            def save_audio(project,chapter):
                service.store.mutate(project['id'],lambda q:q['chapters'][0].update(
                    audio={'path':'audio.wav','voiceFallback':{'actualModel':'kokoro-82m'}}))
            service._measured_stage(p['id'],'Narration',save_audio,p,ch)
            self.assertTrue(service.record_timing.call_args.args[3]['excludeFromLearning'])

    def test_group_quota_rejection_selects_whole_chapter_fallback(self):
        import sqlite3, threading
        from studio_data import ProjectStore
        from studio_voice_batches import grouped_narration
        with tempfile.TemporaryDirectory() as directory:
            service=self.fallback_service();service.store=ProjectStore(directory)
            service.cv=threading.Condition();service.current='job'
            service.db=sqlite3.connect(':memory:');service.db.row_factory=sqlite3.Row
            self.addCleanup(service.db.close)
            service.db.executescript("CREATE TABLE jobs(id TEXT,kind TEXT);INSERT INTO jobs VALUES('job','produce-story');")
            p=service.store.save(new_project());p['settings'].update(voiceEngine='qwen3-tts-1.7b',voiceLocalFallback=True)
            p['chapters'][0]['sourceText']='A quiet gate.'
            with patch.object(KaggleVoice,'generate_many',side_effect=KaggleQuotaExhausted('quota')) as remote:
                self.assertIsNone(grouped_narration(service,p,p['chapters'][0]))
                self.assertTrue(service.voice_quota_fallback_active(p['settings']))
                self.assertIsNone(grouped_narration(service,p,p['chapters'][0]))
                self.assertEqual(remote.call_count,1)

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

    def test_audition_format_change_reuses_synthesis_and_keeps_chapters(self):
        from unittest.mock import Mock
        from studio_data import ProjectStore
        from studio_render import VideoRenderer
        with tempfile.TemporaryDirectory() as directory:
            service=StudioService.__new__(StudioService)
            service.store=ProjectStore(directory)
            service.gate=lambda *_:None
            service.renderer=VideoRenderer(service.store,{'ffmpeg':'configured'})
            p=service.store.save(new_project())
            chapters=copy.deepcopy(p['chapters'])
            def synthesize(text,voice,speed,target,*args,**kwargs):
                with wave.open(str(target),'wb') as wav:
                    wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(b'\0\0'*2400)
                return {'model':'qwen3-tts-1.7b','duration':.1}
            service.create_selected_audio=Mock(side_effect=synthesize)
            def encode(args,*_):Path(args[-1]).write_bytes(b'encoded')
            options={'text':'The gate opened.','voiceEngine':'qwen3-tts-1.7b','audioFormat':'opus','audioBitrate':128}
            with patch.object(service.renderer,'run',side_effect=encode):
                service.voice_preview(p,options)
                saved=service.store.load(p['id']);first=saved['voicePreviews'][-1]
                service.voice_preview(saved,options|{'audioBitrate':64})
            final=service.store.load(p['id'])
            self.assertEqual(service.create_selected_audio.call_count,1)
            self.assertEqual(final['chapters'],chapters)
            self.assertEqual(first['exportCodec'],'Opus')
            self.assertEqual(final['voicePreviews'][-1]['exportCodec'],'Opus')
            self.assertEqual(first['path'],final['voicePreviews'][-1]['path'])

    def test_invalid_download_or_missing_encoder_cannot_start_kaggle_work(self):
        from unittest.mock import Mock
        service=StudioService.__new__(StudioService)
        service.create_selected_audio=Mock()
        for options in ({'audioFormat':'wav'},{'audioFormat':'mp3'},{'audioFormat':'exe'},{'audioFormat':'opus','audioBitrate':True},{'audioFormat':'opus'}):
            with self.assertRaises(ValueError):service.voice_preview(new_project(),{'text':'A gate.'}|options)
        service.create_selected_audio.assert_not_called()

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

    def test_group_recovery_preserves_original_notebook_at_each_publication_boundary(self):
        import ast
        for boundary in ('before-admission','submission-uncertain','after-notebook',
                         'before-first-chapter','before-first-receipt','after-first-chapter','legacy-split'):
            with self.subTest(boundary=boundary),tempfile.TemporaryDirectory() as directory:
                root=Path(directory)
                cfg={'kaggleUsername':'testowner','kaggleVerifiedModels':['qwen3-tts-1.7b']}
                settings={'voiceEngine':'qwen3-tts-1.7b','qwenSpeaker':'Ryan'}
                provider=KaggleVoice(cfg)
                first={'id':'one','signature':'v1','text':'The gate opens.','target':str(root/'one'/'v1.opus')}
                second={'id':'two','signature':'v1','text':'We enter.','target':str(root/'two'/'v1.opus')}
                items=[first,second]
                reference={'audio':'YXVkaW8=','text':'Reference.'}
                admissions=[]
                def run(args,**kw):
                    action=args[1]
                    if action=='status':return 'complete'
                    folder=Path(args[args.index('-p')+1])
                    if action=='pull':
                        (folder/'kernel-metadata.json').write_text(json.dumps({'is_private':True}))
                    else:
                        with (folder/'voice_job.py').open(encoding='utf-8') as code:
                            req=ast.literal_eval(code.readline().removeprefix('REQUEST = '))
                        if action=='push':
                            admissions.append([i['chapterId'] for i in req['parts']])
                            if boundary=='submission-uncertain':raise ValueError('Timed out')
                        if action=='output':
                            signature=next(j['signature'] for j in (json.loads(f.read_text()) for f in
                                folder.parent.glob('*.opus.kaggle.json')) if folder.name.endswith(j['signature'][:16]))
                            output={'signature':signature,'model':req['model'],'duration':len(req['parts']),
                                'setupAndModelLoadSeconds':77,'speechGenerationSeconds':20,'sentences':[],'chapterAudio':[]}
                            for i,part in enumerate(req['parts']):
                                filename=f'chapter-{i:03d}.opus'
                                (folder/filename).write_text(part['text']+req['qwenSpeaker'],encoding='utf-8')
                                output['sentences'].append(dict(index=i,text=part['text'],start=i,end=i+1))
                                output['chapterAudio'].append(dict(id=part['chapterId'],first=i,last=i+1,path=filename))
                            (folder/'result.json').write_text(json.dumps(output))
                    return 'Kernel version successfully pushed.'
                def gate(message):
                    if message.startswith('Saving completed chapter') and (boundary=='before-first-chapter' or
                        boundary in ('after-first-chapter','legacy-split') and Path(first['target']).with_suffix('.voice-ready.json').exists()):
                        raise InterruptedError('simulated stop')
                original_write=provider._write
                def write(path,value):
                    if (path.name.endswith('.publication.json') and
                        (boundary=='after-notebook' and 'outputs' in value or
                         boundary=='before-admission' and 'outputs' not in value) or
                        boundary=='before-first-receipt' and path.name.endswith('.voice-ready.json')):
                        raise InterruptedError('simulated stop')
                    original_write(path,value)
                with patch.object(KaggleVoice,'health',return_value={'reason':''}),patch.object(provider,'_run',side_effect=run), \
                        patch('studio_audio.probe',return_value={'codec':'opus','channels':1,'duration':1}):
                    with patch.object(provider,'_write',side_effect=write),self.assertRaises((InterruptedError,ValueError)):
                        provider.generate_many(items,settings,gate,reference=reference)
                    if boundary=='submission-uncertain':
                        with self.assertRaisesRegex(ValueError,'reconciliation'):
                            provider.generate_many([second],settings,lambda m:None,reference=reference)
                        self.assertEqual(admissions,[['one','two']])
                        continue
                    if boundary=='legacy-split':
                        # Emulate a checkout made before grouped publication records existed.
                        next((root/'.voice-groups').glob('*.publication.json')).unlink()
                        legacy=provider.generate_many(items,settings,lambda m:None,reference=reference)
                        self.assertEqual(admissions,[['one','two']])
                        self.assertEqual(Path(legacy[1]['path']).read_text(),'We enter.Ryan')
                        self.assertEqual(legacy[1]['voiceBatch']['chapters'],2)
                        continue
                    changed=dict(first,signature='v2',text='The gate is closed.',target=str(root/'one'/'v2.opus'))
                    results=provider.generate_many([changed,second],settings,lambda m:None,reference=reference)
                self.assertEqual(admissions,[['one','two']] if boundary=='before-admission' else [['one','two'],['one']])
                self.assertEqual(Path(results[1]['path']).read_text(),'We enter.Ryan')
                self.assertEqual(Path(results[0]['path']).read_text(),'The gate is closed.Ryan')
                self.assertEqual(results[1]['voiceBatch']['chapters'],2)
                self.assertEqual(results[1]['voiceBatch']['groupSetupSeconds'],77)
                self.assertEqual(results[1]['sentences'][0]['start'],0)

    def test_interrupted_group_checks_original_bytes_and_voice_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            provider=KaggleVoice({})
            p=new_project()
            p['settings'].update(voiceEngine='qwen3-tts-1.7b',qwenSpeaker='Ryan')
            ch=p['chapters'][0]
            ch['sourceText']='First chapter.'
            def item():
                signature=StudioService.narration_signature(p,ch)
                return {'id':ch['id'],'signature':signature,'text':ch['sourceText'],
                        'target':str(root/'chapter'/(signature[:12]+'.opus'))}
            original=item()
            calls=[]
            def generate(text,target,settings,gate,parts_override,**kw):
                calls.append(settings['qwenSpeaker'])
                folder=target.parent/target.stem
                folder.mkdir()
                asset=folder/'chapter-000.opus'
                asset.write_bytes(settings['qwenSpeaker'].encode())
                return {'duration':1,'sentences':[{'index':0,'text':text,'start':0,'end':1}],
                        'chapterAudio':[{'path':str(asset)}]}
            with patch.object(provider,'_generate',side_effect=generate):
                with self.assertRaises(InterruptedError):
                    provider.generate_many([original],p['settings'],Mock(side_effect=InterruptedError))
                staged=next((root/'.voice-groups').glob('*/chapter-000.opus'))
                staged.write_bytes(b'truncated')
                with self.assertRaisesRegex(ValueError,'checksum'):
                    provider.generate_many([original],p['settings'],lambda m:None)
                self.assertFalse(Path(original['target']).exists())
                p['settings']['qwenSpeaker']='Aiden'
                changed=item()
                self.assertNotEqual(original['signature'],changed['signature'])
                result=provider.generate_many([changed],p['settings'],lambda m:None)
            self.assertEqual(calls,['Ryan','Aiden'])
            self.assertEqual(Path(result[0]['path']).read_bytes(),b'Aiden')
            self.assertEqual(staged.read_bytes(),b'truncated')

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
