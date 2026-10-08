"""Intro reuse and manual edits must survive preparation without paid calls."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock
from studio_data import ProjectStore, new_project, validate_project
from studio_service import StudioService
import full_video_test as fixtures


class IntroPreparationTest(unittest.TestCase):
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
