"""No-network tests for explicitly opted-in clothing-only decisions."""
import copy
import unittest
from unittest.mock import Mock, patch

from director_provider import DirectorProvider
from studio_data import digest
from studio_qc import (automatic_repairs, decision, effective_issue_levels,
    production_status, review_settings_signature, validate_checks)


def settings(**changes):
    return {'qcCheckLevel':'practical', 'unattendedProduction':True,
        'automaticRepair':True, 'maxImageRetries':1, 'qcClothingContinuity':'advisory',
        'style':'ink', 'continuityStrictness':'Medium', 'director':{}, **changes}


def review(*findings):
    return {'pass':False, 'action':'image_edit', 'repairPrompt':'Retain the story.',
        'status':'FAILED', 'issues':[f['issue'] for f in findings], 'findings':list(findings)}


def finding(issue='Outfit differs from the planned garment.', severity='major', clothing=True):
    return {'issue':issue, 'severity':severity, 'clothingOnly':clothing}


class ClothingDecisionTests(unittest.TestCase):
    def assess(self, qc, **changes):
        project={'id':'project', 'settings':settings(**changes), 'chapters':[]}
        shot={'id':'shot', 'qc':qc}
        return decision(project,shot,Mock()), project, shot

    def repairs(self, qc, **changes):
        with patch('studio_qc.repair_budget_reason',return_value=None):
            return automatic_repairs(settings(**changes),qc)

    def test_opt_in_clothing_advisory_preserves_raw_evidence_and_does_not_repair(self):
        qc=review(finding());before=copy.deepcopy(qc)
        result,project,shot=self.assess(qc)
        self.assertFalse(result['blocking']);self.assertEqual(result['disposition'],'advisory')
        self.assertEqual(result['clothingAdvisoryCount'],1)
        self.assertEqual(production_status(project,shot,Mock()),'COMPLETE')
        self.assertFalse(self.repairs(qc));self.assertEqual(qc,before)
        self.assertFalse(qc['pass']);self.assertEqual(qc['findings'][0]['severity'],'major')

    def test_missing_or_required_setting_keeps_same_major_blocker(self):
        qc=review(finding())
        for value in ('required',None):
            result,_,_=self.assess(qc,qcClothingContinuity=value)
            self.assertTrue(result['blocking']);self.assertTrue(self.repairs(qc,qcClothingContinuity=value))

    def test_each_non_clothing_major_and_mixed_compound_issue_remain_blocking(self):
        for issue in ('Wrong identity.', 'Wrong role.', 'Contradictory action.',
                'Standing instead of kneeling.', 'Hands are unrestrained.', 'Missing essential injury.',
                'Wrong weapon.', 'Extra important person.', 'Uniform identifies the wrong faction.',
                'Disguise reveals the character.', 'Armor changes the action outcome.',
                'Wrong shirt and unrestrained hands.'):
            qc=review(finding(),finding(issue,clothing=False))
            result,_,_=self.assess(qc)
            self.assertTrue(result['blocking'],issue);self.assertTrue(self.repairs(qc),issue)
            self.assertEqual(effective_issue_levels(settings(),qc),['advisory','major'])

    def test_no_keyword_inference_or_legacy_reclassification(self):
        qc=review(finding('Clearly visible wrong clothing, stripped outfit.'))
        for flag in (None,'true',1,{},[]):
            qc['findings'][0]['clothingOnly']=flag
            result,_,_=self.assess(qc)
            self.assertTrue(result['blocking']);self.assertTrue(self.repairs(qc))
        del qc['findings'][0]['clothingOnly']
        self.assertTrue(self.assess(qc)[0]['blocking'])
        self.assertTrue(self.repairs(qc))

    def test_partial_or_unaligned_semantic_fields_do_not_enable_tolerance(self):
        qc=review(finding(),finding('Other outfit mismatch.'))
        del qc['findings'][1]['clothingOnly']
        self.assertEqual(effective_issue_levels(settings(),qc),['major','major'])
        qc=review(finding());qc['findings'][0]['issue']='Different review text.'
        self.assertNotEqual(effective_issue_levels(settings(),qc),['advisory'])

    def test_strict_only_relaxes_explicit_clothing_not_other_advisories(self):
        self.assertFalse(self.assess(review(finding()),qcCheckLevel='strict')[0]['blocking'])
        self.assertFalse(self.repairs(review(finding()),qcCheckLevel='strict'))
        mixed=review(finding(),finding('Framing differs.','advisory',False))
        self.assertTrue(self.assess(mixed,qcCheckLevel='strict')[0]['blocking'])
        self.assertTrue(self.repairs(mixed,qcCheckLevel='strict'))

    def test_practical_retains_other_advisories_after_clothing_tolerance(self):
        qc=review(finding(),finding('Framing differs.','advisory',False))
        self.assertFalse(self.assess(qc)[0]['blocking']);self.assertFalse(self.repairs(qc))

    def test_stale_and_pending_reviews_remain_blocking_before_tolerance(self):
        qc=review(finding())
        qc['reviewSettingsSignature']=review_settings_signature(settings(qcClothingContinuity='required'))
        result,_,_=self.assess(qc)
        self.assertTrue(result['blocking']);self.assertEqual(result['disposition'],'pending')
        qc.pop('reviewSettingsSignature');qc['status']='PENDING'
        self.assertTrue(self.assess(qc)[0]['blocking'])

    def test_default_signature_preserves_legacy_inputs_and_advisory_is_distinct(self):
        s=settings(qcClothingContinuity='required')
        legacy=digest({'version':1,'policy':'practical','continuity':'Medium','style':'ink',
            'constraints':'','provider':None,'model':None,'reasoning':'Fast'})
        self.assertEqual(review_settings_signature(s),legacy)
        del s['qcClothingContinuity'];self.assertEqual(review_settings_signature(s),legacy)
        s['qcClothingContinuity']='advisory';self.assertNotEqual(review_settings_signature(s),legacy)

    def test_repair_budget_retry_ceiling_and_off_gates_still_apply(self):
        mixed=review(finding(),finding('Wrong identity.',clothing=False))
        self.assertFalse(self.repairs(mixed,maxImageRetries=0))
        self.assertFalse(self.repairs(mixed,automaticRepair=False))
        self.assertFalse(self.repairs(mixed,qcCheckLevel='off'))
        with patch('studio_qc.repair_budget_reason',return_value='Unverified budget'):
            self.assertFalse(automatic_repairs(settings(),mixed))

    def test_setting_validation_is_narrow(self):
        for value in ('required','advisory'):validate_checks(settings(qcClothingContinuity=value))
        for value in (None,True,'off','ignore-all'):
            with self.assertRaises(ValueError):validate_checks(settings(qcClothingContinuity=value))


