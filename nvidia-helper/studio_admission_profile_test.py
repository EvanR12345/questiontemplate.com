import copy,unittest
from studio_data import new_project
from studio_planner_profile import planner_profile

class AdmissionProfileTest(unittest.TestCase):
    def test_adaptive_metadata_requires_verification_and_never_changes_voice_or_forecast(self):
        p=new_project();p['settings']['image']['provider']='comfyui'
        p['settings']['director'].update(provider='openai-luna',executionMode='staged-lean',parallelism=4)
        p['settings']['qcCheckLevel']='practical';before=copy.deepcopy(p)
        value=planner_profile(p,config={'comfyModelCopies':2})
        self.assertEqual(value['imageTasks'],5)
        self.assertEqual(value['imageAdmission']['maximumAfterVerification'],5)
        self.assertEqual(value['imageAdmission']['benchmarkStatus'],'no-reliable-eight-task-gain')
        self.assertFalse(value['imageAdmission']['requiresMemoryHeadroom'])
        value=planner_profile(p,config={'comfyModelCopies':2,'experimentalAdaptiveImageAdmission':True})
        self.assertEqual(value['imageAdmission']['maximumAfterVerification'],8)
        self.assertTrue(value['imageAdmission']['requiresVerifiedWorkers'])
        self.assertTrue(value['imageAdmission']['requiresMemoryHeadroom'])
        self.assertFalse(value['imageAdmission']['forecastSpeedupApplied'])
        self.assertFalse(value['imageExecution']['multipleModelCopiesVerified'])
        self.assertEqual(value['settings']['voice'],p['settings']['voice']);self.assertEqual(p,before)
        p['settings']['qcCheckLevel']='strict'
        value=planner_profile(p,config={'comfyModelCopies':2,'experimentalAdaptiveImageAdmission':True})
        self.assertEqual(value['imageAdmission']['maximumAfterVerification'],5)

if __name__=='__main__':unittest.main()
