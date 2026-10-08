"""Exercise unattended orchestration with deterministic stage adapters."""

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from studio_data import (
    new_project,
    new_chapter,
    character,
    uid,
    digest,
    get_chapter,
    get_shot,
    state_before,
)
from studio_service import StudioService, JobCancelled


class Provider:
    id = "existing"

    def unload(self):
        pass

    def getCapabilities(self):
        return {"maxReferenceImages": 1}

    def validateSettings(self, settings):
        return settings | {"seed": settings.get("seed") or 123}


class Renderer:
    def __init__(self, store):
        self.store = store
        self.full_calls = 0

    def chapter(self, p, ch, gate):
        name = ch["id"] + ".mp4"
        self.store.asset(p["id"], name).write_bytes(b"chapter video")
        return {"path": name, "duration": 4, "signature": ch["id"]}

    def full(self, p, gate):
        assert all(c.get("render", {}).get("path") for c in p["chapters"])
        self.full_calls += 1
        self.store.asset(p["id"], "story.mp4").write_bytes(b"full video")
        return {"path": "story.mp4", "duration": 8, "signature": "full"}


class Harness(StudioService):
    def worker(self):
        pass

    def unload_models(self):
        pass

    def narration(self, p, ch):
        signature = digest(ch["sourceText"])
        if ch.get("audio", {}).get("signature") == signature:
            return
        self.calls.append(("audio", ch["number"]))
        self.store.mutate(
            p["id"],
            lambda q: get_chapter(q, ch["id"]).update(
                audio={"signature": signature, "path": "audio.wav", "duration": 4},
                cleanNarrationText=ch["sourceText"],
            ),
        )

    def analyze(self, p, chid, options):
        assert options["managedPipeline"]
        ch = get_chapter(p, chid)
        self.calls.append(("analysis", ch["number"]))
        if self.proposal:
            self.store.mutate(
                p["id"],
                lambda q: get_chapter(q, chid).update(proposedPlan={"scenes": []}),
            )
            return
        main = (
            copy.deepcopy(p["characters"][0])
            if p["characters"]
            else character("Mira", kind="main")
        )
        teacher = character("Teacher", kind="supporting")
        scene = {
            "id": uid("scene-"),
            "chapterId": chid,
            "characters": [{"id": main["id"], "type": "main"}],
            "appearanceChanges": [],
            "shots": [],
        }
        shot = {
            "id": uid("shot-"),
            "chapterId": chid,
            "sceneId": scene["id"],
            "start": 0,
            "end": 4,
            "characters": [{"id": main["id"], "type": "main", "appearanceState": {}}],
            "generationSettings": p["settings"]["image"] | {"seed": 123},
            "camera": {},
            "imageProvider": "existing",
            "imageModel": "sd15",
            "workflow": "text-to-image",
            "prompt": "Mira carries a key",
            "negativePrompt": "",
            "status": "READY_FOR_IMAGES",
            "imagePath": "",
            "manual": {},
            "intentionalAppearanceChanges": [],
        }
        scene["shots"] = [shot]
        state, _ = state_before(p, chid)
        self.store.mutate(
            p["id"],
            lambda q: get_chapter(q, chid).update(
                people=[main, teacher],
                scenes=[scene],
                inputState=state,
                handoff={
                    "sourceSignature": digest(ch["sourceText"]),
                    "state": state,
                    "memory": {},
                },
            ),
        )

    def character_reference(self, p, options):
        self.calls.append(("reference", options["characterId"]))
        path = "face.png"
        self.store.asset(p["id"], path).write_bytes(b"reference")
        self.store.mutate(
            p["id"],
            lambda q: next(
                c for c in q["characters"] if c["id"] == options["characterId"]
            )["references"].append({"path": path, "kind": "face"}),
        )

    def generate(self, p, chid, sid, seed, options):
        chapter = get_chapter(p, chid)
        self.calls.append(("image", chapter["number"], seed))
        if self.fail_chapter == chapter["number"]:
            raise ValueError("image backend unavailable")
        path = sid + ".png"
        self.store.asset(p["id"], path).write_bytes(b"image")
        self.store.mutate(
            p["id"],
            lambda q: get_shot(q, chid, sid).update(imagePath=path, status="COMPLETE"),
        )
        if self.cancel_after_image:
            self.cancel_after_image = False
            raise JobCancelled()