class ClothingReviewSchemaTests(unittest.TestCase):
    def capture(self, context):
        provider=DirectorProvider();provider.vision_available=True
        returned=review(finding());returned['pass']=True
        provider.call=Mock(return_value=returned)
        result=provider.inspectGeneratedImage(context,lambda *a:None)
        return provider.call.call_args,result

    def test_default_and_required_wire_schema_role_are_identical(self):
        default,_=self.capture({});required,_=self.capture({'clothingContinuity':'required'})
        self.assertEqual(default.args[0],required.args[0])
        self.assertEqual(default.args[2],required.args[2])
        fields=default.args[2]['properties']['findings']['items']['properties']
        self.assertNotIn('clothingOnly',fields)

    def test_advisory_requests_explicit_semantics_without_changing_raw_pass_rules(self):
        call,result=self.capture({'clothingContinuity':'advisory'})
        schema=call.args[2]['properties']['findings']['items']
        self.assertEqual(schema['properties']['clothingOnly'],{'type':'boolean'})
        self.assertIn('clothingOnly',schema['required'])
        for condition in ('ENTIRE finding','mixed concern','uniform, disguise, armor',
                'original issues, severity','raw pass','restraint','injury'):
            self.assertIn(condition,call.args[0])
        self.assertFalse(result['pass']);self.assertEqual(result['issues'],['Outfit differs from the planned garment.'])
        self.assertTrue(call.kwargs['vision'])


if __name__=='__main__':unittest.main()
