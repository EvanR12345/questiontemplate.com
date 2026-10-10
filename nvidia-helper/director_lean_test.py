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
    def test_storyboard_owned_beats_preserve_source_fact_requirements_and_ai_mood(self):
        from director_provider import DirectorProvider
        class Captured(DirectorProvider):
            def call(self,role,context,schema,gate):
                self.role=role;self.schema=schema
                return {'summary':'Mira holds key','locations':[],'beats':[],'changes':[],
                    'objectChanges':[],'environmentChanges':[],'dialogueSpeakers':[],
                    'objects':['key'],'goals':[],'unresolved':[]}
        provider=Captured();context={'sentences':[{'index':0,'text':'Mira takes the key.'}],
            'chapterCast':[{'id':'mira','name':'Mira'}],'visualPlanningOwnsBeats':True}
        result=provider.analyzeFacts(context,lambda *_:None)
        self.assertEqual(provider.schema['properties']['beats']['maxItems'],0)
        self.assertIn('extract ALL source facts',provider.role)
        self.assertIn('changes',provider.schema['required']);self.assertIn('dialogueSpeakers',provider.schema['required'])
        self.assertEqual(result['objects'],['key']);self.assertEqual(result['beats'],[])
        from director_pipeline import fact_beats_mode
        self.assertEqual(fact_beats_mode({}),'analyst')
        with self.assertRaises(ValueError):fact_beats_mode({'factBeatsMode':'storyboard','executionMode':'classic'})
        with self.assertRaises(ValueError):fact_beats_mode({'factBeatsMode':'off'})
    def test_voice_timing_changes_reuse_identical_fact_inputs_but_visual_timing_changes(self):
        p=self.fixture();p['settings']['director']['cadencePerMinute']=12
        cid=p['characters'][0]['id'];facts=[];reviews=[];visuals=[]
        class Director:
            def analyzeFacts(self,context,gate):
                facts.append(copy.deepcopy(context))
                return {'summary':'Mira carries key','people':[],'locations':[],'beats':[],
                    'changes':[],'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):
                reviews.append(copy.deepcopy(context));return {'issues':[],'intentionalChanges':[],'objectChanges':[]}
        class Service:
            director=Director()
            def gate(self,*_):pass
            def director_calls(self,project,chid,calls,expected):
                visuals.extend(copy.deepcopy(ctx) for method,ctx in calls if method=='planStoryboard')
                return [compile_storyboard(storyboard(ctx),ctx) if method=='planStoryboard'
                        else {'majorIssues':[],'advisories':[]} for method,ctx in calls]
        ch=p['chapters'][0];group=copy.deepcopy(ch['audio']['sentences'][:4])
        for scale in (1,1.1):
            timed=[{**s,'start':s['start']*scale,'end':s['end']*scale} for s in group]
            prepare_staged(Service(),p,ch,[timed],[{'id':cid,'name':'Mira'}],
                {'characters':{cid:{'outfit':'gray coat'}},'environment':{'time':'night'}},{},'hash')
        self.assertEqual(facts[0],facts[1]);self.assertEqual(reviews[0],reviews[1])
        self.assertTrue(all(set(s)=={'index','text'} for s in facts[0]['sentences']))
        self.assertEqual(facts[0]['priorState']['environment']['time'],'night')
        self.assertNotEqual(visuals[0]['sentences'],visuals[1]['sentences'])
        self.assertEqual(visuals[0]['cadenceTarget']['imagesPerMinute'],visuals[1]['cadenceTarget']['imagesPerMinute'])
    def test_unused_cuts_cannot_buy_empty_directions_and_whitespace_is_rejected(self):
        ctx={'people':[{'id':'mira'}],'sentences':[{'text':'Mira opens the door.'}],
            'directorPayloadVersion':6,'requiredVisualChangeBoundaries':[0]}
        value=storyboard(ctx);schema=storyboard_schema(ctx)
        value['cuts']['cut0']['action']=''
        with self.assertRaisesRegex(ValueError,'short|minLength|length'):validate_schema(value,schema)
        value['cuts']['cut0']['action']='   '
        with self.assertRaisesRegex(ValueError,'empty visual action'):compile_storyboard(value,ctx)
    def test_request_packing_is_bounded_preserves_legacy_defaults_and_requires_lean(self):
        from director_staged import fact_group_limits,visual_group_limits
        self.assertEqual(fact_group_limits({}),(48,9000))
        self.assertEqual(visual_group_limits({'executionMode':'staged-review'}),(24,6000,8))
        d={'executionMode':'staged-lean','factGroupSentences':256,'visualPacking':'large'}
        self.assertEqual(fact_group_limits(d),(256,32000));self.assertEqual(visual_group_limits(d),(40,18000,20))
        for invalid in [True,0,257,'256']:
            with self.assertRaises(ValueError):fact_group_limits({**d,'factGroupSentences':invalid})
        with self.assertRaises(ValueError):fact_group_limits({**d,'executionMode':'staged-review'})
        with self.assertRaises(ValueError):visual_group_limits({**d,'executionMode':'classic'})
        with self.assertRaises(ValueError):visual_group_limits({**d,'visualPacking':'unbounded'})

    def test_larger_visual_groups_keep_every_source_cut_and_state_at_boundaries(self):
        p=self.fixture();p['settings']['director']['visualPacking']='large'
        # Twelve images/min over five-second sentences selects each source
        # sentence; the 20-shot cap splits40 sentences into safe20/20 groups.
        p['settings']['customLayout']['imagesPerMinute']=12
        ch=p['chapters'][0];cid=p['characters'][0]['id'];captured=[]
        sentences=[{'index':n,'text':f'Mira holds key {n}.','start':n*5,'end':(n+1)*5} for n in range(40)]
        class Director:
            def analyzeFacts(self,context,gate):
                return {'summary':'key carried','people':[],'locations':[],'beats':[],
                    'changes':[{'characterId':cid,'field':'outfit','value':'red coat','reason':sentences[19]['text'],'sentence':19}],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[],
                    'dialogueSpeakers':[{'sentence':19,'characterId':cid,'cueSentence':20}]}
            def checkSourceFacts(self,context,gate):return {'objectChanges':[],'issues':[],'intentionalChanges':[]}
        class Service:
            director=Director()
            def gate(self,*args):pass
            def director_calls(self,project,chid,calls,expected):
                captured.extend(copy.deepcopy(calls))
                return [compile_storyboard(storyboard(ctx),ctx) if method=='planStoryboard'
                        else {'majorIssues':[],'advisories':[]} for method,ctx in calls]
        before=copy.deepcopy(p)
        output=prepare_staged(Service(),p,ch,[sentences],[{'id':cid,'name':'Mira'}],
            {'characters':{cid:{'outfit':'gray coat'}}},{},'hash')
        shots=output[0]['storyboard']['detail']['shots']
        self.assertEqual([s['startSentence'] for s in shots],list(range(40)))
        self.assertEqual(shots[-1]['endSentence'],39)
        visuals=[ctx for method,ctx in captured if method=='planStoryboard']
        self.assertEqual([len(ctx['sentences']) for ctx in visuals],[20,20])
        self.assertIn(19,visuals[0]['requiredVisualChangeBoundaries'])
        self.assertEqual(visuals[1]['priorState']['characters'][cid]['outfit'],'red coat')
        self.assertEqual([len(ctx['shots']) for method,ctx in captured if method=='checkStoryboard'],[40])
        review=next(ctx for method,ctx in captured if method=='checkStoryboard')
        self.assertEqual(review['speakerHints'],[{'sentence':19,'characterId':cid,'cueSentence':20}])
        self.assertEqual(visuals[0]['speakerHints'],[{'sentence':19,'characterId':cid,'cue':sentences[20]['text']}])
        self.assertEqual(p,before)

    def test_compact_cuts_preserve_required_events_and_full_source_without_null_padding(self):
        ctx={'people':[{'id':'mira','name':'Mira'}],
            'sentences':[{'text':'Mira walks.'} for _ in range(134)],
            'directorPayloadVersion':6,'requiredVisualChangeBoundaries':[0,20,80,120],
            'cadenceTarget':{'approximateShots':40}}
        original=storyboard(ctx);chosen={0,20,80,120}|set(range(1,37))
        compact={'openingScene':original['openingScene'],
            'requiredCuts':{f'cut{n}':original['cuts'][f'cut{n}'] for n in (0,20,80,120)},
            'additionalCuts':[dict(original['cuts'][f'cut{n}'],startSentence=n) for n in sorted(chosen-{0,20,80,120})]}
        ctx['directorPayloadVersion']=7
        result=compile_storyboard(compact,ctx)
        shots=result['detail']['shots']
        self.assertEqual(len(shots),len(chosen));self.assertEqual(shots[0]['startSentence'],0)
        self.assertEqual(shots[-1]['endSentence'],133)
        self.assertTrue(all(a['endSentence']+1==b['startSentence'] for a,b in zip(shots,shots[1:])))
        self.assertTrue(set((0,20,80,120)).issubset({s['startSentence'] for s in shots}))
        for mutate in ('missing','duplicate','oversized','identity'):
            bad=copy.deepcopy(compact)
            if mutate=='missing':bad['requiredCuts'].pop('cut20')
            elif mutate=='duplicate':bad['additionalCuts'][1]=bad['additionalCuts'][0]
            elif mutate=='oversized':bad['additionalCuts']*=2
            else:bad['requiredCuts']['cut0']['characters']=['invented-person']
            with self.subTest(mutate=mutate),self.assertRaises(ValueError):compile_storyboard(bad,ctx)
        self.assertLess(len(json.dumps(storyboard_schema(ctx))),len(json.dumps(storyboard_schema({**ctx,'directorPayloadVersion':6}))))

    def test_compact_duplicate_recovery_keeps_every_alternative_but_never_counts_it_as_a_picture(self):
        ctx={'people':[{'id':'mira'}],'sentences':[{'text':f'Mira walks past door {n}.'} for n in range(6)],
            'directorPayloadVersion':6,'requiredVisualChangeBoundaries':[0,3],
            'cadenceTarget':{'approximateShots':4}}
        source=storyboard(ctx)
        value={'openingScene':source['openingScene'],
            'requiredCuts':{f'cut{n}':source['cuts'][f'cut{n}'] for n in (0,3)},
            'additionalCuts':[dict(source['cuts']['cut1'],startSentence=1),
                dict(source['cuts']['cut2'],startSentence=2),
                dict(source['cuts']['cut2'],startSentence=2,action='Mira peers through the doorway.') ]}
        ctx['directorPayloadVersion']=8;before=copy.deepcopy(value)
        result=compile_storyboard(value,ctx);shots=result['detail']['shots']
        self.assertEqual([s['startSentence'] for s in shots],[0,1,2,3])
        self.assertEqual(shots[-1]['endSentence'],5)
        self.assertEqual(shots[2]['alternateDirections'][0]['action'],'Mira peers through the doorway.')
        self.assertEqual(shots[2]['alternateDirections'][0]['qcStatus'],'UNREVIEWED')
        self.assertEqual(len(result['prompts']),4);self.assertEqual(value,before)
        # Different actions are retained, not counted twice or silently moved
        # onto another sentence to pretend the cadence was satisfied.
        bad=copy.deepcopy(value);bad['additionalCuts'][1]['startSentence']=1
        bad['additionalCuts'][2]['startSentence']=1
        with self.assertRaisesRegex(ValueError,'cadence'):compile_storyboard(bad,ctx)

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
        self.assertEqual(lean['planVersion'],11);self.assertEqual(lean['tier'],'default')
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
        payload['speakerHints']=[{'sentence':18,'characterId':'mira','cueSentence':19}]
        context=repair_review_context(payload,output,[4,9])
        self.assertEqual(context['speakerHints'],payload['speakerHints'])
        from director_provider import DirectorProvider
        class Reviewer(DirectorProvider):
            def call(self,role,context,schema,gate):
                self.role=role
                self.allowed=schema['properties']['majorIssues']['items']['properties']['shotIndex']['enum']
                return {'majorIssues':[{'shotIndex':9,'sentence':18,'issue':'wrong owner'}],'advisories':[]}
        reviewer=Reviewer()
        review=reviewer.checkStoryboard(context,lambda *args:None)
        self.assertEqual(reviewer.allowed,[3,4,5,8,9])
        self.assertEqual(review['majorIssues'][0]['sourceQuote'],'18')
        self.assertEqual(review['coverage']['scope'],'repair-and-neighbors')
        self.assertIn('post-repair check',reviewer.role)
        full={**payload,'shots':output['detail']['shots'],'cameras':output['cameras']}
        reviewer.checkStoryboard(full,lambda *args:None)
        self.assertIn('initial full storyboard review',reviewer.role)
        self.assertNotIn('initial review already examined',reviewer.role)
        self.assertIn('listener reacting',reviewer.role)
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
