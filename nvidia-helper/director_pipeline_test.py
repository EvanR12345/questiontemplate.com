"""Prove causality, frozen input ownership and failure draining for overlap."""
import copy
import contextvars
import heapq
import json
import threading
import unittest
from director_pipeline import AcceptedVisualQueue,source_visual_overlap_enabled,focused_storyboard_review_context
from director_staged import prepare_staged
import director_lean_test as lean_tests
from director_acceleration_test import storyboard
from director_storyboard import compile_storyboard


class RequestFixture:
    """Synthetic request-owned executor; no model, socket or provider calls."""
    def __init__(self,limit=4):
        self.cv=threading.Condition();self.release={};self.started={};self.finished=set()
        self.clock=0;self.active=0;self.peak=0;self.factories=[];self.stop_events=[]
        self.trace=[];self.context=contextvars.ContextVar('accepted-test-context',default=None)
        self.api_lane=type('Lane',(),{'capacity':limit})()
        fixture=self
        class Service:
            def gate(self,*_):pass
            def director_calls(self,*_):raise AssertionError('Production request dispatcher bypassed its owned executor')
        self.main=Service();self.main._overlap=self

    def _director_executor(self,project,chapter,expected):
        self.factories.append((copy.deepcopy(project),chapter,expected))
        def execute(item,stopped):
            name=item[1]['id'];event=self.release.setdefault(name,threading.Event())
            with self.cv:
                if stopped.is_set():raise RuntimeError('dispatch cancelled')
                self.stop_events.append(stopped);self.active+=1;self.peak=max(self.peak,self.active)
                self.started[name]=self.clock
                self.trace.append((name,copy.deepcopy(item),self.context.get()))
                self.cv.notify_all()
            try:
                if not event.wait(3):raise AssertionError('Synthetic receipt not released: '+name)
                if item[1].get('fail'):raise ValueError('synthetic paid failure')
                return {'id':name,'accepted':copy.deepcopy(item[1])}
            finally:
                with self.cv:
                    self.active-=1;self.finished.add(name);self.cv.notify_all()
        return execute

    def wait_started(self,*names):
        with self.cv:
            if not self.cv.wait_for(lambda:all(n in self.started for n in names),timeout=2):
                raise AssertionError('Requests did not start: '+repr(names))

    def release_at(self,clock,*names):
        with self.cv:self.clock=clock
        for name in names:self.release[name].set()

    def release_all(self):
        with self.cv:
            for event in self.release.values():event.set()


def replay_separate_maps(groups,limit):
    """Exact deterministic old batch barrier, using synthetic duration units."""
    now=0;starts={}
    for group in groups:
        active=[]
        for name,duration in group:
            if len(active)>=limit:now=heapq.heappop(active)
            starts[name]=now;heapq.heappush(active,now+duration)
        if active:now=max(active)
    return now,starts


