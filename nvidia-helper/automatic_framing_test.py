import unittest

from scene_repair import framing_subjects
from studio_automation import fresh_scene_repair


class AutomaticFramingTests(unittest.TestCase):
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
