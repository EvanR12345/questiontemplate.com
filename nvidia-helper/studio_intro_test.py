"""Intro reuse and manual edits must survive preparation without paid calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from studio_data import ProjectStore, new_project, validate_project
from studio_service import StudioService
from studio_render import VideoRenderer
import full_video_test as fixtures


class IntroPreparationTest(unittest.TestCase):
    def test_full_resume_regenerates_missing_intro_file_without_rebuying_chapter_images(self):
        fixture=fixtures.FullVideoTest();fixture.setUp();service=fixture.service
        try:
            service.produce_story(fixture.project['id'],{})
            before=sum(c[0]=='image' for c in service.calls)
            service.store.mutate(fixture.project['id'],lambda p:p['intro'].update(
                enabled=True,visualPrompt='A story-supported opening.',visualPath='missing-intro.png'))
            def replacement(p,options):
                service.store.asset(p['id'],'replacement-intro.png').write_bytes(b'fixture intro')
                service.store.mutate(p['id'],lambda latest:latest['intro'].update(visualPath='replacement-intro.png'))
            service.intro_image=Mock(side_effect=replacement)
            service.produce_story(fixture.project['id'],{})
            service.intro_image.assert_called_once()
            self.assertEqual(sum(c[0]=='image' for c in service.calls),before)
            self.assertEqual(service.store.load(fixture.project['id'])['production']['status'],'COMPLETE')
        finally:fixture.tearDown()

    def test_review_metadata_does_not_rerender_intro_but_major_flags_block_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder);project=store.save(new_project())
            project['settings']['qcCheckLevel']='practical'
            shot={'id':'intro-shot','imagePath':'art.png','start':0,'end':15,'motion':'static','qc':{'pass':True}}
            project['intro'].update(enabled=True,duration=15,shots=[shot])
            store.asset(project['id'],'art.png').write_bytes(b'unchanged image')
            renderer=VideoRenderer(store,{})
            renderer.cached_asset=Mock(return_value=True)
            original=renderer.raw_intro(project,lambda *_:None)
            shot['qc']={'pass':False,'issues':['Framing slightly wider'],'findings':[{'issue':'Framing slightly wider','severity':'advisory'}]}
            self.assertEqual(renderer.raw_intro(project,lambda *_:None),original)
            shot['qc']={'pass':False,'issues':['Wrong main character'],'findings':[{'issue':'Wrong main character','severity':'major'}]}
            renderer.cached_asset.reset_mock()
            with self.assertRaisesRegex(ValueError,'needs review'):
                renderer.raw_intro(project,lambda *_:None)
            renderer.cached_asset.assert_not_called()
            project['settings']['qcCheckLevel']='off'
            self.assertEqual(renderer.raw_intro(project,lambda *_:None),original)

    def test_reuse_checks_script_and_voice_but_explicit_regeneration_still_runs(self):
        with tempfile.TemporaryDirectory() as folder:
            service=StudioService.__new__(StudioService);service.store=ProjectStore(folder)
            project=new_project();project['intro']['voiceText']='A specific conflict, without invented events.'
            project=service.store.save(project)
            def synthesize(text,voice,speed,target,*options):
                target.write_bytes(b'narration fixture')
                return {'duration':12,'sentences':[]}
            service.create_audio=Mock(side_effect=synthesize)
            service.intro_audio(project,True)
            saved=service.store.load(project['id'])
            self.assertTrue(saved['renderStale'])
            service.intro_audio(saved,True)
            self.assertEqual(service.create_audio.call_count,1)
            saved['settings']['voice']='bm_george'
            service.intro_audio(saved,True)
            self.assertEqual(service.create_audio.call_count,2)
            service.intro_audio(service.store.load(project['id']))
            self.assertEqual(service.create_audio.call_count,3)

    def test_prepare_keeps_existing_intro_shots_and_images(self):
        fixture=fixtures.FullVideoTest();fixture.setUp();service=fixture.service
        try:
            project=new_project();project['intro'].update(enabled=True,voiceText='Saved hook.',sourceBrief='Verified events.',
                shots=[{'id':'saved-shot','prompt':'Manual composition','manual':{'prompt':True},'imagePath':'saved.png'}])
            project=service.store.save(project)
            service.intro_audio=Mock();service.plan_intro=Mock();service.before_audio=Mock()
            before=copy.deepcopy(project['intro']['shots'])
            service.prepare_story(project['id'],{})
            service.plan_intro.assert_not_called()
            self.assertEqual(service.store.load(project['id'])['intro']['shots'],before)
        finally:fixture.tearDown()

    def test_end_on_narration_accepts_only_boolean_and_preserves_legacy_projects(self):
        project=new_project();self.assertTrue(project['intro']['endOnNarration'])
        for value in ('false',0,None):
            project['intro']['endOnNarration']=value
            with self.assertRaisesRegex(ValueError,'end-on-narration'):validate_project(project)
        project['intro'].pop('endOnNarration')
        validate_project(project)


if __name__=='__main__':unittest.main()
