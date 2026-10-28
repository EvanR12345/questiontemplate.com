"""Exact review requests and bounded chains; every provider reply is synthetic."""
import copy
import json
import threading
import unittest
from unittest.mock import patch
import director_parallel_test as parallel_fixtures
import director_analysis_test as planning_fixtures
from director_staged import prepare_staged,storyboard_review_chain
from director_tasks import bounded_director_chains
from studio_service import StudioService


def group_fixture(index=0):
    sentences=[{'index':n,'text':f'Group {index}, Mira carries key past door {n}.',
        'start':n*5,'end':(n+1)*5} for n in range(6)]
    cameras=[{'shotIndex':n,'shot':'medium wide','angle':'eye level',
        'composition':'Mira foreground right'} for n in range(6)]
    shots=[{'sceneIndex':0,'startSentence':n,'endSentence':n,'characters':['mira'],
        'action':s['text'],'expression':'calm','pose':'walking','lighting':'daylight',
        'motion':'slow zoom in','transition':'cut'} for n,s in enumerate(sentences)]
    item={'storyboard':{'plan':{'scenes':[{'startSentence':0,'endSentence':5,'mood':'calm'}]},
        'detail':{'shots':shots},'cameras':cameras,
        'prompts':[{'shotIndex':n,'prompt':s['action']} for n,s in enumerate(shots)]}}
    payload={'sentences':sentences,'people':[{'id':'mira','name':'Mira'}],
        'priorState':{'characters':{'mira':{'outfit':'gray coat'}}},
        'acceptedChanges':[],'analysis':{'objects':['key']},'speakerHints':[],
        'directorPayloadVersion':6}
    return item,payload


def repairs(context):
    return {'repairs':[{'shotIndex':n,'characters':['mira'],'action':'Corrected Mira carries key.',
        'expression':'calm','pose':'walking','lighting':'daylight',
        'motion':'slow zoom in','transition':'cut',
        'camera':{'shot':'medium wide','angle':'eye level','composition':'Mira foreground right'}}
        for n in context['repairIndices']]}


