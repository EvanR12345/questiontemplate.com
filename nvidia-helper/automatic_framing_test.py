import unittest

from scene_repair import framing_subjects,closeup_subject,compose_repair,planning_context,FACE_CLOSEUP_BRIEF
from studio_automation import fresh_scene_repair


class AutomaticFramingTests(unittest.TestCase):
    def test_face_only_framing_uses_one_saved_subject_and_the_original_portrait(self):
        from studio_data import new_project,character
        p=new_project();person=character('Mira');p['characters']=[person]
        portrait={'path':'references/mira.png','kind':'face'};person['references']=[portrait]
        shot=self.shot();shot['characters']=shot['characters'][:1];shot['characters'][0].update(id=person['id'],type='main')
        shot.update(chapterId=p['chapters'][0]['id'],imageModel='flux2-klein-4b',imageProvider='comfyui',
                    camera={'shot':'extreme close-up','composition':'Frame her face and eyes.'})
        finding={'issue':'Mira is wearing a jacket despite the specified stripped state.','severity':'major'}
        qc={'pass':False,'action':'image_edit','issues':[finding['issue']],'findings':[finding]}
        self.assertEqual(planning_context(p,shot,qc)['framingGuidance']['instruction'],FACE_CLOSEUP_BRIEF)
        meta=[portrait|{'characterId':person['id']}]
        self.assertEqual(compose_repair(p,shot,qc,'Mira narrows her eyes.',meta),
                         'Mira narrows her eyes. Use image 1 for Mira’s permanent facial identity only.')
        from unittest.mock import patch
        with patch('scene_reference_binding.compose',return_value='normal reference guidance') as general:
            for refs in (meta+meta,[meta[0]|{'path':'derived.png'}],[meta[0]|{'characterId':'other'}]):
                self.assertEqual(compose_repair(p,shot,qc,'Corrected scene.',refs),'normal reference guidance')
            self.assertEqual(general.call_count,3)
        shot['manual']={'camera':True}
        self.assertNotIn('framingGuidance',planning_context(p,shot,qc))

    def test_face_only_framing_excludes_other_models_cast_and_a_hand_in_the_saved_frame(self):
        from copy import deepcopy
        base=self.shot();base['characters']=base['characters'][:1]
        base.update(imageProvider='comfyui',imageModel='flux2-klein-4b',
                    camera={'shot':'close-up','composition':'Her eyes fill the frame.'})
        self.assertEqual(closeup_subject(base),'captured')
        for delta in (dict(imageModel='other'),dict(characters=self.shot()['characters']),
                      dict(camera={'shot':'medium','composition':'Her face is visible.'}),
                      dict(camera={'shot':'close-up','composition':'Show her face and her hand holding the letter.'}),
                      dict(camera={'shot':'close-up','composition':'A sealed letter.'})):
            self.assertIsNone(closeup_subject(deepcopy(base)|delta))

    @staticmethod
    def shot():
        return {'characters':[{'id':'captured','appearanceState':{'outfit':'Stripped of his clothes.'}},
                              {'id':'captain','appearanceState':{'outfit':'white suit'}}],
                'manual':{}}

    def test_uses_first_repair_slot_to_reframe_a_confirmed_incompatible_garment(self):
        shot=self.shot()
        finding={'issue':'The captive is wearing trousers despite the specified stripped state.','severity':'major'}
        qc={'pass':False,'action':'image_edit','findings':[finding],'issues':[finding['issue']]}
        self.assertEqual(framing_subjects({},shot,qc),['captured'])
        self.assertTrue(fresh_scene_repair({},qc,shot=shot))

    def test_preserves_manual_composition_policy_and_advisory_classification(self):
        from copy import deepcopy
        finding={'issue':'He is wearing trousers despite the specified stripped state.','severity':'major','clothingOnly':True}
        qc={'pass':False,'action':'image_edit','findings':[finding],'issues':[finding['issue']]}
        for field in ('prompt','camera','referenceImages'):
            shot=self.shot();shot['manual'][field]=True
            self.assertFalse(framing_subjects({},shot,qc))
        self.assertFalse(framing_subjects({'automaticSceneRestaging':False},self.shot(),qc))
        self.assertFalse(framing_subjects({'qcClothingContinuity':'advisory'},self.shot(),qc))
        for outfit in ('shirtless, wearing dark trousers','stripped of his jacket','not naked','white suit'):
            shot=self.shot();shot['characters'][0]['appearanceState']['outfit']=outfit
            self.assertFalse(framing_subjects({},shot,qc))
        for text in ('The wrong person is holding a glass.','The white suit has the wrong colour.'):
            copy=deepcopy(qc);copy['issues']=[text];copy['findings'][0]['issue']=text
            self.assertFalse(framing_subjects({},self.shot(),copy))

    def test_attribution_does_not_reframe_the_wrong_person(self):
        project={'characters':[{'id':'captured','name':'River Smith'},{'id':'captain','name':'Elias Jones'}]}
        shot=self.shot()
        def owners(issue):
            qc={'pass':False,'action':'image_edit','issues':[issue],'findings':[{'issue':issue,'severity':'major'}]}
            return framing_subjects({},shot,qc,project=project)
        self.assertEqual(owners('River is wearing shorts despite his stripped state.'),['captured'])
        for issue in ('Elias is wearing shorts despite his stripped state.',
                      'River appears stripped although he should be wearing trousers.',
                      'River is not wearing trousers and is correctly stripped.',
                      'River is clothed, while Elias appears stripped.'):
            self.assertEqual(owners(issue),[])
        shot['characters'][1]['appearanceState']['outfit']='Stripped of his clothes.'
        self.assertEqual(owners('One person is wearing shorts despite the specified stripped state.'),[])
        self.assertEqual(owners('Elias is wearing shorts despite his stripped state.'),['captain'])
