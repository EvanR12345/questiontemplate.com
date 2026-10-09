import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from studio_schedule import start_decision,strategy
import studio_overlap_test as fixtures


class SchedulingDecisions(unittest.TestCase):
    def test_alignment_requires_matching_history_or_finished_plans(self):
        self.assertFalse(start_decision('align',None,50)['start'])
        self.assertFalse(start_decision('align',100,84)['start'])
        self.assertTrue(start_decision('align',100,85)['start'])
        self.assertTrue(start_decision('align',None,None,finished=True)['start'])
        self.assertTrue(start_decision('fastest',None,None)['start'])
        for value in (float('nan'),float('inf'),-1):
            self.assertFalse(start_decision('align',value,50)['start'])

    def test_strategy_cannot_silently_fallback_to_serial_or_local(self):
        self.assertEqual(strategy({}),'legacy')
        for value in ('bad','',None):
            with self.assertRaises(ValueError):strategy({'generationStrategy':value,'overlap':True})
        with self.assertRaises(ValueError):strategy({'generationStrategy':'align','overlap':False})


class ScheduledStoryTest(unittest.TestCase):
    setUp=fixtures.CloudOverlapTest.setUp
    tearDown=fixtures.CloudOverlapTest.tearDown
    response=fixtures.CloudOverlapTest.response
    run_story=fixtures.CloudOverlapTest.run_story

    def test_fastest_admission_allows_next_direction_with_more_than_a_window_of_images(self):
        from studio_data import character
        self.service.store.mutate(self.project['id'],lambda p:p['characters'].append(
            character('Mira',kind='main') | {'references':[{'path':'face.png','kind':'face'}]}))
        self.service.store.asset(self.project['id'],'face.png').write_bytes(b'reference')
        self.service.extra_unchecked_shot=True
        self.signals['firstQC'].set()
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        original=self.service.__class__.analyze
        def direction(service,p,chid,options):
            import copy
            result=original(service,p,chid,options)
            def more(q):
                chapter=next(c for c in q['chapters'] if c['id']==chid)
                scene=chapter['scenes'][0]
                for n in range(3):
                    shot=copy.deepcopy(scene['shots'][0]);shot['id']+='-extra-'+str(n)
                    scene['shots'].append(shot)
            service.store.mutate(p['id'],more)
            return result
        with patch.object(self.service.__class__,'analyze',direction):self.run_story(generationStrategy='fastest')
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertEqual(p['production']['generationStrategy'],'fastest')
        self.assertTrue(self.signals['secondDirection'].is_set())
        self.assertEqual(len(self.provider.requests),10)
        self.assertEqual(self.provider.peak,1)

    def test_calibrated_alignment_dispatches_before_all_planning_is_finished(self):
        from studio_data import character
        self.service.store.mutate(self.project['id'],lambda p:p['characters'].append(
            character('Mira',kind='main') | {'references':[{'path':'face.png','kind':'face'}]}))
        self.service.store.asset(self.project['id'],'face.png').write_bytes(b'reference')
        self.signals['firstQC'].set()
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        with patch.object(self.service.__class__,'performance_forecast',return_value={
                'planningSeconds':40,'readyImageSeconds':30,'unknownStages':[]}):
            self.run_story(generationStrategy='align')
        self.assertTrue(self.signals['firstImage'].is_set());self.assertEqual(len(self.provider.requests),2)

    def test_fastest_missing_portraits_wait_until_ordered_planning_is_finished(self):
        self.service.wait_for_overlap=False;self.signals['firstQC'].set()
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        original=self.service.__class__.character_reference
        def reference(service,*args,**kwargs):
            self.assertTrue(self.signals['secondDirection'].is_set())
            return original(service,*args,**kwargs)
        with patch.object(self.service.__class__,'character_reference',reference):self.run_story(generationStrategy='fastest')
        self.assertEqual(len(self.provider.requests),2)

    def test_uncalibrated_align_finishes_ordered_plans_before_dispatch_without_paid_guessing(self):
        self.service.wait_for_overlap=False
        self.signals['firstQC'].set()
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        original=self.provider.generateImage
        def image(*args,**kwargs):
            self.assertTrue(self.signals['secondDirection'].is_set())
            return original(*args,**kwargs)
        with patch.object(self.provider,'generateImage',side_effect=image):self.run_story(generationStrategy='align')
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertEqual(p['production']['generationStrategy'],'align')
        self.assertEqual(len(self.provider.requests),2)
        self.assertEqual(p['production']['rentalControl'],'external')
        self.assertTrue(p['production']['scheduling']['imageDispatchStarted'])
        rows=[t for t in p['production']['timings'] if t['stage']=='Image pipeline']
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['performanceUnits']['value'],2)

    def test_manual_prompt_changed_before_delayed_admission_is_retained(self):
        self.service.wait_for_overlap=False
        original=self.service.__class__.analyze
        def direction(service,p,chid,options):
            if p['chapters'][0]['id']!=chid:
                first=service.store.load(p['id'])['chapters'][0]
                service.store.mutate(p['id'],lambda q:q['chapters'][0]['scenes'][0]['shots'][0].update(
                    prompt='My new manual prompt',manual={'prompt':True}))
            return original(service,p,chid,options)
        with patch.object(self.service.__class__,'analyze',direction):
            with self.assertRaisesRegex(Exception,'changed before image admission'):
                self.run_story(generationStrategy='align')
        self.assertEqual(len(self.provider.requests),0)
        self.assertEqual(self.service.store.load(self.project['id'])['chapters'][0]['scenes'][0]['shots'][0]['prompt'],'My new manual prompt')

    def test_calibration_database_failure_cannot_discard_completed_image_results(self):
        import sqlite3
        self.signals['firstQC'].set();self.service.wait_for_overlap=False
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        with patch.object(self.service.performance,'record',side_effect=sqlite3.OperationalError('Synthetic unavailable DB')):
            self.run_story(generationStrategy='align')
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertTrue(all(t.get('performanceId') for t in p['production']['timings'] if t.get('performanceProfile')))
        report=self.service.performance_report(p['id'])
        self.assertGreater(report['history']['observations'],0)
        self.assertIsNone(report['warning'])

    def test_pause_holds_new_dispatch_and_excludes_paused_speed_sample(self):
        self.service.wait_for_overlap=False;self.signals['firstQC'].set()
        self.service.store.mutate(self.project['id'],lambda p:p['settings'].update(qcCheckLevel='off'))
        paused=threading.Event();original=self.provider.generateImage
        def image(*args,**kwargs):
            result=original(*args,**kwargs)
            if len(self.provider.requests)==1:
                self.service.control('pause');paused.set()
            return result
        with patch.object(self.provider,'generateImage',side_effect=image),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.run_story,generationStrategy='align')
            try:
                self.assertTrue(paused.wait(4));self.assertFalse(future.done())
                self.assertEqual(len(self.provider.requests),1)
            finally:self.service.control('resume')
            future.result(6)
        p=self.service.store.load(self.project['id'])
        self.assertEqual(p['production']['status'],'COMPLETE')
        self.assertFalse(any(t['stage']=='Image pipeline' for t in p['production']['timings']))

    def test_failed_scheduled_qc_retains_assets_and_blocks_rendering(self):
        self.service.wait_for_overlap=False;self.failed_qc=True
        with self.assertRaises(Exception):self.run_story(generationStrategy='align')
        p=self.service.store.load(self.project['id'])
        self.assertTrue(p['chapters'][0]['scenes'][0]['shots'][0]['imagePath'])
        self.assertEqual(self.service.renderer.full_calls,0)
        self.assertFalse(self.service.active_executions)

    def test_partly_cached_direction_does_not_train_a_fresh_chapter_estimate(self):
        p=self.service.store.load(self.project['id'])
        def callback():
            self.service.record_timing(p['id'],'Story analyst',0,{'reused':True,'detail':True,'chapter':1})
        self.service.measured_stage(p['id'],'AI directing',callback,chapter=1)
        row=self.service.store.load(p['id'])['production']['timings'][-1]
        self.assertTrue(row['reused'])
        estimate=self.service.performance.estimate(row['performanceProfile'],row['performanceUnits'])
        self.assertIsNone(estimate['seconds'])

    def test_cancel_before_align_dispatch_keeps_plans_and_buys_no_images(self):
        self.service.wait_for_overlap=False
        entered=threading.Event();release=threading.Event()
        original=self.service.__class__.analyze
        def direction(service,p,chid,options):
            if p['chapters'][0]['id']!=chid:
                entered.set()
                if not release.wait(5):raise RuntimeError('Synthetic release timed out')
            return original(service,p,chid,options)
        with patch.object(self.service.__class__,'analyze',direction),ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(self.run_story,generationStrategy='align')
            try:
                self.assertTrue(entered.wait(3));self.service.control('cancel-current')
            finally:release.set()
            with self.assertRaises(Exception):future.result(5)
        self.assertEqual(len(self.provider.requests),0)
        self.assertTrue(self.service.store.load(self.project['id'])['chapters'][0]['scenes'])