class AcceptedVisualTest(unittest.TestCase):
    def request_project(self,parallelism=4):
        return {'settings':{'director':{'parallelism':parallelism}},'state':{'coat':'gray'}}

    def test_one_dispatcher_removes_slow_batch_tail_with_same_four_slots_and_work(self):
        fixture=RequestFixture();project=self.request_project();fixture.context.set('frozen-parent')
        groups=[[('A0',12),('A1',2),('A2',2),('A3',2)],
                [('B0',6),('B1',6),('B2',6),('B3',6)]]
        calls=[[('planStoryboard',{'id':n,'duration':d,'state':{'coat':'gray'}}) for n,d in g] for g in groups]
        frozen=copy.deepcopy(calls)
        try:
            with AcceptedVisualQueue(fixture.main,project,'chapter','exact-hash',True) as queue:
                queue.add(calls[0]);fixture.wait_started('A0','A1','A2','A3')
                queue.add(calls[1]);project['state']['coat']='red';calls[1][0][1]['state']['coat']='red'
                fixture.release_at(2,'A1','A2','A3');fixture.wait_started('B0','B1','B2')
                self.assertNotIn('B3',fixture.started);self.assertNotIn('A0',fixture.finished)
                fixture.release_at(8,'B0','B1','B2');fixture.wait_started('B3')
                fixture.release_at(12,'A0')
                fixture.release_at(14,'B3')
                results=queue.finish(calls[0]+calls[1])
        finally:fixture.release_all()
        baseline,old_starts=replay_separate_maps(groups,4)
        durations=dict(sum(groups,[]));candidate=max(fixture.started[n]+d for n,d in durations.items())
        self.assertEqual((baseline,candidate),(18,14))
        self.assertEqual(sum(durations.values()),42)
        self.assertEqual(len(fixture.trace),8);self.assertEqual(fixture.peak,4)
        self.assertEqual(len({id(event) for event in fixture.stop_events}),1)
        self.assertEqual([r['id'] for r in results],list(durations))
        self.assertEqual(sorted(json.dumps(item,sort_keys=True) for _,item,_ in fixture.trace),
                         sorted(json.dumps(item,sort_keys=True) for g in frozen for item in g))
        self.assertEqual({ctx for _,_,ctx in fixture.trace},{'frozen-parent'})
        self.assertEqual([p['state']['coat'] for p,_,_ in fixture.factories],['gray','gray'])
        self.assertEqual(old_starts['B0'],12);self.assertEqual(fixture.started['B0'],2)

    def test_two_frozen_groups_bound_third_admission_and_preserve_out_of_order_results(self):
        fixture=RequestFixture(2);entered=threading.Event();added=threading.Event();errors=[]
        try:
            with AcceptedVisualQueue(fixture.main,self.request_project(2),'ch','sha',True) as queue:
                queue.add([('planStoryboard',{'id':'first'})]);fixture.wait_started('first')
                queue.add([('planStoryboard',{'id':'second'})]);fixture.wait_started('second')
                def third():
                    entered.set()
                    try:queue.add([('planStoryboard',{'id':'third'})]);added.set()
                    except BaseException as error:errors.append(error)
                thread=threading.Thread(target=third);thread.start();self.assertTrue(entered.wait(2))
                self.assertFalse(added.wait(.03));self.assertEqual(len(fixture.factories),2)
                fixture.release_at(1,'second');self.assertFalse(added.wait(.03))
                fixture.release_at(2,'first');self.assertTrue(added.wait(2));thread.join(2)
                fixture.wait_started('third');fixture.release_at(3,'third')
                results=queue.finish([])
                self.assertEqual([r['id'] for r in results],['first','second','third'])
                self.assertLessEqual(len(queue.groups),2);self.assertEqual(errors,[])
        finally:fixture.release_all()

    def test_known_failure_closes_all_group_admission_but_drains_paid_sibling(self):
        fixture=RequestFixture(2)
        try:
            with AcceptedVisualQueue(fixture.main,self.request_project(2),'ch','sha',True) as queue:
                queue.add([('planStoryboard',{'id':'bad','fail':True}),('planStoryboard',{'id':'paid'})])
                fixture.wait_started('bad','paid')
                queue.add([('planStoryboard',{'id':'never'})])
                fixture.release_at(1,'bad')
                self.assertTrue(queue.stopped.wait(2));self.assertNotIn('never',fixture.started)
                with self.assertRaisesRegex(ValueError,'synthetic paid failure'):queue.add([('planStoryboard',{'id':'future'})])
                fixture.release_at(2,'paid')
                with self.assertRaisesRegex(ValueError,'synthetic paid failure'):queue.finish([])
                self.assertIn('paid',fixture.finished)
        finally:fixture.release_all()
        self.assertEqual(set(fixture.started),{'bad','paid'})

    def test_failure_event_is_shared_with_active_sibling_retry_gate(self):
        fixture=RequestFixture(2);retry_blocked=threading.Event();ready=threading.Event()
        original=fixture._director_executor
        def factory(*args):
            owned=original(*args)
            def execute(item,stopped):
                if item[1]['id']!='retry':return owned(item,stopped)
                ready.set()
                if not stopped.wait(2):raise AssertionError('Sibling failure did not stop retry')
                retry_blocked.set()
                raise RuntimeError('retry cancelled before dispatch')
            return execute
        fixture._director_executor=factory
        try:
            with AcceptedVisualQueue(fixture.main,self.request_project(2),'ch','sha',True) as queue:
                queue.add([('planStoryboard',{'id':'bad','fail':True})]);fixture.wait_started('bad')
                queue.add([('planStoryboard',{'id':'retry'})]);self.assertTrue(ready.wait(2))
                fixture.release_at(1,'bad');self.assertTrue(retry_blocked.wait(2))
                with self.assertRaises((ValueError,RuntimeError)):queue.finish([])
        finally:fixture.release_all()
        self.assertEqual(set(fixture.started),{'bad'})

    def test_future_fact_failure_cancels_unsubmitted_calls_and_drains_bought_receipts(self):
        fixture=RequestFixture(2);timer=None
        try:
            with self.assertRaisesRegex(ValueError,'future source rejected'):
                with AcceptedVisualQueue(fixture.main,self.request_project(2),'ch','sha',True) as queue:
                    queue.add([('planStoryboard',{'id':str(n)}) for n in range(6)])
                    fixture.wait_started('0','1')
                    timer=threading.Timer(.03,fixture.release_all);timer.start()
                    raise ValueError('future source rejected')
            self.assertEqual(set(fixture.started),{'0','1'});self.assertEqual(fixture.active,0)
        finally:
            fixture.release_all()
            if timer:timer.cancel()

    def test_immediate_cache_results_and_empty_groups_do_not_deadlock_or_add_work(self):
        fixture=RequestFixture(2);seen=[]
        def factory(*args):
            def execute(item,stopped):
                self.assertFalse(stopped.is_set());seen.append(item);return item[1]['id']
            return execute
        fixture._director_executor=factory
        with AcceptedVisualQueue(fixture.main,self.request_project(4),'ch','sha',True) as queue:
            self.assertEqual(queue.limit,2)
            queue.add([])
            queue.add([('planStoryboard',{'id':n}) for n in range(16)])
            self.assertEqual(queue.finish([]),list(range(16)))
        self.assertEqual(len(seen),16)

    def test_factory_failure_stops_sibling_and_preserves_receipt_drain(self):
        fixture=RequestFixture(2);original=fixture._director_executor;timer=None
        def factory(*args):
            if fixture.factories:raise ValueError('request owner unavailable')
            return original(*args)
        fixture._director_executor=factory
        try:
            with AcceptedVisualQueue(fixture.main,self.request_project(2),'ch','sha',True) as queue:
                queue.add([('planStoryboard',{'id':'paid'})]);fixture.wait_started('paid')
                timer=threading.Timer(.03,fixture.release_all);timer.start()
                with self.assertRaisesRegex(ValueError,'request owner unavailable'):
                    queue.add([('planStoryboard',{'id':'never'})])
                self.assertTrue(queue.stopped.is_set())
            self.assertIn('paid',fixture.finished);self.assertEqual(set(fixture.started),{'paid'})
        finally:
            fixture.release_all()
            if timer:timer.cancel()

    def test_focused_review_preserves_fidelity_evidence_and_never_mutates_generation_input(self):
        context={'sentences':[{'index':0,'text':'Mira changes her coat.','start':1,'end':2}],
            'people':[{'id':'mira','identity':{'hair':'brown'},'defaultAppearance':{'outfit':'gray coat'}}],
            'priorState':{'characters':{'mira':{'key':'right hand'}}},
            'acceptedChanges':[{'sentence':0,'characterId':'mira','field':'outfit','value':'red coat'}],
            'analysis':{'objects':['key'],'environmentChanges':[]},
            'speakerHints':[{'sentence':0,'characterId':'mira','cueSentence':0}],
            'shots':[{'startSentence':0,'endSentence':0,'characters':['mira'],'action':'Mira puts on a red coat.',
                      'pose':'standing','lighting':'dusk','motion':'slow zoom in','transition':'cut','sceneIndex':0,'shotIndex':8}],
            'cameras':[{'shotIndex':8,'shot':'medium','angle':'eye level','composition':'Mira right; key in right hand'}],
            'reviewShotIndices':[8],'repairIndices':[8],'cadenceTarget':{'imagesPerMinute':6},
            'customTargets':{'pacing':'slow'},'style':'fantasy','productionDirection':'zoom slowly'}
        original=copy.deepcopy(context);focused=focused_storyboard_review_context(context)
        for key in ('people','priorState','acceptedChanges','analysis','speakerHints','cameras','reviewShotIndices','repairIndices'):
            self.assertEqual(focused[key],context[key])
        self.assertEqual(focused['sentences'][0],{'index':0,'text':'Mira changes her coat.'})
        for key in ('startSentence','endSentence','characters','action','pose','lighting','shotIndex'):
            self.assertEqual(focused['shots'][0][key],context['shots'][0][key])
        self.assertNotIn('motion',focused['shots'][0]);self.assertNotIn('cadenceTarget',focused)
        self.assertEqual(context,original)
    def test_visuals_start_before_next_facts_without_borrowing_future_state(self):
        p=lean_tests.LeanDirectorTest().fixture();d=p['settings']['director'];d.update(sourceVisualOverlap=True)
        d['factBeatsMode']='storyboard'
        ch=p['chapters'][0];cid=p['characters'][0]['id'];groups=[ch['audio']['sentences'][:2],ch['audio']['sentences'][2:4]]
        began=threading.Event();release=threading.Event();fact_count=[0];seen=[]
        class Director:
            def analyzeFacts(self,context,gate):
                fact_count[0]+=1
                if fact_count[0]==2:
                    if not began.wait(2):raise AssertionError('Visuals did not overlap the next facts')
                    release.set()
                return {'summary':'Mira progresses','people':[],'locations':[],'beats':[],
                    'changes':[{'characterId':cid,'field':'outfit','value':'red coat','sentence':1,
                        'reason':context['sentences'][1]['text']}] if fact_count[0]==2 else [],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):return {'issues':[],'intentionalChanges':[],'objectChanges':[]}
        class Service:
            director=Director()
            def gate(self,*_):pass
            def director_calls(self,project,chid,calls,expected):
                if calls[0][0]=='planStoryboard':
                    if not began.is_set():
                        began.set()
                        if not release.wait(2):raise AssertionError('Next fact pass never ran')
                    seen.extend(copy.deepcopy(ctx) for _,ctx in calls)
                return [compile_storyboard(storyboard(ctx),ctx) if method=='planStoryboard'
                        else {'majorIssues':[],'advisories':[]} for method,ctx in calls]
        service=Service();service._overlap=type('Overlap',(),{'main':service})()
        original=copy.deepcopy(p)
        output=prepare_staged(service,p,ch,groups,[{'id':cid,'name':'Mira'}],
            {'characters':{cid:{'outfit':'gray coat'}}},{},'source-hash')
        self.assertEqual(len(output),2);self.assertEqual(fact_count[0],2)
        self.assertEqual(seen[0]['acceptedChanges'],[])
        self.assertEqual(seen[0]['priorState']['characters'][cid]['outfit'],'gray coat')
        self.assertEqual(output[1]['analysis']['changes'][0]['value'],'red coat')
        self.assertEqual(output[0]['analysis']['beats'][0]['emotion'],'calm')
        self.assertEqual(output[0]['analysis']['beats'][0]['origin'],'accepted-storyboard-scene')
        self.assertEqual(output[0]['analysis']['beats'][0]['action'],groups[0][0]['text'])
        self.assertEqual(p,original)

    def test_future_source_failure_waits_for_already_accepted_paid_work(self):
        entered=threading.Event();release=threading.Event();finished=threading.Event()
        class Service:
            def director_calls(self,*_):
                entered.set()
                if not release.wait(2):raise AssertionError('Receipt drain timed out')
                finished.set();return ['accepted']
        timer=threading.Timer(.05,release.set)
        try:
            with self.assertRaisesRegex(ValueError,'source failed'):
                with AcceptedVisualQueue(Service(),{},'ch','hash',True) as queue:
                    queue.add([('visual',{})]);self.assertTrue(entered.wait(2));timer.start()
                    raise ValueError('source failed')
            self.assertTrue(finished.is_set())
        finally:release.set();timer.cancel()

    def test_visual_failure_blocks_new_batches_and_does_not_return_partial_plan(self):
        failed=threading.Event();calls=[]
        class Service:
            def director_calls(self,*args):
                calls.append(args);failed.set();raise ValueError('invalid visual')
        with AcceptedVisualQueue(Service(),{},'ch','hash',True) as queue:
            queue.add([('visual',{})]);self.assertTrue(failed.wait(2))
            # Await the completed task so no thread scheduling race masks it.
            with self.assertRaisesRegex(ValueError,'invalid visual'):queue.pending[0].result()
            with self.assertRaisesRegex(ValueError,'invalid visual'):queue.add([('future',{})])
            with self.assertRaisesRegex(ValueError,'invalid visual'):queue.finish([])
        self.assertEqual(len(calls),1)

    def test_optional_overlap_requires_safe_lane_and_never_changes_legacy_calls(self):
        class Service:
            def director_calls(self,project,ch,calls,expected):return [calls]
        service=Service();director={'executionMode':'staged-lean','sourceVisualOverlap':True,'parallelism':8}
        self.assertFalse(source_visual_overlap_enabled(service,director))
        service._overlap=type('Overlap',(),{'main':service})()
        self.assertTrue(source_visual_overlap_enabled(service,director))
        self.assertFalse(source_visual_overlap_enabled(service,{**director,'parallelism':1}))
        with self.assertRaises(ValueError):source_visual_overlap_enabled(service,{**director,'sourceVisualOverlap':1})
        with self.assertRaises(ValueError):source_visual_overlap_enabled(service,{**director,'executionMode':'classic'})
        with AcceptedVisualQueue(service,{},'ch','hash') as queue:
            queue.add([('ignored',{})]);self.assertEqual(queue.finish([('original',{})]),[[('original',{})]])


if __name__=='__main__':unittest.main()
