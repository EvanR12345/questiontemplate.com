import os, unittest, tempfile, wave
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project,digest
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
                self.assertEqual(run.call_count, 4)
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
