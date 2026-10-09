import os, unittest, tempfile, wave
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project,digest
from studio_render import VideoRenderer, effective_motion


class MotionTest(unittest.TestCase):
    def test_assembly_rejects_insufficient_space_without_deleting_inputs(self):
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'completed-clip.mp4';source.write_bytes(b'keep')
            with patch('studio_render.shutil.disk_usage',return_value=SimpleNamespace(free=80*2**20)):
                with self.assertRaisesRegex(RuntimeError,'Completed clips and audio are saved'):
                    VideoRenderer.require_output_space(Path(folder)/'final.mp4',100*2**20,120)
            self.assertEqual(source.read_bytes(),b'keep')
            with patch('studio_render.shutil.disk_usage',return_value=SimpleNamespace(free=300*2**20)):
                VideoRenderer.require_output_space(Path(folder)/'final.mp4',100*2**20,120)

    def test_render_pool_respects_ram_cpu_and_configured_limit(self):
        renderer = VideoRenderer(None,{})
        video = {'width':1280,'height':720}
        with patch('studio_render.os.cpu_count',return_value=12):
            with patch.object(renderer,'available_memory_bytes',return_value=3*2**30):
                self.assertEqual(renderer.render_capacity(video),2)
                renderer.config['renderWorkers'] = 3
                self.assertEqual(renderer.render_capacity(video),3)
            with patch.object(renderer,'available_memory_bytes',return_value=256*2**20):
                self.assertEqual(renderer.render_capacity(video),1)
            with patch.object(renderer,'available_memory_bytes',return_value=None):
                self.assertEqual(renderer.render_capacity(video),1)
        with patch('studio_render.os.cpu_count',return_value=4), patch.object(renderer,'available_memory_bytes',return_value=3*2**30):
            self.assertEqual(renderer.render_capacity(video),1)
        renderer.config['renderWorkers'] = True
        with self.assertRaises(ValueError):
            renderer.render_capacity(video)

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
        self.assertIn("force_original_aspect_ratio=increase,crop=1280:720", zoom_in)
        self.assertNotIn("pad=", zoom_in)
        self.assertIn("1+0.06*on/47", zoom_in)
        self.assertIn("1.06-0.06*on/47", zoom_out)
        self.assertIn('interpolation=cubic', zoom_in)
        self.assertIn('eval=frame', zoom_in)
        self.assertIn("pad=", renderer.motion(self.shot(), video | {"imageFit": "contain"}, 48))

    def test_prose_director_motion_receives_valid_gentle_fallback(self):
        video={'motionMode':'gentle'}
        self.assertEqual(effective_motion(self.shot(motion='A slow dramatic reaction'),video),'slow zoom in')
        self.assertEqual(effective_motion(self.shot('close-up',motion='A held moment of shock'),video),'slow zoom out')
        self.assertEqual(effective_motion(self.shot(motion='Unsupported action',manual={'motion':True}),video),'static')


