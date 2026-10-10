"""Lean requests preserve required story cuts/state and real billing profiles."""
import copy
import json
import unittest
from director_acceleration_test import storyboard
from director_analysis_test import ChapterDirectorAnalysisTest
from director_storyboard import storyboard_schema,compile_storyboard,repair_schema
from director_staged import prepare_staged
from director_provider import validate_schema
from studio_performance import profile_for
from openai_director import request_sizes
from director_wire import CompactDirectorWire


class LeanDirectorTest(unittest.TestCase):
    def test_flex_is_accepted_without_enabling_premium_or_changing_voice_cadence(self):
        from studio_data import validate_project
        p=self.fixture();p['settings']['director']['processingTier']='flex'
        voice=copy.deepcopy(p['settings']['voice']);cadence=p['settings']['director'].get('cadencePerMinute')
        validate_project(p)
        self.assertEqual(p['settings']['director']['processingTier'],'flex')
        self.assertEqual(p['settings']['voice'],voice)
        self.assertEqual(p['settings']['director'].get('cadencePerMinute'),cadence)
        p['settings']['director']['processingTier']='fast'
        with self.assertRaisesRegex(ValueError,'premium Fast'):
            validate_project(p)

    def fixture(self):
        p=ChapterDirectorAnalysisTest().fixture()
        p['settings']['director'].update(executionMode='staged-lean',parallelism=8,processingTier='default')
        return p

    def test_one_request_can_cross_state_changes_but_must_keep_each_shot_cut(self):
        p=self.fixture();ch=p['chapters'][0];cid=p['characters'][0]['id']
        sentences=ch['audio']['sentences'][:4];captured=[];facts=[]
        class Director:
            def analyzeFacts(self,context,gate):
                facts.append(copy.deepcopy(context))
                return {'summary':'state follows source','people':[],'locations':[],
                    'beats':[],'changes':[{'characterId':cid,'field':'key','value':'right hand',
                        'reason':sentences[1]['text'],'sentence':1},
                        {'characterId':cid,'field':'outfit','value':'red coat',
                         'reason':sentences[2]['text'],'sentence':2}],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):return {'objectChanges':[],'issues':[],'intentionalChanges':[]}
        class Service:
            director=Director()
            def gate(self,*args):pass
            def director_calls(self,project,chid,calls,expected):
                captured.extend(copy.deepcopy(calls))
                return [compile_storyboard(storyboard(ctx),ctx) if method=='planStoryboard'
                        else {'majorIssues':[],'advisories':[]} for method,ctx in calls]
        source=copy.deepcopy(p)
        result=prepare_staged(Service(),p,ch,[sentences],[{'id':cid,'name':'Mira','identity':{'hair':'black'}}],
                              {'characters':{cid:{'outfit':'old coat'}}},{},'signature')
        visuals=[ctx for method,ctx in captured if method=='planStoryboard']
        self.assertEqual(len(visuals),1)
        self.assertEqual(visuals[0]['requiredVisualChangeBoundaries'],[0,1,2])
        self.assertEqual(visuals[0]['priorState']['characters'][cid]['outfit'],'old coat')
        self.assertEqual(visuals[0]['people'][0]['identity']['hair'],'black')
        self.assertNotIn('knownMainCharacters',visuals[0]);self.assertNotIn('chapterCast',visuals[0])
        self.assertNotIn('layoutMode',facts[0]);self.assertNotIn('beats',visuals[0]['analysis'])
        self.assertNotIn('appearanceHistory',visuals[0]['priorState'])
        self.assertEqual(visuals[0]['directorPayloadVersion'],6)
        self.assertTrue(all('visualDirection' not in ctx for method,ctx in captured if method=='checkStoryboard'))
        self.assertEqual([e['sentence'] for e in result[0]['analysis']['changes']],[1,2])
        self.assertEqual(p,source)
        bad=storyboard(visuals[0]);bad['cuts']['cut1']=None
        with self.assertRaisesRegex(ValueError,'required|cadence'):compile_storyboard(bad,visuals[0])

    def test_source_keyed_cuts_are_unique_and_schema_is_not_repeated(self):
        ctx={'people':[{'id':'mira','name':'Mira'}],'sentences':[{'text':'Mira walks.'} for _ in range(12)],
             'directorPayloadVersion':6,'requiredVisualChangeBoundaries':[0,4]}
        schema=storyboard_schema(ctx);value=storyboard(ctx)
        self.assertLess(len(json.dumps(schema)),len(json.dumps(storyboard_schema({**ctx,'directorPayloadVersion':2}))))
        value['cuts']['cut2']=None;validate_schema(value,schema)
        codec=CompactDirectorWire(ctx,schema)
        self.assertEqual(codec.keys['cut4'],'cut4')
        def encode(value):
            if isinstance(value,dict):return {codec.keys.get(k,k):encode(v) for k,v in value.items()}
            if isinstance(value,list):return [encode(v) for v in value]
            return codec.ids.get(value,value) if isinstance(value,str) else value
        encoded=encode(value);validate_schema(encoded,codec.schema)
        self.assertEqual(codec.decode(encoded),value)
        output=compile_storyboard(value,ctx)
        starts=[s['startSentence'] for s in output['detail']['shots']]
        self.assertEqual(starts,sorted(set(starts)));self.assertNotIn(2,starts)
        value['cuts']['cut4']=None
        with self.assertRaises(ValueError):validate_schema(value,schema)
        with self.assertRaises(ValueError):compile_storyboard(value,ctx)
        with self.assertRaises(ValueError):validate_schema({}, {'$ref':'https://example.com/schema'})
        with self.assertRaises(ValueError):validate_schema({}, {'$ref':'#/$defs/missing'})

    def test_null_validator_rejects_non_null_values(self):
        validate_schema(None,{'type':'null'})
        for invalid in [{},[],False,0,'null']:
            with self.assertRaises(ValueError):validate_schema(invalid,{'type':'null'})

    def test_sustained_restraint_and_posture_survive_the_next_visual_request(self):
        from director_staged import apply_source_changes
        from image_provider import visible_appearance
        events=[{'characterId':'mira','type':'restraint','to':{'restraint':'hands tied'},'reason':'Mira has her hands tied.'},
                {'characterId':'mira','type':'posture','to':{'posture':'kneeling'},'reason':'Mira kneels on the floor.'}]
        state=apply_source_changes({'characters':{'mira':{'outfit':'red coat'}}},events,1,'facts')
        current=copy.deepcopy(state);current.pop('appearanceHistory')
        self.assertEqual(current['characters']['mira'],{'outfit':'red coat','restraint':'hands tied','posture':'kneeling'})
        self.assertEqual(len(state['appearanceHistory']),2)
        appearance=visible_appearance({'action':'Mira looks toward the doorway.'},{'appearanceState':current['characters']['mira']})
        self.assertEqual(appearance['restraint'],'hands tied');self.assertEqual(appearance['posture'],'kneeling')
        released=apply_source_changes(state,[{'characterId':'mira','type':'restraint','to':{'restraint':'released'},
            'reason':'Mira is released.'},{'characterId':'mira','type':'posture','to':{'posture':'standing'},
            'reason':'Mira stands up.'}],2,'facts')
        self.assertEqual(released['characters']['mira']['restraint'],'released')
        self.assertEqual(released['characters']['mira']['posture'],'standing')
        self.assertEqual(state['characters']['mira']['posture'],'kneeling')

    def test_repair_schema_retains_flagged_indices_and_source_context(self):
        ctx={'people':[{'id':'mira'}],'sentences':[{'text':'Mira holds the key.'}],
             'directorPayloadVersion':4,'repairIndices':[0]}
        schema=repair_schema(ctx)
        self.assertEqual(schema['properties']['repairs']['items']['properties']['shotIndex']['enum'],[0])
        self.assertNotIn('startSentence',schema['properties']['repairs']['items']['properties'])

    def test_learning_never_mixes_old_full_payload_or_fast_tier_with_lean_standard(self):
        p=self.fixture();lean=profile_for('AI directing',p,{})
        self.assertEqual(lean['planVersion'],9);self.assertEqual(lean['tier'],'default')
        p['settings']['director']['executionMode']='staged-review'
        old=profile_for('AI directing',p,{})
        self.assertNotEqual(lean,old);self.assertEqual(old['planVersion'],2)

    def test_request_metrics_are_counts_not_source_text_tokens_or_credentials(self):
        result=request_sizes('instructions',{'sentences':[{'text':'private story'}]}, {'type':'object'})
        self.assertEqual(result['unit'],'characters')
        self.assertNotIn('private story',json.dumps(result))
        self.assertTrue(all(type(n) is int for n in result['contextFields'].values()))

    def test_repair_recheck_preserves_global_indices_and_full_source_with_neighbors(self):
        from director_staged import repair_review_context
        source=[{'text':str(n)} for n in range(20)]
        payload={'sentences':source,'priorState':{'key':'right hand'}}
        output={'detail':{'shots':[{'startSentence':n*2,'endSentence':n*2+1} for n in range(10)]},
                'cameras':[{'shotIndex':n,'shot':'medium'} for n in range(10)]}
        before=copy.deepcopy(output)
        context=repair_review_context(payload,output,[4,9])
        self.assertEqual(context['reviewShotIndices'],[3,4,5,8,9])
        self.assertEqual([s['shotIndex'] for s in context['shots']],[3,4,5,8,9])
        self.assertEqual(context['sentences'],source)
        self.assertEqual(context['priorState'],payload['priorState'])
        self.assertEqual(output,before)
        from director_provider import DirectorProvider
        class Reviewer(DirectorProvider):
            def call(self,role,context,schema,gate):
                self.allowed=schema['properties']['majorIssues']['items']['properties']['shotIndex']['enum']
                return {'majorIssues':[{'shotIndex':9,'sentence':18,'issue':'wrong owner'}],'advisories':[]}
        reviewer=Reviewer()
        review=reviewer.checkStoryboard(context,lambda *args:None)
        self.assertEqual(reviewer.allowed,[3,4,5,8,9])
        self.assertEqual(review['majorIssues'][0]['sourceQuote'],'18')
        self.assertEqual(review['coverage']['scope'],'repair-and-neighbors')
        context['shots'][0]['shotIndex']=0
        with self.assertRaisesRegex(ValueError,'indices'):reviewer.checkStoryboard(context,lambda *args:None)

    def test_scope_keeps_cast_aliases_previous_and_manual_actors_without_erasing_library(self):
        from director_staged import relevant_main_cast
        known=[{'id':'m','name':'Mira','aliases':['scarred traveler'],'identity':{'hair':'brown'}},
               {'id':'v','name':'Vaan','aliases':[]},{'id':'s','name':'Sarah','aliases':[]}]
        before=copy.deepcopy(known)
        chapter={'sourceText':'The scarred traveler opens the door.','scenes':[]}
        self.assertEqual([c['id'] for c in relevant_main_cast(known,chapter,[])],['m'])
        previous={'scenes':[{'shots':[{'characters':[{'id':'s'}]}]}]}
        self.assertEqual([c['id'] for c in relevant_main_cast(known,chapter,[],previous)],['m','s'])
        casting=[{'pass':'casting-supervisor','output':{'people':[{'id':'v','name':'Vaan'}]}}]
        self.assertEqual([c['id'] for c in relevant_main_cast(known,chapter,casting)],['m','v'])
        self.assertEqual(relevant_main_cast(known,{'sourceText':'He looks up.','scenes':[]},[]),known)
        self.assertEqual(known,before)

if __name__=='__main__':unittest.main()