class FullVideoTest(unittest.TestCase):
    def test_full_production_retains_separate_patreon_and_youtube_exports(self):
        old={'path':'previous-youtube.mp4','exportDestination':'youtube'}
        self.service.store.mutate(self.project['id'],lambda p:p.update(exports={'youtube':old}))
        render=self.service.renderer.full
        self.service.renderer.full=lambda p,gate:render(p,gate)|{'exportDestination':'patreon'}
        self.service.produce_story(self.project['id'],{})
        saved=self.service.store.load(self.project['id'])
        self.assertEqual(saved['exports']['youtube'],old)
        self.assertEqual(saved['exports']['patreon'],saved['render'])
        self.assertFalse(self.service.snapshot()['storage']['enabled'])

    def cloud_mode(self):
        cloud = Provider()
        cloud.id = 'comfyui'
        self.service.providers['comfyui'] = cloud
        self.service.store.mutate(self.project['id'], lambda q:q['settings']['image'].update(provider='comfyui'))

    def test_cloud_plans_all_chapters_and_images_before_rendering(self):
        self.cloud_mode()
        original = self.service.renderer.chapter
        def render(project, chapter, gate):
            self.service.calls.append(('render', chapter['number']))
            return original(project, chapter, gate)
        self.service.renderer.chapter = render
        self.service.produce_story(self.project['id'], {})
        kinds = [call[0] for call in self.service.calls]
        self.assertLess(max(i for i,k in enumerate(kinds) if k == 'analysis'), kinds.index('reference'))
        self.assertLess(max(i for i,k in enumerate(kinds) if k == 'image'), kinds.index('render'))
        self.assertEqual(kinds.count('reference'), 1)
        self.assertEqual(kinds.count('analysis'), 2)
        self.assertEqual(len(self.service.store.load(self.project['id'])['characters']), 1)
        calls = self.service.calls.copy()
        self.service.produce_story(self.project['id'], {})
        self.assertEqual([c for c in self.service.calls[len(calls):] if c[0] != 'render'], [])

    def test_cloud_bad_prompt_stops_before_any_image_or_reference_charge(self):
        self.cloud_mode()
        original = self.service.analyze
        def bad_plan(project, chid, options):
            original(project, chid, options)
            if get_chapter(project, chid)['number'] == 2:
                self.service.store.mutate(project['id'], lambda q:get_chapter(q,chid)['scenes'][0]['shots'][0].update(prompt=''))
        self.service.analyze = bad_plan
        with self.assertRaisesRegex(ValueError, 'preflight'):
            self.service.produce_story(self.project['id'], {})
        self.assertFalse(any(c[0] in ('reference', 'image') for c in self.service.calls))
        self.assertEqual(self.service.renderer.full_calls, 0)

    def test_cloud_cancel_retains_images_and_resume_does_not_repeat_generation(self):
        self.cloud_mode()
        self.service.cancel_after_image = True
        with self.assertRaises(JobCancelled):
            self.service.produce_story(self.project['id'], {})
        self.service.produce_story(self.project['id'], {})
        self.assertEqual(sum(c[:2] == ('image', 1) for c in self.service.calls), 1)
        self.assertEqual(self.service.store.load(self.project['id'])['production']['status'], 'COMPLETE')

    def test_cloud_only_error_cannot_fall_back_to_local_images(self):
        from unittest.mock import Mock
        self.service.prepare_story(self.project['id'], {})
        p = self.service.store.load(self.project['id'])
        p['settings']['cloudImagesOnly'] = True
        p['settings']['image'].update(provider='comfyui', fallbackEnabled=True,
                                      fallback={'provider':'existing','model':'sd15'})
        ch = p['chapters'][0]
        shot = ch['scenes'][0]['shots'][0]
        shot['imageProvider'] = 'comfyui'
        p = self.service.store.save(p)
        cloud = Mock()
        cloud.id = 'comfyui'
        cloud.getCapabilities.return_value = {'maxReferenceImages':0}
        cloud.getModelCapabilities.return_value = {'maxReferenceImages':0}
        cloud.generateImage.side_effect = RuntimeError('backend unavailable')
        self.service.providers['comfyui'] = cloud
        self.service.providers['native-flux'] = Mock()
        self.service.providers['existing'].generateImage = Mock()
        with self.assertRaisesRegex(ValueError,'local fallback is disabled'):
            StudioService.generate(self.service,p,ch['id'],shot['id'],123,{})
        self.service.providers['existing'].generateImage.assert_not_called()

    def test_changed_narration_invalidates_prepared_scene_timing(self):
        self.service.prepare_story(self.project['id'], {})
        def changed_audio(project, chapter):
            self.service.store.mutate(project['id'], lambda q:get_chapter(q,chapter['id'])['audio'].update(signature='changed-audio'))
        self.service.narration = changed_audio
        calls = len(self.service.calls)
        self.service.prepare_story(self.project['id'], {})
        self.assertEqual([c for c in self.service.calls[calls:] if c[0] == 'analysis'], [('analysis', 1), ('analysis', 2)])

    def test_replacing_character_reference_at_same_path_changes_visual_version(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        ref='versioned-portrait.png'
        p['chapters'][0]['people'][0]['references']=[{'path':ref,'kind':'face'}]
        self.service.store.asset(p['id'],ref).write_bytes(b'old user portrait')
        p=self.service.store.save(p)
        before=self.service.visual_spec_signature(p,shot)
        self.service.store.asset(p['id'],ref).write_bytes(b'new user portrait')
        self.assertNotEqual(before,self.service.visual_spec_signature(p,shot))

    def test_intro_edit_during_render_does_not_replace_selected_intro(self):
        self.service.store.mutate(self.project['id'],lambda q:q['intro'].update(videoPath='old-intro.mp4'))
        p=self.service.store.load(self.project['id'])
        signature=self.service.intro_submission_signature(p)
        self.service.store.mutate(p['id'],lambda q:q['intro'].update(title='Manual title'))
        self.assertFalse(self.service.publish_intro_render(p['id'],'finished-earlier.mp4',signature))
        intro=self.service.store.load(p['id'])['intro']
        self.assertEqual(intro['title'],'Manual title')
        self.assertEqual(intro['videoPath'],'old-intro.mp4')
        self.assertFalse(intro['renderHistory'][-1]['selected'])
        self.assertTrue(intro['renderStale'])

    def test_chapter_edit_during_render_preserves_manual_data_and_old_selection(self):
        self.service.prepare_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0]
        self.service.store.mutate(p['id'],lambda q:get_chapter(q,ch['id']).update(render={'path':'previous.mp4'}))
        original=self.service.renderer.chapter
        def edited_render(project,chapter,gate):
            result=original(project,chapter,gate)
            self.service.store.mutate(project['id'],lambda q:get_chapter(q,chapter['id']).update(sourceText='New manual narration'))
            return result
        self.service.renderer.chapter=edited_render
        with self.assertRaisesRegex(ValueError,'Chapter changed during rendering'):
            self.service.produce_story(p['id'],{})
        latest=get_chapter(self.service.store.load(p['id']),ch['id'])
        self.assertEqual(latest['sourceText'],'New manual narration')
        self.assertEqual(latest['render']['path'],'previous.mp4')
        self.assertFalse(latest['renderHistory'][-1]['selected'])
        self.assertTrue(latest['renderStale'])
        self.assertEqual(self.service.renderer.full_calls,0)

    def test_story_edit_during_assembly_retains_finished_earlier_video(self):
        self.service.store.mutate(self.project['id'],lambda q:q.update(render={'path':'previous-story.mp4'}))
        original=self.service.renderer.full
        def edited_assembly(project,gate):
            result=original(project,gate)
            self.service.store.mutate(project['id'],lambda q:q['intro'].update(enabled=True,title='New intro'))
            return result
        self.service.renderer.full=edited_assembly
        with self.assertRaisesRegex(ValueError,'Story changed during assembly'):
            self.service.produce_story(self.project['id'],{})
        latest=self.service.store.load(self.project['id'])
        self.assertEqual(latest['render']['path'],'previous-story.mp4')
        self.assertTrue(latest['intro']['enabled'])
        self.assertFalse(latest['renderHistory'][-1]['selected'])
        self.assertNotEqual(latest['production']['status'],'COMPLETE')

    def test_render_publication_signature_ignores_progress_but_detects_asset_replacement(self):
        self.service.produce_story(self.project['id'],{})
        p=self.service.store.load(self.project['id'])
        signature=self.service.render_submission_signature(p)
        p['production'].update(stage='Progress only',completedImages=99)
        p['timings']=[{'seconds':99}]
        shot=p['chapters'][0]['scenes'][0]['shots'][0]
        shot.update(status='QC',qc={'pass':True},qcHistory=[{'selected':False}])
        self.assertEqual(signature,self.service.render_submission_signature(p))
        self.service.store.asset(p['id'],shot['imagePath']).write_bytes(b'replaced bytes at same path')
        self.assertNotEqual(signature,self.service.render_submission_signature(p))

    def test_image_finishing_after_manual_edit_is_saved_without_replacing_it(self):
        from PIL import Image
        from unittest.mock import Mock
        chapter=self.project['chapters'][0]
        self.service.analyze(self.project,chapter['id'],{'managedPipeline':True})
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        self.service.providers['native-flux']=Mock()
        manual=self.service.store.asset(p['id'],'manual.png');Image.new('RGB',(8,8),'blue').save(manual)
        def generated(request,checkpoint):
            self.service.store.mutate(p['id'],lambda q:get_shot(q,chapter['id'],shot['id']).update(
                imagePath='manual.png',prompt='User edited this scene',origin='MANUAL'))
            return {'pil':Image.new('RGB',(8,8),'red'),'model':'sd15','settings':request['settings']}
        self.service.providers['existing'].generateImage=generated
        result=StudioService.generate(self.service,p,chapter['id'],shot['id'],123,{})
        latest=get_shot(self.service.store.load(p['id']),chapter['id'],shot['id'])
        self.assertEqual(result['status'],'SUPERSEDED')
        self.assertEqual(latest['imagePath'],'manual.png')
        self.assertEqual(latest['prompt'],'User edited this scene')
        self.assertFalse(latest['history'][-1]['selected'])
        self.assertTrue(self.service.store.asset(p['id'],latest['history'][-1]['imagePath']).is_file())

    def test_review_for_replaced_image_is_retained_without_certifying_new_image(self):
        self.service.produce_story(self.project['id'],{})
        p=self.service.store.load(self.project['id']);ch=p['chapters'][0];shot=ch['scenes'][0]['shots'][0]
        self.service.store.asset(p['id'],'new-manual.png').write_bytes(b'new image')
        def review(*args):
            self.service.store.mutate(p['id'],lambda q:get_shot(q,ch['id'],shot['id']).update(
                imagePath='new-manual.png',camera={'shot':'close-up'},origin='MANUAL'))
            return {'pass':True,'issues':[],'action':'accept','repairPrompt':''}
        self.service.visual_check=review
        result=self.service.inspect_shot(p,ch['id'],shot['id'])
        latest=get_shot(self.service.store.load(p['id']),ch['id'],shot['id'])
        self.assertFalse(result['selected'])
        self.assertEqual(latest['imagePath'],'new-manual.png')
        self.assertEqual(latest['camera']['shot'],'close-up')
        self.assertEqual(latest['qc']['status'],'PENDING')
        self.assertFalse(latest['qcHistory'][-1]['selected'])
        self.assertEqual(latest['qcHistory'][-1]['qc']['checkedImagePath'],shot['imagePath'])

    def test_stale_failed_review_does_not_pay_for_a_repair_of_an_old_prompt(self):
        from PIL import Image
        from unittest.mock import Mock
        chapter=self.project['chapters'][0]
        self.service.analyze(self.project,chapter['id'],{'managedPipeline':True})
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        shot['intentionalAppearanceChanges']=[]
        p=self.service.store.save(p)
        self.service.providers['native-flux']=Mock()
        p['settings'].update(visionQC=True,automaticRepair=True,maxImageRetries=3)
        generate=Mock(return_value={'pil':Image.new('RGB',(8,8),'red'),'model':'sd15'})
        self.service.providers['existing'].generateImage=generate
        def failed_review(*args):
            self.service.store.mutate(p['id'],lambda q:get_shot(q,chapter['id'],shot['id']).update(prompt='Manual new prompt'))
            return {'pass':False,'issues':['old result'],'action':'regenerate','repairPrompt':'old repair'}
        self.service.visual_check=failed_review
        result=StudioService.generate(self.service,p,chapter['id'],shot['id'],123,{})
        self.assertEqual(result['status'],'SUPERSEDED')
        self.assertEqual(generate.call_count,1)
        latest=get_shot(self.service.store.load(p['id']),chapter['id'],shot['id'])
        self.assertEqual(latest['prompt'],'Manual new prompt')
        self.assertEqual(latest['qc']['status'],'PENDING')

    def test_uncertain_remote_result_does_not_retry_or_use_fallback(self):
        from image_provider import RemoteGenerationUncertain
        from unittest.mock import Mock
        chapter=self.project['chapters'][0]
        self.service.analyze(self.project,chapter['id'],{'managedPipeline':True})
        p=self.service.store.load(self.project['id']);shot=p['chapters'][0]['scenes'][0]['shots'][0]
        p['settings']['maxImageRetries']=3
        p['settings']['image'].update(fallbackEnabled=True,fallback={'provider':'native-flux'})
        self.service.providers['native-flux']=Mock()
        generate=Mock(side_effect=RemoteGenerationUncertain('paid-prompt'))
        self.service.providers['existing'].generateImage=generate
        with self.assertRaises(RemoteGenerationUncertain):
            StudioService.generate(self.service,p,chapter['id'],shot['id'],123,{})
        self.assertEqual(generate.call_count,1)
        self.service.providers['native-flux'].generateImage.assert_not_called()

    def test_project_level_jobs_are_deduplicated_with_null_chapter(self):
        for _ in range(2):
            self.service.enqueue(self.project['id'], None, 'intro-audio')
        self.assertEqual(self.service.snapshot()['counts']['QUEUED'], 1)

    def test_one_projects_status_sync_does_not_rewrite_older_projects(self):
        other = self.service.store.save(new_project('Old project'))
        self.service.enqueue(other['id'], other['chapters'][0]['id'], 'narration')
        self.service.enqueue(self.project['id'], self.project['chapters'][0]['id'], 'narration')
        original = self.service.store.mutate
        touched = []
        def track(pid, callback, **options):
            touched.append(pid)
            return original(pid, callback, **options)
        self.service.store.mutate = track
        self.service.sync_queue_states(self.project['id'])
        self.assertEqual(touched, [self.project['id']])

    def test_saved_image_review_queues_without_image_backend_online(self):
        self.service.prepare_story(self.project['id'], {})
        saved = self.service.store.load(self.project['id'])
        c = saved['chapters'][0]
        shot = c['scenes'][0]['shots'][0]
        provider = self.service.provider('existing')
        def unavailable(settings):
            raise RuntimeError('image worker stopped')
        provider.validateSettings = unavailable
        queue = self.service.enqueue(saved['id'], c['id'], 'qc', [shot['id']])
        self.assertEqual(queue['counts']['QUEUED'], 1)

    def test_preparation_directs_both_chapters_without_images_or_rendering(self):
        self.service.prepare_story(self.project['id'], {})
        saved = self.service.store.load(self.project['id'])
        self.assertEqual(self.service.calls, [('audio', 1), ('analysis', 1), ('audio', 2), ('analysis', 2)])
        self.assertEqual(saved['production']['status'], 'READY_FOR_IMAGES')
        self.assertFalse(saved.get('render'))
        self.assertTrue(all(not shot.get('imagePath') for chapter in saved['chapters']
                            for scene in chapter['scenes'] for shot in scene['shots']))

    def test_retry_missing_skips_saved_assets_older_attempts_and_other_projects(self):
        self.service.prepare_story(self.project['id'], {})
        p = self.service.store.load(self.project['id'])
        first, second = [c['scenes'][0]['shots'][0] for c in p['chapters']]
        first['imagePath'] = 'saved.png'
        self.service.store.asset(p['id'], 'saved.png').write_bytes(b'saved')
        self.service.store.save(p)
        rows = [
            ('saved-failed', p['id'], p['chapters'][0]['id'], first['id'], 'FAILED', 1, 10),
            ('missing-old', p['id'], p['chapters'][1]['id'], second['id'], 'FAILED', 2, 20),
            ('missing-latest', p['id'], p['chapters'][1]['id'], second['id'], 'CANCELLED', 3, 30),
            ('other-project', 'other', 'other-chapter', 'other-shot', 'FAILED', 4, 40),
        ]
        for identity, project, chapter, shot, status, created, seed in rows:
            self.service.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (identity, project, chapter, shot, 'image', '{}', status, '', 0, created, None, 0, 1, seed))
        self.service.db.commit()
        result = self.service.control('retry-missing', project=p['id'])
        queued = [j for j in result['jobs'] if j['status'] == 'QUEUED']
        self.assertEqual([j['id'] for j in queued], ['missing-latest'])
        self.assertEqual(queued[0]['seed'], 30)
        self.assertTrue(self.service.store.asset(p['id'], 'saved.png').exists())

    def test_repeated_stream_progress_does_not_commit_for_every_token(self):
        self.service.current = 'job-stream'
        changes = []
        self.service.db.set_trace_callback(changes.append)
        for _ in range(100):
            self.service.gate('Luna reviewing image')
        self.service.gate('Luna review finished')
        updates = [sql for sql in changes if sql.startswith('UPDATE jobs SET message=')]
        self.assertEqual(len(updates), 2)
        self.service.current = 'job-next'
        self.service.gate('Luna reviewing image')
        self.assertEqual(sum(sql.startswith('UPDATE jobs SET message=') for sql in changes), 3)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.service = Harness(
            self.temp.name,
            type("Audio", (), {"gpu": "test"})(),
            lambda: None,
            threading.Lock(),
            lambda: None,
            lambda: None,
            type("Legacy", (), {"control": lambda *args: None})(),
        )
        self.service.providers = {"existing": Provider()}
        self.service.renderer = Renderer(self.service.store)
        self.service.calls = []
        self.service.fail_chapter = None
        self.service.cancel_after_image = False
        self.service.proposal = False
        self.project = new_project()
        self.project["chapters"].append(new_chapter(2))
        for chapter in self.project["chapters"]:
            chapter["sourceText"] = "Mira carries a brass key."
        self.project = self.service.store.save(self.project)

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def test_two_chapters_finish_as_one_video_and_reuse_completed_stages(self):
        self.service.produce_story(self.project["id"], {})
        p = self.service.store.load(self.project["id"])
        self.assertEqual(p["production"]["status"], "COMPLETE")
        self.assertEqual(p["render"]["path"], "story.mp4")
        self.assertEqual(len(p["characters"]), 1)
        self.assertEqual(p["characters"][0]["name"], "Mira")
        self.assertEqual(sum(c[0] == "reference" for c in self.service.calls), 1)
        calls = self.service.calls.copy()
        self.service.produce_story(self.project["id"], {})
        self.assertEqual(self.service.calls, calls)

    def test_queue_eta_does_not_reuse_laptop_timing_for_cloud_workflow(self):
        base = {"imageProvider": "existing", "imageModel": "sd15", "workflow": "text-to-image", "generationSettings": {"width": 512, "height": 512, "steps": 20}}
        cloud = base | {"imageProvider": "comfyui", "imageModel": "qwen-studio-auto", "workflow": "qwen-studio-auto"}
        for identity, snapshot, state, seconds in [("old", base, "COMPLETE", 90), ("new", cloud, "QUEUED", 0)]:
            self.service.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (identity, self.project["id"], None, identity, "image", json.dumps({"shotSnapshot": snapshot}), state, "", 0, 0, 0, seconds, 0, 123))
        self.service.db.commit()
        self.assertIsNone(self.service.snapshot()["etaSeconds"])
        self.service.db.execute("INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("cloud-complete", self.project["id"], None, "done", "image", json.dumps({"shotSnapshot": cloud}), "COMPLETE", "", 0, 0, 0, 12, 0, 123))
        self.service.db.commit()
        self.assertEqual(self.service.snapshot()["etaSeconds"], 12)

    def test_visual_review_is_preserved_without_falsely_passing_or_losing_image(self):
        self.service.produce_story(self.project["id"], {})
        p = self.service.store.load(self.project["id"])
        chapter = p["chapters"][0]
        shot = chapter["scenes"][0]["shots"][0]
        self.service.visual_check = lambda *args: {"pass": False, "issues": ["Small eyebrow scar is unclear at wide framing"], "action": "review", "repairPrompt": ""}
        self.service.inspect_shot(p, chapter["id"], shot["id"])
        saved = get_shot(self.service.store.load(p["id"]), chapter["id"], shot["id"])
        self.assertEqual(saved["qc"]["status"], "REVIEW_REQUIRED")
        self.assertFalse(saved["qc"]["pass"])
        self.assertEqual(saved["status"], "COMPLETE")
        self.assertEqual(saved["imagePath"], shot["imagePath"])
        self.service.visual_check = lambda *args: {"pass": False, "issues": ["Wrong character identity"], "action": "regenerate", "repairPrompt": "Restore identity"}
        self.service.inspect_shot(p, chapter["id"], shot["id"])
        saved = get_shot(self.service.store.load(p["id"]), chapter["id"], shot["id"])
        self.assertEqual(saved["status"], "FAILED")
        self.assertEqual(saved["imagePath"], shot["imagePath"])

    def test_retry_preserves_completed_chapter_and_failed_image_seed(self):
        self.service.fail_chapter = 2
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.service.produce_story(self.project["id"], {})
        p = self.service.store.load(self.project["id"])
        first = p["chapters"][0]["scenes"][0]["shots"][0]["imagePath"]
        self.assertTrue(self.service.store.asset(p["id"], first).exists())
        self.assertFalse(p.get("render"))
        self.service.fail_chapter = None
        self.service.produce_story(p["id"], {})
        self.assertEqual(sum(c[:2] == ("image", 1) for c in self.service.calls), 1)
        self.assertEqual(
            [c[2] for c in self.service.calls if c[:2] == ("image", 2)], [123, 123]
        )

    def test_cancel_after_saved_image_can_restart_without_regenerating_it(self):
        self.service.cancel_after_image = True
        with self.assertRaises(JobCancelled):
            self.service.produce_story(self.project["id"], {})
        self.service.produce_story(self.project["id"], {})
        self.assertEqual(sum(c[:2] == ("image", 1) for c in self.service.calls), 1)

    def test_manual_proposed_plan_stops_automatic_run_without_render(self):
        self.service.proposal = True
        with self.assertRaisesRegex(ValueError, "manual edits were preserved"):
            self.service.produce_story(self.project["id"], {})
        self.assertEqual(self.service.renderer.full_calls, 0)

    def test_run_deduplicates_and_blocks_competing_jobs(self):
        self.service.control("pause")
        q = self.service.enqueue(self.project["id"], None, "produce-story")
        self.assertEqual(q["counts"]["QUEUED"], 1)
        self.assertEqual(
            self.service.enqueue(self.project["id"], None, "produce-story")["counts"][
                "QUEUED"
            ],
            1,
        )
        with self.assertRaisesRegex(ValueError, "full-video run"):
            self.service.enqueue(
                self.project["id"], self.project["chapters"][0]["id"], "analyze"
            )


if __name__ == "__main__":
    unittest.main()
