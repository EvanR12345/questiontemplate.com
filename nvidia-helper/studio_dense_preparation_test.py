"""Offline staging/order regressions; no model, GPU or paid API requests."""
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import studio_dense_phase_test as dense
import studio_overlap_test as fixtures
from image_provider import select_references
from studio_data import get_shot,uid
from studio_overlap import CloudStoryOverlap,StaleExecution
from studio_service import JobCancelled


class DensePreparationTest(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown

    def prepared(self,count=2,two_chapters=False):
        overlap=dense.DensePhaseTest.prepared(self,count)
        if not two_chapters:return overlap
        overlap.close('COMPLETE')
        def append(project):
            chapter=copy.deepcopy(project['chapters'][0])
            chapter.update(id=uid('ch-'),number=2,name='Second chapter')
            for scene in chapter['scenes']:
                scene['id']=uid('scene-')
                for shot in scene['shots']:
                    shot.update(id=uid('shot-'),chapterId=chapter['id'],sceneId=scene['id'])
            project['chapters'].append(chapter)
        self.service.store.mutate(self.project['id'],append)
        self.project=self.service.store.load(self.project['id'])
        return CloudStoryOverlap(self.service,self.project,{'overlap':True,'generationStrategy':'dense'})

    def test_all_chapters_prepare_before_first_gpu_admission_and_are_not_repeated(self):
        overlap=self.prepared(two_chapters=True);events=[];reviews=[]
        before=self.service.store.load(self.project['id'])
        expected={s['id']:copy.deepcopy(s['generationSettings'])|{'model':s['imageModel']}
            for c in before['chapters'] for sc in c['scenes'] for s in sc['shots']}
        references={s['id']:select_references(before,s,self.service.store,self.provider)
            for c in before['chapters'] for sc in c['scenes'] for s in sc['shots']}
        normalize=overlap.main.prepare_production_chapter_image_settings
        preflight=overlap.main.preflight_chapter
        ensure=self.rental.ensure
        def prepare(pid,chid,*args):
            events.append(('normalize',chid));return normalize(pid,chid,*args)
        def check(pid,chid,*args):
            result=preflight(pid,chid,*args);events.append(('preflight',chid));return result
        def start():
            events.append(('ensure',None));return ensure()
        def response(*args,**kwargs):
            reviews.append(None);return dense.DensePhaseTest.response(len(reviews))
        try:
            with patch.object(overlap.main,'prepare_production_chapter_image_settings',side_effect=prepare), \
                 patch.object(overlap.main,'preflight_chapter',side_effect=check), \
                 patch.object(self.rental,'ensure',side_effect=start), \
                 patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                overlap.main.run_dense_image_phase(self.project['id'])
            first=next(i for i,event in enumerate(events) if event[0]=='ensure')
            self.assertEqual(events[:first],[(kind,c['id']) for c in before['chapters'] for kind in ('normalize','preflight')])
            self.assertEqual(sum(kind=='normalize' for kind,_ in events),2)
            self.assertEqual(sum(kind=='preflight' for kind,_ in events),2)
            after=self.service.store.load(self.project['id'])
            self.assertEqual(after['settings'],before['settings'])
            self.assertEqual(len(reviews),len(expected))
            for chapter in after['chapters']:
                for scene in chapter['scenes']:
                    for shot in scene['shots']:
                        self.assertEqual(shot['imageMetadata']['requestedSettings'],expected[shot['id']])
                        self.assertEqual(shot['imageMetadata']['referenceImages'],references[shot['id']][1])
                        self.assertTrue(shot['qc']['pass'])
            self.assertTrue(all(request['referenceImages']==next(iter(references.values()))[0] for request in self.provider.requests))
            self.assertEqual(self.rental.starts,1)
        finally:overlap.close('COMPLETE')

    def test_edit_between_normalization_and_preflight_is_not_certified_or_overwritten(self):
        overlap=self.prepared();pid=self.project['id'];chid=self.project['chapters'][0]['id']
        sid=self.project['chapters'][0]['scenes'][0]['shots'][0]['id']
        normalize=overlap.main.prepare_production_chapter_image_settings
        def edit_after_normalization(*args):
            result=normalize(*args)
            self.service.store.mutate(pid,lambda p:get_shot(p,chid,sid).update(prompt='Newer prompt during preparation.'))
            return result
        try:
            with patch.object(overlap.main,'prepare_production_chapter_image_settings',side_effect=edit_after_normalization):
                with self.assertRaisesRegex(StaleExecution,'changed after image preparation'):
                    overlap.main.prepare_dense_image_phase(pid)
            self.assertEqual(get_shot(self.service.store.load(pid),chid,sid)['prompt'],'Newer prompt during preparation.')
            self.assertFalse(overlap.dense_preflight_complete)
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
        finally:overlap.close('COMPLETE')

    def test_changed_prepared_prompt_fails_before_gpu_and_preserves_manual_edit(self):
        overlap=self.prepared();pid=self.project['id'];chid=self.project['chapters'][0]['id']
        try:
            overlap.main.prepare_dense_image_phase(pid)
            sid=self.project['chapters'][0]['scenes'][0]['shots'][0]['id']
            def edit(project):
                shot=get_shot(project,chid,sid)
                shot['prompt']='Newer manually edited narration illustration.'
                shot.setdefault('manual',{})['prompt']=True
            self.service.store.mutate(pid,edit)
            with self.assertRaisesRegex(StaleExecution,'changed after image preparation'):
                overlap.main.generate_production_chapter_images(pid,chid,0,[chid])
            self.assertEqual(get_shot(self.service.store.load(pid),chid,sid)['prompt'],'Newer manually edited narration illustration.')
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
        finally:overlap.close('COMPLETE')

    def test_changed_prepared_settings_fail_before_gpu(self):
        overlap=self.prepared();pid=self.project['id'];chid=self.project['chapters'][0]['id']
        try:
            overlap.main.prepare_dense_image_phase(pid)
            self.service.store.mutate(pid,lambda p:p['settings'].update(speed=1.1))
            with self.assertRaisesRegex(ValueError,'settings changed'):
                overlap.main.generate_production_chapter_images(pid,chid,0,[chid])
            self.assertEqual(self.service.store.load(pid)['settings']['speed'],1.1)
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
        finally:overlap.close('COMPLETE')

    def test_cancel_during_preparation_never_joins_or_admits_gpu(self):
        overlap=self.prepared();pid=self.project['id'];check=overlap.main.preflight_chapter
        def cancel_after_check(*args):
            result=check(*args)
            with self.service.cv:self.service.cancel=True
            return result
        try:
            with patch.object(overlap.main,'preflight_chapter',side_effect=cancel_after_check), \
                 patch.object(overlap.main,'ensure_production_gpu') as ensure:
                with self.assertRaises(JobCancelled):overlap.main.prepare_dense_image_phase(pid)
            ensure.assert_not_called()
            self.assertFalse(overlap.dense_preflight_complete)
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
            with self.service.cv:self.service.cancel=False
            chid=self.project['chapters'][0]['id']
            with self.assertRaisesRegex(RuntimeError,'complete accepted-plan preflight'):
                overlap.main.generate_production_chapter_images(pid,chid,0,[chid])
            self.assertEqual(self.rental.starts,0)
        finally:
            with self.service.cv:self.service.cancel=False
            overlap.close('COMPLETE')

    def test_cpu_preparation_finishes_before_waiting_for_owned_prewarm(self):
        overlap=self.prepared(two_chapters=True);events=[];pid=self.project['id']
        preflight=overlap.main.preflight_chapter
        def check(*args):
            result=preflight(*args);events.append(('preflight',args[1]));return result
        overlap.dense_prewarm=SimpleNamespace(retired=False,check=lambda:None,
            join=lambda:events.append(('join',None)),close=lambda **kwargs:None)
        overlap.dense_prewarm_proof={'test':'owned startup'}
        try:
            with patch.object(overlap.main,'preflight_chapter',side_effect=check), \
                 patch('studio_dense_prewarm.validate_final'):
                overlap.main.prepare_dense_image_phase(pid)
            self.assertEqual(events,[('preflight',c['id']) for c in self.project['chapters']]+[('join',None)])
            self.assertTrue(overlap.dense_preflight_complete)
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
        finally:overlap.close('COMPLETE')

    def test_cancel_after_preparation_never_admits_gpu(self):
        overlap=self.prepared();pid=self.project['id'];chid=self.project['chapters'][0]['id']
        try:
            overlap.main.prepare_dense_image_phase(pid)
            with self.service.cv:self.service.cancel=True
            with self.assertRaises(JobCancelled):overlap.main.generate_production_chapter_images(pid,chid,0,[chid])
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
        finally:
            with self.service.cv:self.service.cancel=False
            overlap.close('COMPLETE')

    def test_provider_rejection_during_preparation_never_admits_gpu(self):
        overlap=self.prepared();pid=self.project['id']
        try:
            with patch.object(self.provider,'validateSettings',side_effect=ValueError('Selected workflow settings rejected')):
                with self.assertRaisesRegex(ValueError,'settings rejected'):overlap.main.run_dense_image_phase(pid)
            self.assertEqual(self.rental.starts,0);self.assertEqual(self.provider.requests,[])
            self.assertFalse(overlap.dense_preflight_complete)
        finally:overlap.close('COMPLETE')


if __name__=='__main__':unittest.main()
