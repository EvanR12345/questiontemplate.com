import copy
import json
import tempfile
import unittest
from pathlib import Path
from studio_data import new_project
from studio_performance import PerformanceStore,profile_for,observation_units,interval_union,HostExecutionClock


class PerformanceTest(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.path=Path(self.folder.name)/'p.sqlite3'
        self.store=PerformanceStore(self.path)
        self.project=new_project();self.project['chapters'][0]['sourceText']='A tale.'
        self.profile=profile_for('AI directing',self.project,{})
    def tearDown(self):self.store.close();self.folder.cleanup()
    def add(self,key,run,seconds,**flags):
        return self.store.record(key,run,self.profile,{'kind':'characters','value':100},seconds,
            {'status':'COMPLETE','estimatedUSD':.001,**flags},10000)
    def test_durable_reopen_idempotency_and_latest_compatible_records(self):
        self.assertTrue(self.add('one','r1',10));self.assertFalse(self.add('one','r1',1000))
        self.store.close();self.store=PerformanceStore(self.path)
        row=self.store.estimate(self.profile,{'kind':'characters','value':200},10001)
        self.assertEqual(row['seconds'],20);self.assertEqual(row['costUSD'],.002)
        self.assertEqual(row['runs'],1);self.assertEqual(row['status'],'LIMITED_HISTORY')
    def test_failures_reused_paused_unknown_are_retained_but_not_speed_samples(self):
        for i,flags in enumerate(({'status':'FAILED'},{'reused':True},{'paused':True},{'usagePending':True},{'hostInterrupted':True})):
            self.add(str(i),'r'+str(i),1,**flags)
        row=self.store.estimate(self.profile,{'kind':'characters','value':100},10001)
        self.assertEqual(row['retained'],5);self.assertIsNone(row['seconds'])
    def test_host_sampler_distinguishes_remote_wait_from_suspended_execution(self):
        tick=[0.];clock=HostExecutionClock(clock=lambda:tick[0])
        for second in range(2,62,2):
            tick[0]=second;self.assertEqual(clock.sample(),0)
        tick[0]+=3602;self.assertEqual(clock.sample(),3600)
        tick[0]+=2;self.assertEqual(clock.sample(),3600)
    def test_model_settings_and_execution_changes_do_not_inherit_history(self):
        self.add('one','r1',10)
        for field,value in (('model','other'),('reasoning','High'),('parallelism',3),('concise',True)):
            profile={**self.profile,field:value}
            self.assertIsNone(self.store.estimate(profile,{'kind':'characters','value':100},10001)['seconds'])
    def test_compact_storyboard_format_keeps_older_and_flex_measurements_separate(self):
        self.project['settings']['director'].update(executionMode='staged-lean',compactCuts=False)
        old=profile_for('AI directing',self.project,{})
        self.project['settings']['director']['compactCuts']=True
        compact=profile_for('AI directing',self.project,{})
        self.assertEqual(old['planVersion'],9);self.assertEqual(compact['planVersion'],10)
        self.assertNotEqual(old,compact)
        self.project['settings']['director']['processingTier']='flex'
        self.assertNotEqual(compact,profile_for('AI directing',self.project,{}))
    def test_one_run_does_not_outvote_three_independent_runs(self):
        for i in range(20):self.add('a'+str(i),'r1',100)
        self.add('b','r2',10);self.add('c','r3',10)
        row=self.store.estimate(self.profile,{'kind':'characters','value':100},10001)
        self.assertEqual(row['seconds'],10);self.assertEqual(row['runs'],3)
        self.assertEqual(row['rangeSeconds'],[10,100])
    def test_private_keys_story_ids_and_endpoints_are_absent_from_profiles(self):
        config={'openaiApiKey':'SECRET','comfyEndpoint':'https://private-host.example','comfySshHost':'private-ip'}
        for stage in ('AI directing','Image generation','Narration','Render chapter'):
            raw=json.dumps(profile_for(stage,self.project,config))
            for value in ('SECRET','private-host','private-ip',self.project['id'],'A tale.'):
                self.assertNotIn(value,raw)
    def test_expired_history_and_invalid_units_never_become_zero_speed(self):
        self.add('one','r1',10)
        self.assertIsNone(self.store.estimate(self.profile,{'kind':'characters','value':100},10000+91*86400)['seconds'])
        self.assertIsNone(self.store.estimate(self.profile,{'kind':'characters','value':0},10001)['seconds'])
    def test_concurrent_pipeline_intervals_are_counted_once(self):
        self.assertEqual(interval_union([(0,10),(3,8),(7,12),(15,17)]),14)
        self.assertEqual(observation_units('Image pipeline',self.project,{'images':8}),{'kind':'images','value':8})
    def test_voice_effect_and_pipeline_concurrency_changes_require_new_history(self):
        a=profile_for('Narration',self.project,{})
        self.project['settings']['soundEffects']='off'
        self.assertNotEqual(a,profile_for('Narration',self.project,{}))
        self.assertNotEqual(profile_for('Image pipeline',self.project,{}, {'imageTasks':2}),
            profile_for('Image pipeline',self.project,{}, {'imageTasks':3}))
