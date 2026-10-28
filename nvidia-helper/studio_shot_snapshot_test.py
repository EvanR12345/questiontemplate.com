import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from studio_shot_snapshot import shot_snapshot,shot_snapshot_view
from studio_data import new_project,character,ProjectStore,get_shot
from studio_performance import profile_for
from studio_render_inputs import visual_spec_signature
from studio_qc import should_check,review_settings_signature
from prompt_preflight import check_shot
from image_provider import select_references,reference_prompt


class ShotSnapshotTest(unittest.TestCase):
    def test_only_top_level_production_timings_are_omitted_and_everything_else_is_independent(self):
        p={'chapters':[{'source':'exact','audio':{'timings':[1]},'scenes':[{'shots':[{'history':[3]}]}]}],
            'settings':{'voice':'unchanged','budget':{'openaiUSD':1}},'settingsHistory':[2],
            'production':{'timings':[{'historical':True}],'gpuRental':{'provider':'vast'},'costs':{'USD':.5},'scheduling':{'forecast':4}}}
        original=copy.deepcopy(p);expected=copy.deepcopy(p);del expected['production']['timings']
        snapshot=shot_snapshot(p)
        self.assertEqual(snapshot,expected);self.assertEqual(p,original)
        snapshot['chapters'][0]['scenes'][0]['shots'][0]['history'].append(9)
        snapshot['production']['gpuRental']['provider']='changed local snapshot'
        self.assertEqual(p,original)

    def test_absent_or_nonmapping_production_is_preserved(self):
        for p in ({'id':'x'},{'production':None},{'production':[]},{'production':{'other':[1]}}):
            self.assertEqual(shot_snapshot(p),p)

    def fixture(self,folder):
        p=new_project();ch=p['chapters'][0];person=character('Mira');person['references']=[dict(path='face.png',kind='face')]
        p['characters']=[person];p['settings']['image'].update(provider='comfyui',model='klein',workflow='reference-edit')
        p['settings']['director'].update(provider='openai-luna');p['settings']['qcCheckLevel']='practical'
        p['settings']['budget']={'openaiUSD':1}
        p['settings']['cloudWindow']={'gpuHourlyUSD':1,'gpuBudgetUSD':2,'gpuPreviouslySpentUSD':.1,'cloudStartedAt':100}
        p['production']={'timings':[{'stage':'Historical','seconds':55}],'gpuRental':{'provider':'vast','project':p['id'],'imageBackendFingerprint':'exact-worker'},'costs':{'apiEstimatedUSD':.1}}
        ch.update(sourceText='Mira holds a key.',cleanNarrationText='Mira holds a key.',people=[],analysis={'groups':[{'changes':[]}]})
        shot=dict(id='shot-parity',chapterId=ch['id'],sceneId='scene-parity',characters=[dict(id=person['id'],type='main',appearanceState={'outfit':'blue coat'})],
            prompt='Mira holds a brass key.',negativePrompt='',imageProvider='comfyui',imageModel='klein',workflow='reference-edit',
            generationSettings=p['settings']['image']|{'seed':42},camera={'shot':'close-up'},start=0,end=4,manual={},intentionalAppearanceChanges=[],
            narrationSegment='Mira holds a key.',qc={'status':'UNREVIEWED','pass':None})
        ch['scenes']=[dict(id='scene-parity',characters=shot['characters'],shots=[shot])]
        store=ProjectStore(folder);store.folder(p['id']).mkdir();Image.new('RGB',(4,4),'gray').save(store.asset(p['id'],'face.png'))
        return p,shot,store

    def test_quality_signatures_references_prompts_sampled_cadence_and_profiles_are_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            p,shot,store=self.fixture(folder);snapshot=shot_snapshot(p);other=get_shot(snapshot,shot['chapterId'],shot['id'])
            provider=type('Provider',(),{'getCapabilities':lambda _:{'maxReferenceImages':3,'supportsStyleReference':True}})()
            self.assertEqual(check_shot(p,shot,True),check_shot(snapshot,other,True))
            self.assertEqual(visual_spec_signature(p,shot,store),visual_spec_signature(snapshot,other,store))
            refs,metadata=select_references(p,shot,store,provider)
            self.assertEqual((refs,metadata),select_references(snapshot,other,store,provider))
            self.assertEqual(reference_prompt(p,shot,metadata),reference_prompt(snapshot,other,metadata))
            self.assertEqual(review_settings_signature(p['settings']),review_settings_signature(snapshot['settings']))
            for stage in ('Image pipeline','Image + quality checks','Image generation','Visual QC','Narration'):
                for tasks in (5,8):
                    details={'shot':shot['id'],'_shotSpec':shot,'imageTasks':tasks,'admissionMode':'adaptive-verified' if tasks==8 else 'base'}
                    self.assertEqual(profile_for(stage,p,{'comfyModelCopies':2},details),profile_for(stage,snapshot,{'comfyModelCopies':2},details))
            for level in ('off','sampled','practical','strict'):
                p['settings']['qcCheckLevel']=level;snapshot['settings']['qcCheckLevel']=level
                self.assertEqual(should_check(p,shot),should_check(snapshot,other))
            self.assertEqual(p['settings']['cloudWindow'],snapshot['settings']['cloudWindow'])
            self.assertEqual(p['settings']['budget'],snapshot['settings']['budget'])
            self.assertEqual(p['production']['gpuRental'],snapshot['production']['gpuRental'])

    def test_actual_fake_story_outputs_requests_and_durable_timing_history_are_preserved(self):
        import studio_overlap as overlap
        import studio_overlap_test as fixtures
        outcomes=[];original_submit=overlap.CloudStoryOverlap.submit_shot
        for projected in (False,True):
            serial=[0]
            def uid(prefix=''):
                serial[0]+=1;return prefix+f'{serial[0]:016x}'
            with patch('studio_data.uid',side_effect=uid),patch('full_video_test.uid',side_effect=uid):
                fixture=fixtures.CloudOverlapTest(methodName='runTest');fixture.setUp()
                try:
                    marker={'stage':'Retained historical marker','seconds':123}
                    fixture.service.store.mutate(fixture.project['id'],lambda p:p.setdefault('production',{}).update(timings=[marker]))
                    def submit(instance,project,*args,**kwargs):
                        return original_submit(instance,shot_snapshot_view(project) if projected else project,*args,**kwargs)
                    # Baseline remains a full deepcopy even once integration
                    # imports the new helper into the orchestration module.
                    with patch.object(overlap,'shot_snapshot',copy.deepcopy,create=True),patch.object(overlap.CloudStoryOverlap,'submit_shot',submit):
                        fixture.run_story()
                    saved=fixture.service.store.load(fixture.project['id'])
                    self.assertIn(marker,saved['production']['timings'])
                    frames=[hashlib.sha256(fixture.service.store.asset(saved['id'],s['imagePath']).read_bytes()).hexdigest() for ch in saved['chapters'] for sc in ch['scenes'] for s in sc['shots']]
                    outcomes.append((fixture.provider.requests,frames,saved['production']['status'],
                        [(s['status'],s['qc']['pass']) for ch in saved['chapters'] for sc in ch['scenes'] for s in sc['shots']]))
                finally:fixture.tearDown()
        self.assertEqual(outcomes[0],outcomes[1])

    def test_scheduler_signature_and_repair_caps_preserve_their_guards(self):
        from studio_schedule import chapter_signature
        from studio_automation import project_cap_reason,repair_operation
        from studio_overlap import CloudStoryOverlap
        with tempfile.TemporaryDirectory() as folder:
            p,shot,store=self.fixture(folder)
            p['settings'].update(unattendedProduction=True,maxImageRetries=2)
            p['settings']['budget']['totalUSD']=3
            p['production']['scheduling']={'strategy':'align','forecast':{'readyImageSeconds':42}}
            shot.update(automaticRepairAttempts=1,automaticRepairSpec='saved-spec')
            snapshot=shot_snapshot(p);other=get_shot(snapshot,shot['chapterId'],shot['id'])
            provider=type('Provider',(),{'id':'comfyui','getCapabilities':lambda _:{'maxReferenceImages':3,'supportsImageEditing':True}})()
            self.assertEqual(CloudStoryOverlap.source_inputs(p),CloudStoryOverlap.source_inputs(snapshot))
            self.assertEqual(chapter_signature(None,p,shot['chapterId']),chapter_signature(None,snapshot,shot['chapterId']))
            self.assertEqual(p['production']['scheduling'],snapshot['production']['scheduling'])
            self.assertEqual(repair_operation(p,shot,provider),repair_operation(snapshot,other,provider))
            self.assertEqual(other['automaticRepairAttempts'],1)
            self.assertEqual(other['automaticRepairSpec'],'saved-spec')
            self.assertIsNone(project_cap_reason(snapshot['settings']))
            snapshot['settings']['budget']['totalUSD']=1
            self.assertIsNotNone(project_cap_reason(snapshot['settings']))
            self.assertIsNone(project_cap_reason(p['settings']))
            # Changing source still invalidates the scheduling signature.
            snapshot['chapters'][0]['sourceText']='A changed source.'
            self.assertNotEqual(chapter_signature(None,p,shot['chapterId']),chapter_signature(None,snapshot,shot['chapterId']))

    def test_repair_reservation_mutates_durable_shot_and_keeps_historical_timings(self):
        from studio_service import StudioService
        with tempfile.TemporaryDirectory() as folder:
            p,shot,store=self.fixture(folder);p['settings']['maxImageRetries']=1
            shot['status']='READY_FOR_IMAGES'
            store.save(p);snapshot=shot_snapshot(p)
            service=object.__new__(StudioService);service.store=store
            service.reserve_automatic_repair(p['id'],shot['chapterId'],shot['id'],'spec')
            saved=store.load(p['id']);actual=get_shot(saved,shot['chapterId'],shot['id'])
            self.assertEqual(actual['automaticRepairAttempts'],1)
            self.assertEqual(saved['production']['timings'],p['production']['timings'])
            self.assertNotIn('automaticRepairAttempts',get_shot(snapshot,shot['chapterId'],shot['id']))
            with self.assertRaisesRegex(ValueError,'repair limit'):
                service.reserve_automatic_repair(p['id'],shot['chapterId'],shot['id'],'spec')


if __name__=='__main__':unittest.main()