class ReviewRequestParityTest(unittest.TestCase):
    def run_fixture(self,mode,chained):
        p=planning_fixtures.ChapterDirectorAnalysisTest().fixture();p['settings']['director'].update(
            executionMode=mode,cadencePerMinute=12)
        p['characters'][0]['id']='mira'
        ch=p['chapters'][0];cid=p['characters'][0]['id'];seen=[]
        groups=[group_fixture(n)[1]['sentences'] for n in range(3)]
        class Director:
            def analyzeFacts(self,context,gate):
                return {'summary':'Mira carries key','people':[],'locations':[],
                    'beats':[],'changes':[],'environmentChanges':[],
                    'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):
                return {'issues':[],'intentionalChanges':[],'objectChanges':[]}
        class Service:
            director=Director()
            def gate(self,*_):pass
            def execute(self,call,stopped=None):
                method,context=call;seen.append((method,copy.deepcopy(context)))
                if method=='planStoryboard':
                    item,_=group_fixture();out=item['storyboard']
                    for n,s in enumerate(context['sentences']):out['detail']['shots'][n]['action']=s['text']
                    return out
                if method=='repairStoryboard':return repairs(context)
                group=int(context['sentences'][0]['text'].split(',')[0].split()[-1])
                initial=not any(s['action'].startswith('Corrected') for s in context['shots'])
                return {'majorIssues':[{'shotIndex':n,'sentence':n,'issue':'wrong key owner'} for n in (1,3)]
                    if initial and group<2 else [],'advisories':['Retain this advisory.']}
            def director_calls(self,project,chid,calls,expected):
                return [self.execute(call) for call in calls]
        class ChainService(Service):
            def director_review_chains(self,project,chid,chains,expected):
                bounded_director_chains(chains,self.execute,self.gate,3)
                return True
        service=ChainService() if chained else Service()
        output=prepare_staged(service,p,ch,groups,[{'id':cid,'name':'Mira'}],
            {'characters':{cid:{'outfit':'gray coat'}}},{},'exact-signature')
        return output,seen

    def test_multiple_repairs_preserve_all_exact_requests_and_final_plans_in_both_modes(self):
        for mode in ('staged-lean','staged-review'):
            with self.subTest(mode=mode):
                serial,old_requests=self.run_fixture(mode,False)
                parallel,new_requests=self.run_fixture(mode,True)
                self.assertEqual(serial,parallel)
                canonical=lambda requests:sorted(json.dumps(call,sort_keys=True) for call in requests)
                self.assertEqual(canonical(old_requests),canonical(new_requests))
                self.assertEqual(sum(m=='checkStoryboard' for m,_ in new_requests),5)
                self.assertEqual(sum(m=='repairStoryboard' for m,_ in new_requests),2)
                self.assertEqual(len(parallel),3)

    def test_global_cadence_failure_buys_no_review(self):
        p=planning_fixtures.ChapterDirectorAnalysisTest().fixture();p['settings']['director'].update(
            executionMode='staged-lean',cadencePerMinute=120)
        ch=p['chapters'][0];cid=p['characters'][0]['id'];calls=[]
        class Director:
            def analyzeFacts(self,ctx,gate):
                return {'summary':'Mira','people':[],'locations':[],'beats':[],
                    'changes':[],'environmentChanges':[],'objects':[],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,ctx,gate):return {'objectChanges':[]}
        class Service:
            director=Director()
            def gate(self,*_):pass
            def director_calls(self,p,ch,items,expected):
                calls.extend(items);outputs=[]
                for _ in items:
                    output=group_fixture()[0]['storyboard']
                    output['detail']['shots']=output['detail']['shots'][:1]
                    output['cameras']=output['cameras'][:1];output['prompts']=output['prompts'][:1]
                    outputs.append(output)
                return outputs
            def director_review_chains(self,*args):raise AssertionError('Review before cadence guard')
        with self.assertRaisesRegex(ValueError,'cadence'):
            prepare_staged(Service(),p,ch,[group_fixture()[1]['sentences']],
                [{'id':cid,'name':'Mira'}],{'characters':{cid:{}}},{},'signature')
        self.assertTrue(calls);self.assertTrue(all(m=='planStoryboard' for m,_ in calls))

    def test_serial_service_declines_chain_runner_without_consuming_generators(self):
        class Service:
            _overlap=None
        def chain():raise AssertionError('Serial path consumed chain');yield
        self.assertFalse(StudioService.director_review_chains(Service(),
            {'settings':{'director':{'parallelism':1}}},'chapter',[chain()]))


class ReadyChainSchedulerTest(unittest.TestCase):
    def test_ready_repair_starts_before_slow_initial_sibling_and_preserves_output_order(self):
        initial_started=threading.Event();repair_started=threading.Event();seen=[]
        lock=threading.Lock();active=peak=0
        def chain(n):
            for stage in range(3):yield (n,stage)
            return n
        def execute(item,stopped):
            nonlocal active,peak
            n,stage=item
            with lock:active+=1;peak=max(peak,active);seen.append(item)
            try:
                if item==(0,0):self.assertTrue(initial_started.wait(3))
                if item==(1,0):
                    initial_started.set();self.assertTrue(repair_started.wait(3))
                if item==(0,1):repair_started.set()
                return item
            finally:
                with lock:active-=1
        result=bounded_director_chains((chain(n) for n in range(8)),execute,lambda *_:None,2)
        self.assertEqual(result,list(range(8)));self.assertEqual(peak,2)
        self.assertLess(seen.index((0,1)),seen.index((2,0)))
        self.assertEqual(len(seen),24)

    def test_worker_failure_stops_chain_stages_and_roots_drains_and_closes_on_caller(self):
        barrier=threading.Barrier(3);started=[];drained=[];closed=[];caller=threading.get_ident()
        def chain(n):
            try:
                yield (n,0)
                yield (n,1)
            finally:closed.append((n,threading.get_ident()))
        def execute(item,stopped):
            n,stage=item;started.append(item);barrier.wait(timeout=3)
            if n==0:raise ValueError('Broken initial review')
            self.assertTrue(stopped.wait(3));drained.append(n)
        with self.assertRaisesRegex(ValueError,'Broken initial'):
            bounded_director_chains((chain(n) for n in range(100)),execute,lambda *_:None,3)
        self.assertEqual(set(started),{(0,0),(1,0),(2,0)})
        self.assertEqual(set(drained),{1,2})
        self.assertEqual(sorted(closed),[(0,caller),(1,caller),(2,caller)])

    def test_failed_recheck_stops_all_new_stages_and_does_not_retry(self):
        seen=[]
        def chain():
            yield 'initial';yield 'repair';yield 'recheck'
            raise ValueError('Still has source-fidelity issues')
        with self.assertRaisesRegex(ValueError,'source-fidelity'):
            bounded_director_chains([chain()],lambda item,stop:seen.append(item),lambda *_:None,2)
        self.assertEqual(seen,['initial','repair','recheck'])


class DurableReviewChainsTest(unittest.TestCase):
    setUp=parallel_fixtures.DirectorParallelTest.setUp
    tearDown=parallel_fixtures.DirectorParallelTest.tearDown
    stream=staticmethod(parallel_fixtures.DirectorParallelTest.stream)
    ledger=parallel_fixtures.DirectorParallelTest.ledger
    requests=parallel_fixtures.DirectorParallelTest.requests

    def make_overlap(self,limit=3):
        overlap,p=parallel_fixtures.DirectorParallelTest.make_overlap(self,limit)
        def run(project,chapter,calls,expected=None):
            def chain(call):return (yield call)
            return overlap.director_review_chains(project,chapter,(chain(c) for c in calls),expected)
        # Existing real-adapter safety cases now exercise the new queue, while
        # the dedicated test below exercises actual multi-stage review chains.
        overlap.main.director_calls=run
        return overlap,p

    test_unknown_usage_stops_replay=parallel_fixtures.DirectorParallelTest.test_missing_receipt_preserves_unknown_liability_and_prevents_replay
    test_budget_rejection_buys_nothing=parallel_fixtures.DirectorParallelTest.test_budget_rejection_buys_no_request
    test_stale_before_dispatch_buys_nothing=parallel_fixtures.DirectorParallelTest.test_stale_input_before_dispatch_buys_no_call_and_keeps_edit
    test_stale_during_paid_response_settles=parallel_fixtures.DirectorParallelTest.test_edit_during_paid_response_settles_receipt_but_cannot_commit_old_plan
    test_malformed_sibling_drains_receipts=parallel_fixtures.DirectorParallelTest.test_malformed_sibling_stops_new_dispatch_and_drains_paid_receipts
    test_targeted_cancel_retains_unknown_and_drains=parallel_fixtures.DirectorParallelTest.test_cancelling_one_paid_pass_keeps_its_liability_and_drains_siblings
    test_pause_drains_paid_streams_before_new_admission=parallel_fixtures.DirectorParallelTest.test_pause_drains_paid_responses_then_holds_admission_until_resume

    def test_real_review_repair_recheck_calls_have_separate_owners_receipts_and_shared_bound(self):
        overlap,p=self.make_overlap();items=[group_fixture(n) for n in range(3)]
        slow_started=threading.Event();fast_repair=threading.Event();seen=[];lock=threading.Lock()
        def response(request,**kwargs):
            body=json.loads(request.data);role=body['input'][0]['content']
            context=json.loads(body['input'][1]['content'][0]['text'])
            group=int(context['sentences'][0]['text'].split(',')[0].split()[-1])
            with lock:index=len(seen);seen.append((role,group,copy.deepcopy(context)))
            if 'Targeted storyboard repair' in role:
                if group==0:fast_repair.set()
                value=repairs(context)
            else:
                initial='reviewShotIndices' not in context
                if initial and group==0:self.assertTrue(slow_started.wait(3))
                if initial and group==1:
                    slow_started.set();self.assertTrue(fast_repair.wait(3))
                value={'majorIssues':[{'shotKey':context['shots'][1]['shotKey'],
                    'category':'object','sentence':1,'issue':'wrong key owner'}] if initial else [],'advisories':[]}
            return self.stream(index,value)
        try:
            with patch('openai_director.OpenAIDirector.key',return_value='synthetic-key'), \
                 patch('openai_director.urllib.request.urlopen',side_effect=response):
                overlap.director_review_chains(p,p['chapters'][0]['id'],
                    (storyboard_review_chain(n,item,payload,True) for n,(item,payload) in enumerate(items)))
            self.assertTrue(fast_repair.is_set());self.assertEqual(len(seen),9)
            self.assertTrue(all(not item['visualReview']['majorIssues'] for item,_ in items))
            self.assertEqual(overlap.api_lane.active,0)
            state=self.ledger(p);self.assertFalse(state['requests']);self.assertEqual(len(state['receipts']),9)
            children=[row for row in self.service.execution_journal.snapshot(overlap.parent) if row['kind']=='director-pass']
            self.assertEqual(len(children),9);self.assertTrue(all(row['status']=='COMPLETE' for row in children))
            self.assertTrue(all(len(row['operations'])==1 for row in children))
            self.assertEqual(len({next(iter(row['operations'])) for row in children}),9)
            self.assertEqual(len(self.service.active_executions),1)
        finally:overlap.close('COMPLETE')


if __name__=='__main__':unittest.main()
