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
        self.assertEqual(old['planVersion'],11);self.assertEqual(compact['planVersion'],13)
        self.assertNotEqual(old,compact)
        self.project['settings']['director']['processingTier']='flex'
        self.assertNotEqual(compact,profile_for('AI directing',self.project,{}))
    def test_changed_request_packing_and_fact_windows_never_inherit_smaller_history(self):
        d=self.project['settings']['director'];d.update(executionMode='staged-lean',factGroupSentences=128)
        original=profile_for('AI directing',self.project,{})
        d['visualPacking']='large';large=profile_for('AI directing',self.project,{})
        self.assertEqual(large['visualGroupLimit'],{'sentences':40,'characters':18000,'targetShots':20})
        self.assertNotEqual(original,large)
        d['factGroupSentences']=256
        self.assertNotEqual(large,profile_for('AI directing',self.project,{}))
        wide=profile_for('AI directing',self.project,{})
        d['sourceVisualOverlap']=True
        self.assertNotEqual(wide,profile_for('AI directing',self.project,{}))
        self.assertEqual(profile_for('AI directing',self.project,{})['acceptedVisualDispatchVersion'],2)
        self.assertNotIn('acceptedVisualDispatchVersion',wide)
        before=profile_for('AI directing',self.project,{})
        d['factBeatsMode']='storyboard'
        after=profile_for('AI directing',self.project,{})
        self.assertEqual(after['factsVersion'],5);self.assertEqual(before['factsVersion'],4)
        self.assertNotEqual(before,after)
    def test_one_run_does_not_outvote_three_independent_runs(self):
        for i in range(20):self.add('a'+str(i),'r1',100)
        self.add('b','r2',10);self.add('c','r3',10)
        row=self.store.estimate(self.profile,{'kind':'characters','value':100},10001)
        self.assertEqual(row['seconds'],10);self.assertEqual(row['runs'],3)
        self.assertEqual(row['rangeSeconds'],[10,100])
    def test_qc_lookup_matches_actual_director_callback_instead_of_image_model(self):
        p=self.project;p['settings']['director'].update(provider='openai-luna',qcReasoning='Fast')
        expected=profile_for('Visual QC',p,{'openaiModel':'gpt-6-luna'})
        actual=profile_for('Visual QC',p,{'openaiModel':'gpt-6-luna'},{'provider':'openai-luna','model':'gpt-6-luna','reasoning':'low'})
        self.assertEqual(actual,expected);self.assertEqual(actual['model'],'gpt-6-luna')
        self.store.record('qc','run',actual,{'kind':'images','value':1},6,{'estimatedUSD':.0003})
        self.assertEqual(self.store.estimate(expected,{'kind':'images','value':1})['seconds'],6)

    def test_nested_whole_review_does_not_mix_with_api_latency_samples(self):
        api=profile_for('Visual QC',self.project,{})
        whole=profile_for('Visual QC',self.project,{}, {'reviewTimingScope':'whole-review'})
        self.assertNotEqual(api,whole)
        self.store.record('api','same-run',api,{'kind':'images','value':1},6,{'estimatedUSD':.0003})
        self.store.record('parent','same-run',whole,{'kind':'images','value':1},60,{})
        self.assertEqual(self.store.estimate(api,{'kind':'images','value':1})['seconds'],6)
        self.assertEqual(self.store.estimate(whole,{'kind':'images','value':1})['seconds'],60)

    def test_qc_slots_and_tiers_isolate_history_without_changing_voice_or_render(self):
        p=self.project;d=p['settings']['director'];d.update(parallelism=4,processingTier='default')
        current=profile_for('Visual QC',p,{})
        voice=profile_for('Narration',p,{});render=profile_for('Render chapter',p,{})
        legacy=dict(current);legacy.pop('reviewAPISlots');legacy.pop('processingTier')
        self.store.record('legacy-qc','old',legacy,{'kind':'images','value':1},1,{})
        self.assertIsNone(self.store.estimate(current,{'kind':'images','value':1})['seconds'])
        self.store.record('current-qc','four',current,{'kind':'images','value':1},8,{})
        d['parallelism']=8
        eight=profile_for('Visual QC',p,{})
        self.assertEqual(eight['reviewAPISlots'],8)
        self.assertIsNone(self.store.estimate(eight,{'kind':'images','value':1})['seconds'])
        d['parallelism']=4;d['processingTier']='flex'
        flex=profile_for('Visual QC',p,{})
        self.assertEqual(flex['processingTier'],'flex')
        self.assertIsNone(self.store.estimate(flex,{'kind':'images','value':1})['seconds'])
        explicit=profile_for('Visual QC',p,{}, {'reviewAPISlots':2})
        self.assertEqual(explicit['reviewAPISlots'],2)
        existing_override=profile_for('Visual QC',p,{}, {'directorTasks':3})
        self.assertEqual(existing_override['reviewAPISlots'],3)
        self.assertEqual(profile_for('Narration',p,{}),voice)
        self.assertEqual(profile_for('Render chapter',p,{}),render)
        self.assertEqual(self.store.estimate(legacy,{'kind':'images','value':1})['seconds'],1)
        self.assertEqual(self.store.estimate(current,{'kind':'images','value':1})['seconds'],8)

    def test_private_keys_story_ids_and_endpoints_are_absent_from_profiles(self):
        config={'openaiApiKey':'SECRET','comfyEndpoint':'https://private-host.example','comfySshHost':'private-ip'}
        for stage in ('AI directing','Image generation','Narration','Render chapter'):
            raw=json.dumps(profile_for(stage,self.project,config))
            for value in ('SECRET','private-host','private-ip',self.project['id'],'A tale.'):
                self.assertNotIn(value,raw)

    def test_cpu_and_native_rendering_cannot_teach_each_others_speed(self):
        units={'kind':'videoSeconds','value':120}
        cpu=profile_for('Render chapter',self.project,{'renderBackend':'cpu'})
        native=profile_for('Render chapter',self.project,{'renderBackend':'native'})
        self.store.record('cpu','cpu-run',cpu,units,150,{})
        self.store.record('native','native-run',native,units,14,{})
        self.assertEqual(self.store.estimate(cpu,units)['seconds'],150)
        self.assertEqual(self.store.estimate(native,units)['seconds'],14)
        fallback=profile_for('Render chapter',self.project,{'renderBackend':'native'},{'renderBackend':'cpu'})
        self.assertEqual(fallback,cpu)
        old=dict(cpu);old.pop('renderTimingVersion');old['renderer']='auto'
        self.store.record('legacy','legacy-run',old,units,1,{})
        self.assertEqual(self.store.estimate(cpu,units)['samples'],1)

    def test_changed_render_effects_and_capacity_require_matching_history(self):
        old=profile_for('Render chapter',self.project,{'renderBackend':'cpu','renderWorkers':1})
        self.assertNotEqual(old,profile_for('Render chapter',self.project,{'renderBackend':'cpu','renderWorkers':3}))
        self.project['settings']['watermark']={'enabled':True,'text':'Studio'}
        self.assertNotEqual(old,profile_for('Render chapter',self.project,{'renderBackend':'cpu','renderWorkers':1}))

    def test_native_sampled_progress_does_not_inherit_per_frame_commit_timings(self):
        units={'kind':'videoSeconds','value':120}
        for stage in ('Render chapter','Assemble full video'):
            with self.subTest(stage=stage):
                current=profile_for(stage,self.project,{'renderBackend':'native'})
                self.assertEqual(current['frameProgressPolicy'],'sampled-250ms-v1')
                old=dict(current);old.pop('frameProgressPolicy')
                self.store.record(stage+'-old','old-run',old,units,120,{})
                self.assertIsNone(self.store.estimate(current,units)['seconds'])
                self.store.record(stage+'-new','new-run',current,units,30,{})
                self.assertEqual(self.store.estimate(current,units)['seconds'],30)
                self.assertEqual(self.store.estimate(current,units)['samples'],1)
                # Incompatible history remains available for diagnostics.
                self.assertEqual(self.store.estimate(old,units)['seconds'],120)

    def test_frame_progress_policy_does_not_change_cpu_fallback_or_voice_history(self):
        for stage in ('Render chapter','Assemble full video'):
            cpu=profile_for(stage,self.project,{'renderBackend':'cpu'})
            fallback=profile_for(stage,self.project,{'renderBackend':'native'},{'renderBackend':'cpu'})
            self.assertEqual(cpu,fallback)
            self.assertNotIn('frameProgressPolicy',cpu)
        cpu_voice=profile_for('Narration',self.project,{'renderBackend':'cpu'})
        native_voice=profile_for('Narration',self.project,{'renderBackend':'native'})
        self.assertEqual(cpu_voice,native_voice)
        self.assertNotIn('frameProgressPolicy',native_voice)
        units={'kind':'characters','value':100}
        self.store.record('voice-unchanged','voice-run',cpu_voice,units,17,{})
        self.assertEqual(self.store.estimate(native_voice,units)['seconds'],17)
    def test_deferred_repairs_have_separate_compatible_timing_history(self):
        old=profile_for('Image pipeline',self.project,{})
        self.project['settings'].update(unattendedProduction=True,qcRepairBudgetMode='project-cap')
        current=profile_for('Image pipeline',self.project,{})
        self.assertNotEqual(old,current)
        repair=profile_for('Automatic image repair',self.project,{})
        self.assertEqual(repair['repairTiming'],'deferred')
        self.assertEqual(observation_units('Automatic image repair',self.project,{}),{'kind':'images','value':1})
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

    def test_kaggle_single_jobs_do_not_inherit_a_grouped_timing_profile(self):
        self.project['settings'].update(voiceEngine='qwen3-tts-1.7b',voiceChapterBatchSize=4)
        single=profile_for('Narration',self.project,{})
        grouped=profile_for('Narration',self.project,{}, {'voiceBatch':{'chapters':2}})
        self.assertEqual(single['chapterBatchSize'],1)
        self.assertEqual(grouped['chapterBatchSize'],2)
        self.store.record('single','run',single,{'kind':'characters','value':100},10,{})
        self.assertIsNone(self.store.estimate(grouped,{'kind':'characters','value':100})['seconds'])
