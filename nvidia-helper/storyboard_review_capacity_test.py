"""Uncapped findings, unchanged source bindings and one repair; all replies synthetic."""
import copy
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from director_provider import DirectorProvider, validate_schema
from director_staged import storyboard_review_chain
from openai_director import OpenAIDirector


def request_fixture(count=30):
    return {'sentences':[{'index':n,'text':f'Actor {n} holds object {n}.'} for n in range(count)],
        'people':[], 'priorState':{},
        'shots':[{'startSentence':n,'endSentence':n,'action':f'Actor {n} holds object {n}.'} for n in range(count)]}


class Capture(DirectorProvider):
    def __init__(self, indices=(), remaining=False):
        self.indices=list(indices);self.remaining=remaining;self.requests=[];self.raw=[]
    def call(self, role, context, schema, gate):
        self.requests.append({'role':role,'context':copy.deepcopy(context),'schema':copy.deepcopy(schema)})
        if role.startswith('Targeted storyboard repair:'):
            value={'repairs':[{'shotIndex':n,'characters':['actor'],'action':'Actor holds the correct source object.',
                'expression':'calm','pose':'standing','lighting':'daylight','motion':'static','transition':'cut',
                'camera':{'shot':'medium','angle':'eye level','composition':'Actor by source object'}}
                for n in context['repairIndices']]}
        else:
            final='reviewShotIndices' in context
            indices=([self.indices[-1]] if self.remaining and self.indices else []) if final else self.indices
            shots={s['shotIndex']:s for s in context['shots']}
            value={'majorIssues':[{'shotKey':shots[n]['shotKey'],'category':'object',
                'sentence':shots[n]['startSentence'],'issue':f'Wrong essential source object in shot {n}.'}
                for n in indices], 'advisories':['Retain existing advisory.']}
        OpenAIDirector.validate_result(role, context, value, schema)
        self.raw.append(copy.deepcopy(value))
        return value


