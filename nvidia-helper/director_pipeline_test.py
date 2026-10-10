"""Prove causality, frozen input ownership and failure draining for overlap."""
import copy
import threading
import unittest
from director_pipeline import AcceptedVisualQueue,source_visual_overlap_enabled,focused_storyboard_review_context
from director_staged import prepare_staged
import director_lean_test as lean_tests
from director_acceleration_test import storyboard
from director_storyboard import compile_storyboard


class AcceptedVisualTest(unittest.TestCase):
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