class IntroTest(unittest.TestCase):
    def narrated_project(self, folder, seconds=24):
        store=ProjectStore(folder);p=store.save(new_project('Teaser'))
        p['settings']['video']['fps']=24  # This fixture asserts quarter-second frame boundaries.
        p['intro'].update(enabled=True,duration=30,voiceText='A story-supported conflict.',audioPath='intro.wav')
        with wave.open(str(store.asset(p['id'],'intro.wav')),'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(24000)
            output.writeframes(b'\0'*int(seconds*24000*2))
        return store,p,VideoRenderer(store,{})

    def test_narration_end_removes_padding_and_fixed_duration_is_optional(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,renderer=self.narrated_project(folder)
            self.assertEqual(renderer.intro_duration(p),24.25)
            with patch.object(renderer,'run') as run:
                renderer.intro(p,lambda *_:None)
            args=run.call_args.args[0]
            self.assertEqual(args[args.index('-t')+1],'24.25')
            self.assertEqual(p['intro']['duration'],30)
            p['intro']['endOnNarration']=False
            self.assertEqual(renderer.intro_duration(p),30)
            p['intro'].pop('audioPath')
            self.assertEqual(renderer.intro_duration(p),30)
        with tempfile.TemporaryDirectory() as folder:
            _,p,renderer=self.narrated_project(folder,3)
            self.assertEqual(renderer.intro_duration(p),10)

    def test_edited_intro_script_cannot_render_old_audio(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,renderer=self.narrated_project(folder)
            p['intro']['audioTextDigest']=digest(p['intro']['voiceText'])
            p['intro']['voiceText']='An edited hook.'
            with self.assertRaisesRegex(ValueError,'narration text changed'), patch.object(renderer,'run') as run:
                renderer.intro(p,lambda *_:None)
            run.assert_not_called()
            self.assertTrue(store.asset(p['id'],'intro.wav').exists())

    def test_montage_trims_last_clip_and_preserves_storyboard(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,renderer=self.narrated_project(folder)
            for name in ('a.png','b.png'):store.asset(p['id'],name).write_bytes(b'fixture')
            p['intro']['shots']=[{'start':0,'end':12,'imagePath':'a.png'}, {'start':12,'end':30,'imagePath':'b.png'}]
            with patch.object(renderer,'run') as run:
                renderer.intro(p,lambda *_:None)
            encoding=[c.args[0] for c in run.call_args_list if '-loop' in c.args[0]]
            self.assertEqual([a[a.index('-t')+1] for a in encoding],['12.0','12.25'])
            self.assertEqual(p['intro']['shots'][1]['end'],30)

    def test_replaced_intro_image_invalidates_cached_render(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,renderer=self.narrated_project(folder)
            store.asset(p['id'],'a.png').write_bytes(b'old image')
            p['intro']['shots']=[{'start':0,'end':30,'imagePath':'a.png'}]
            with patch.object(renderer,'run',side_effect=RenderReuseTest.save_output) as run:
                first=renderer.intro(p,lambda *_:None)
                run.reset_mock()
                self.assertEqual(first,renderer.intro(p,lambda *_:None))
                run.assert_not_called()
                store.asset(p['id'],'a.png').write_bytes(b'new reference-conditioned image')
                second=renderer.intro(p,lambda *_:None)
                self.assertNotEqual(first,second)
                self.assertGreater(run.call_count,0)
                self.assertTrue(first.exists())

    def test_pending_cloud_quality_check_blocks_intro_render(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProjectStore(folder); p = store.save(new_project())
            p['intro']['shots'] = [{'start':0, 'end':15, 'qc': {'status':'PENDING'}}]
            with self.assertRaisesRegex(ValueError, 'pending intro'):
                VideoRenderer(store, {}).intro(p, lambda *args:None)

    def test_thirty_second_intro_moves_and_never_cuts_long_narration(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder)
            p=new_project('Opening')
            p['settings']['video']['fps']=24
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
                self.assertEqual(filters[filters.index('-c:a')+1],'aac')
                self.assertEqual(filters[filters.index('-b:a')+1],'128k')
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


class RenderReuseTest(unittest.TestCase):
    def setup_project(self, folder):
        store = ProjectStore(folder)
        p = store.save(new_project())
        ch = p['chapters'][0]
        for name in ('first.png', 'second.png', 'chapter.wav'):
            (store.folder(p['id']) / name).write_bytes(name.encode())
        ch['audio'] = {'path': 'chapter.wav', 'duration': 2}
        ch['scenes'] = [{'shots': [
            {'id': 'a', 'start': 0, 'end': 1, 'imagePath': 'first.png', 'motion': 'static'},
            {'id': 'b', 'start': 1, 'end': 2, 'imagePath': 'second.png', 'motion': 'static'},
        ]}]
        return store, p, ch

    @staticmethod
    def save_output(args, *_):
        Path(args[-1]).write_bytes(b'encoded fixture')

    def test_review_and_prompt_changes_do_not_encode_unchanged_video(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            renderer = VideoRenderer(store, {})
            with patch.object(renderer, 'run', side_effect=self.save_output) as run:
                ch['render'] = renderer.chapter(p, ch, lambda *_: None)
                self.assertEqual(run.call_count, 3)
                args = run.call_args.args[0]
                self.assertEqual(args[:4],['-f','concat','-safe','0'])
                self.assertEqual(args[args.index('-c:v')+1],'copy')
                self.assertFalse(list((store.folder(p['id'])/ch['id']).glob('visual-*.mp4')))
                run.reset_mock()
                ch['scenes'][0]['shots'][0].update(qc={'status':'REVIEW_REQUIRED', 'issues':['watch']}, prompt='Revised prompt', seed=123)
                ch['audio']['reviewNotes'] = 'Reviewed'
                renderer.chapter(p, ch, lambda *_: None)
                self.assertEqual(run.call_count, 0)

    def test_changing_one_image_only_encodes_that_shot(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            renderer = VideoRenderer(store, {})
            with patch.object(renderer, 'run', side_effect=self.save_output) as run:
                ch['render'] = renderer.chapter(p, ch, lambda *_: None)
                run.reset_mock()
                (store.folder(p['id']) / 'first.png').write_bytes(b'new image content')
                renderer.chapter(p, ch, lambda *_: None)
                encoding = [call for call in run.call_args_list if '-frames:v' in call.args[0]]
                self.assertEqual(len(encoding), 1)
                self.assertIn('first.png', ' '.join(encoding[0].args[0]))

    def test_mid_render_image_edit_cannot_poison_the_old_clip_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,ch=self.setup_project(folder);renderer=VideoRenderer(store,{})
            previous=store.asset(p['id'],'previous.mp4');previous.write_bytes(b'keep previous video')
            def changed(args,*_):
                self.save_output(args)
                if '-frames:v' in args:store.asset(p['id'],'first.png').write_bytes(b'changed during encoding')
            with patch.object(renderer,'run',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'source photo changed'):
                    renderer.chapter(p,ch,lambda *_:None)
            self.assertEqual(previous.read_bytes(),b'keep previous video')
            self.assertFalse(list((store.folder(p['id'])/ch['id']).glob('clip-*.mp4')))

    def test_completed_clip_and_chapter_are_checkpointed_after_atomic_rename(self):
        with tempfile.TemporaryDirectory() as folder:
            store,p,ch=self.setup_project(folder)
            renderer=VideoRenderer(store,{})
            completed=[]
            renderer.on_output=lambda path:completed.append((path, path.read_bytes()))
            with patch.object(renderer,'run',side_effect=self.save_output):
                result=renderer.chapter(p,ch,lambda *_:None)
            clips=[path for path,raw in completed if path.name.startswith('clip-')]
            self.assertEqual(len(clips),2)
            self.assertTrue(all('.partial.' not in path.name and raw for path,raw in completed))
            self.assertIn(store.asset(p['id'],result['path']),[path for path,_ in completed])

    def test_fresh_workspace_reuses_cloud_clips_when_one_image_is_changed(self):
        from studio_storage import R2Archive
        from studio_cloud_test import Objects
        with tempfile.TemporaryDirectory() as original, tempfile.TemporaryDirectory() as scratch:
            store,p,ch=self.setup_project(original)
            ch['scenes'][0]['id']='scene-cache-test'
            for shot in ch['scenes'][0]['shots']:
                shot.update(sceneId='scene-cache-test',chapterId=ch['id'],characters=[],
                    generationSettings={},camera={},status='COMPLETE')
            renderer=VideoRenderer(store,{})
            with patch.object(renderer,'run',side_effect=self.save_output):
                ch['render']=renderer.chapter(p,ch,lambda *_:None)
            p=store.save(p)
            archive=R2Archive(store,Path(original)/'absent');archive.close()
            archive.objects=Objects();archive.enabled=True;archive.sync(p['id'])
            cold=ProjectStore(scratch)
            restored=R2Archive(cold,Path(scratch)/'absent');restored.close()
            restored.objects=archive.objects;restored.enabled=True;cold.archive=restored
            current=cold.load(p['id']);chapter=current['chapters'][0]
            cold.asset(p['id'],'first.png').write_bytes(b'changed image')
            render=VideoRenderer(cold,{})
            with patch.object(render,'run',side_effect=self.save_output) as run:
                render.chapter(current,chapter,lambda *_:None)
            encodes=[call for call in run.call_args_list if '-frames:v' in call.args[0]]
            self.assertEqual(len(encodes),1)
            self.assertIn('first.png',' '.join(encodes[0].args[0]))
            self.assertEqual((store.folder(p['id'])/'first.png').read_bytes(),b'first.png')

    def test_retiming_keeps_unchanged_motion_clip(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            ch['scenes'][0]['shots'][1]['motion'] = 'slow zoom in'
            renderer = VideoRenderer(store, {})
            with patch.object(renderer, 'run', side_effect=self.save_output) as run:
                ch['render'] = renderer.chapter(p, ch, lambda *_:None)
                run.reset_mock()
                ch['scenes'][0]['shots'][0]['end'] = 2
                ch['scenes'][0]['shots'][1].update(start=2,end=3)
                ch['audio']['duration'] = 3
                renderer.chapter(p, ch, lambda *_:None)
                encodes = [c for c in run.call_args_list if '-frames:v' in c.args[0]]
                self.assertEqual(len(encodes),1)
                self.assertIn('first.png', ' '.join(encodes[0].args[0]))

    def test_frame_rounding_still_invalidates_motion(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            renderer = VideoRenderer(store,{})
            shot = ch['scenes'][0]['shots'][0]
            a = renderer.shot_identity(p,shot)
            b = renderer.shot_identity(p,shot | {'start':0.01,'end':1.01})
            c = renderer.shot_identity(p,shot | {'end':1.05})
            self.assertEqual(a,b)
            self.assertNotEqual(a,c)

    def test_aac_audition_reuses_bytes_and_refreshes_changed_source(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            renderer = VideoRenderer(store,{})
            with patch.object(renderer,'run',side_effect=self.save_output) as run:
                first = renderer.audio_preview(p['id'],'chapter.wav',lambda *_:None)
                second = renderer.audio_preview(p['id'],'chapter.wav',lambda *_:None)
                self.assertEqual(first,second)
                self.assertEqual(run.call_count,1)
                args = run.call_args.args[0]
                self.assertEqual(args[args.index('-b:a')+1],'128k')
                self.assertEqual(first['exportBitrate'],128000)
                (store.folder(p['id'])/'chapter.wav').write_bytes(b'new source')
                third = renderer.audio_preview(p['id'],'chapter.wav',lambda *_:None)
                self.assertNotEqual(first['exportPath'],third['exportPath'])
                self.assertEqual(run.call_count,2)

    def test_old_cache_is_adopted_without_reencoding(self):
        with tempfile.TemporaryDirectory() as folder:
            store, p, ch = self.setup_project(folder)
            renderer = VideoRenderer(store, {})
            path = store.folder(p['id']) / 'old-chapter.mp4'
            path.write_bytes(b'existing render')
            ch['render'] = {'path':path.name, 'signature':digest({'rendererVersion':5,
                'shots':ch['scenes'][0]['shots'], 'audio':ch['audio'], 'video':p['settings']['video']})}
            with patch.object(renderer, 'run') as run:
                ch['render'] = renderer.chapter(p, ch, lambda *_: None)
                self.assertEqual(ch['render']['path'], path.name)
                self.assertIn('renderIdentity', ch['render'])
                ch['scenes'][0]['shots'][0]['qc'] = {'status':'PASSED'}
                renderer.chapter(p, ch, lambda *_: None)
                run.assert_not_called()

    def test_legacy_cache_rejects_an_asset_replaced_at_the_same_path(self):
        for name in ('first.png', 'chapter.wav'):
            with self.subTest(asset=name), tempfile.TemporaryDirectory() as folder:
                store, p, ch = self.setup_project(folder)
                old = store.folder(p['id']) / 'old-chapter.mp4'
                old.write_bytes(b'old render')
                ch['render'] = {'path':old.name, 'signature':digest({'rendererVersion':5,
                    'shots':ch['scenes'][0]['shots'], 'audio':ch['audio'], 'video':p['settings']['video']})}
                asset = store.folder(p['id']) / name
                asset.write_bytes(b'replaced contents')
                newer = old.stat().st_mtime_ns + 1000000000
                os.utime(asset, ns=(newer, newer))
                renderer = VideoRenderer(store, {})
                with patch.object(renderer, 'run', side_effect=self.save_output) as run:
                    result = renderer.chapter(p, ch, lambda *_:None)
                self.assertNotEqual(result['path'], old.name)
                self.assertGreater(run.call_count, 0)

    def test_stale_legacy_clip_and_empty_file_cannot_be_adopted(self):
        with tempfile.TemporaryDirectory() as folder:
            old, current, asset = [Path(folder)/name for name in ('old.mp4','new.mp4','image.png')]
            old.write_bytes(b'video'); asset.write_bytes(b'new image')
            newer = old.stat().st_mtime_ns + 1000000000
            os.utime(asset, ns=(newer,newer))
            VideoRenderer.reuse_legacy_clip(old, current, [asset])
            self.assertFalse(current.exists())
            old.write_bytes(b'')
            VideoRenderer.reuse_legacy_clip(old, current)
            self.assertFalse(current.exists())

if __name__ == "__main__":
    unittest.main()
