import copy,json,tempfile,unittest
from pathlib import Path
from studio_data import new_project
from studio_planner_profile import planner_profile
from studio_performance import PerformanceStore,profile_for
from studio_qc import check_level

class PlannerProfileTest(unittest.TestCase):
    def test_new_projects_check_all_images_but_do_not_auto_repair(self):
        p=new_project();self.assertTrue(p['settings']['visionQC'])
        self.assertEqual(check_level(p['settings']),'practical');self.assertFalse(p['settings']['automaticRepair'])

    def test_private_content_and_spending_grants_are_not_exposed(self):
        p=new_project();p['chapters'][0]['sourceText']='PRIVATE CHAPTER'
        p['settings']['apiKey']='PRIVATE KEY';p['settings']['image']['references']=['private/photo.png']
        p['intro']['voiceText']='PRIVATE INTRO';before=copy.deepcopy(p)
        r=planner_profile(p);encoded=json.dumps(r)
        for word in ('PRIVATE CHAPTER','PRIVATE KEY','private/photo.png','PRIVATE INTRO','budget'):
            self.assertNotIn(word,encoded)
        self.assertEqual(r['settings']['qcCheckLevel'],'practical');self.assertEqual(p,before)

    def test_saved_off_and_legacy_behavior_are_preserved(self):
        p=new_project();p['settings']['qcCheckLevel']='off'
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'off')
        del p['settings']['qcCheckLevel'];p['settings']['visionQC']=False
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'off')
        p['settings']['generationMode']='MAX QUALITY'
        self.assertEqual(planner_profile(p)['settings']['qcCheckLevel'],'practical')

    def test_partial_narration_is_not_claimed_complete(self):
        p=new_project();c=p['chapters'][0];c['sourceText']='Hello';c['audio']={'duration':20}
        self.assertTrue(planner_profile(p)['narrationComplete'])
        c2=copy.deepcopy(c);c2['audio']={};p['chapters'].append(c2)
        self.assertFalse(planner_profile(p)['narrationComplete'])

    def test_new_pipeline_admission_does_not_inherit_old_three_task_throughput(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project()
            p['settings']['director'].update(executionMode='staged-lean',parallelism=4)
            old=profile_for('Image pipeline',p,{}, {'imageTasks':3})
            db.record('old','run',old,{'kind':'images','value':1},9,{})
            r=planner_profile(p,db,{})
            self.assertEqual(r['imageTasks'],5)
            self.assertIsNone(r['estimates']['images']['seconds'])
            self.assertEqual(profile_for('Image pipeline',p,{})['imageTasks'],5)
            db.close()

    def test_history_only_matches_recorded_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            db=PerformanceStore(Path(folder)/'history.db');p=new_project();cfg={}
            profile=profile_for('Visual QC',p,cfg)
            db.record('ok','run',profile,{'kind':'images','value':1},7,{'estimatedUSD':.0002})
            db.record('bad','run',profile,{'kind':'images','value':1},200,{'status':'FAILED'})
            r=planner_profile(p,db,cfg)['estimates']['visualQC']
            self.assertEqual(r['seconds'],7);self.assertEqual(r['samples'],1)
            p['settings']['image']['model']='different'
            self.assertIsNone(planner_profile(p,db,cfg)['estimates']['visualQC']['seconds']);db.close()

if __name__=='__main__':unittest.main()