class ReviewCapacityTest(unittest.TestCase):
    def test_fifteen_findings_validate_against_actual_schema_and_restore_every_source_binding(self):
        provider=Capture(range(15))
        result=provider.checkStoryboard(request_fixture(),lambda *_:None)
        schema=provider.requests[0]['schema']
        self.assertNotIn('maxItems',schema['properties']['majorIssues'])
        self.assertEqual(schema['properties']['advisories']['maxItems'],12)
        self.assertEqual(len(result['majorIssues']),15)
        self.assertEqual([i['shotIndex'] for i in result['majorIssues']],list(range(15)))
        self.assertEqual([i['sourceQuote'] for i in result['majorIssues']],
            [f'Actor {n} holds object {n}.' for n in range(15)])
        self.assertTrue(all('shotIndex' not in item and 'sourceQuote' not in item for item in provider.raw[0]['majorIssues']))
        old=copy.deepcopy(schema);old['properties']['majorIssues']['maxItems']=12
        with self.assertRaisesRegex(ValueError,'bounds'):validate_schema(provider.raw[0],old)
        for change in ({'category':'unknown'},{'shotKey':'not-a-shot'},{'issue':'x'*301}):
            invalid=copy.deepcopy(provider.raw[0]);invalid['majorIssues'][0].update(change)
            with self.assertRaises(ValueError):validate_schema(invalid,schema)
        invalid=copy.deepcopy(provider.raw[0]);invalid['majorIssues'][0]['sentence']=29
        with self.assertRaisesRegex(ValueError,'outside'):
            OpenAIDirector.validate_result(provider.requests[0]['role'],provider.requests[0]['context'],invalid,schema)

    def test_frozen_prechange_request_diff_is_exactly_the_major_findings_cap(self):
        provider=Capture();provider.checkStoryboard(request_fixture(),lambda *_:None)
        old=copy.deepcopy(provider.requests[0]);old['schema']['properties']['majorIssues']['maxItems']=12
        # Captured from the capped production method before this change, using
        # this generic30-shot fixture. Binds role, source/context and all schema
        # fields, including category/string/source limits and advisory cap.
        digest=hashlib.sha256(json.dumps(old,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(digest,'25d031f91bdd707d765fe4eaf5dfb1b35f60a765901f2b511f54c6b15884f62a')

    def chain(self, remaining=False):
        payload=request_fixture();payload['people']=[{'id':'actor','name':'Actor'}]
        shots=[{**s,'characters':['actor'],'sceneIndex':0,'expression':'calm','pose':'standing',
            'lighting':'daylight','motion':'static','transition':'cut'} for s in payload.pop('shots')]
        item={'storyboard':{'detail':{'shots':shots},
            'cameras':[{'shotIndex':n,'shot':'medium','angle':'eye level','composition':'Actor by source object'} for n in range(30)],
            'prompts':[{'shotIndex':n,'prompt':s['action']} for n,s in enumerate(shots)]}}
        flagged=list(range(1,26,2)) #13 issues; final neighboring recheck is sparse.
        provider=Capture(flagged,remaining)
        chain=storyboard_review_chain(0,item,payload,True)
        call=next(chain)
        while True:
            method,context=call
            result=getattr(provider,method)(context,lambda *_:None)
            try:call=chain.send(result)
            except StopIteration as done:return provider,item,flagged,done.value

    def test_thirteen_findings_get_one_complete_repair_then_only_changed_shots_and_neighbors(self):
        provider,item,flagged,result=self.chain()
        self.assertEqual(result,item)
        roles=[r['role'].split(':')[0] for r in provider.requests]
        self.assertEqual(roles,['Storyboard continuity supervisor','Targeted storyboard repair','Storyboard continuity supervisor'])
        repair=provider.requests[1]['context'];final=provider.requests[2]['context']
        self.assertEqual(repair['repairIndices'],flagged)
        self.assertEqual(len(repair['issues']),13)
        self.assertEqual([s['shotIndex'] for s in repair['repairSlots']],flagged)
        self.assertEqual(final['reviewShotIndices'],list(range(27)))
        self.assertEqual([s['shotIndex'] for s in final['shots']],list(range(27)))
        self.assertEqual(len(final['sentences']),30)
        self.assertEqual(len(item['repairHistory']['initialReview']['majorIssues']),13)
        self.assertEqual(item['visualReview']['majorIssues'],[])

    def test_remaining_major_error_after_single_repair_still_fails_closed_without_another_repair(self):
        # Keep the actual provider methods/schema/restore and staged generator.
        payload=request_fixture();payload['people']=[{'id':'actor','name':'Actor'}]
        shots=payload.pop('shots')
        item={'storyboard':{'detail':{'shots':[{**s,'characters':['actor']} for s in shots]},
            'cameras':[{'shotIndex':n,'shot':'medium','angle':'eye level','composition':'Actor'} for n in range(30)],
            'prompts':[{'shotIndex':n,'prompt':s['action']} for n,s in enumerate(shots)]}}
        provider=Capture(range(1,26,2),remaining=True)
        chain=storyboard_review_chain(0,item,payload,True);call=next(chain)
        with self.assertRaisesRegex(ValueError,'after one targeted repair'):
            while True:
                method,context=call
                call=chain.send(getattr(provider,method)(context,lambda *_:None))
        self.assertEqual(len(provider.requests),3)
        self.assertEqual(sum(r['role'].startswith('Targeted storyboard repair:') for r in provider.requests),1)
        self.assertEqual(len(item['repairHistory']['initialReview']['majorIssues']),13)
        self.assertEqual(len(item['repairHistory']['finalReview']['majorIssues']),1)

    def test_actual_standard_wire_request_changes_only_that_schema_cap(self):
        bodies=[]
        def response(request,**kwargs):
            bodies.append(json.loads(request.data))
            result={'majorIssues':[],'advisories':[]}
            events=[{'type':'response.created','response':{'id':'synthetic-review'}},
                {'type':'response.output_text.delta','delta':json.dumps(result)},
                {'type':'response.completed','response':{'id':'synthetic-review','status':'completed',
                    'service_tier':'default','usage':{'input_tokens':100,'output_tokens':10}}}]
            return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
        class LegacyCap(OpenAIDirector):
            def call(self,role,context,schema,gate,vision=False):
                old=copy.deepcopy(schema);old['properties']['majorIssues']['maxItems']=12
                return super().call(role,context,old,gate,vision)
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(OpenAIDirector,'key',return_value='synthetic-test-key'), \
             patch('openai_director.urllib.request.urlopen',side_effect=response):
            for name,kind in (('capped',LegacyCap),('uncapped',OpenAIDirector)):
                provider=kind({'openaiModel':'gpt-6-luna','openaiServiceTier':'default'},Path(directory)/name)
                provider.max_output_tokens=12000
                provider.checkStoryboard(request_fixture(),lambda *_:None)
        self.assertEqual(len(bodies),2)
        old,new=map(copy.deepcopy,bodies)
        self.assertEqual(old['text']['format']['schema']['properties']['majorIssues'].pop('maxItems'),12)
        self.assertEqual(old,new)
        self.assertEqual(new['model'],'gpt-6-luna')
        self.assertEqual(new['service_tier'],'default')
        self.assertEqual(new['reasoning'],{'effort':'medium'})
        self.assertEqual(new['max_output_tokens'],12000)
        self.assertEqual(new['text']['format']['schema']['properties']['advisories']['maxItems'],12)


if __name__=='__main__':unittest.main()
