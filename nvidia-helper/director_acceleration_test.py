import copy
import io
import json
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from director_wire import CompactDirectorWire
from director_storyboard import storyboard_schema, compile_storyboard
from director_provider import validate_schema, obj, arr, STR
from director_staged import prepare_staged,apply_source_changes
from openai_director import OpenAIDirector,final_response_text
from director_analysis_test import ChapterDirectorAnalysisTest, PlanningService, PlanningProvider
from studio_overlap import CloudStoryOverlap
from studio_data import apply_changes


def storyboard(context):
    cid=context['people'][0]['id']
    return {'shots':[{'newScene':True,'purpose':'follow the source','location':'school hallway','mood':'calm',
        'pacingReason':'one visible moment','startSentence':i,'characters':[cid],
        'action':s['text'],'expression':'calm','pose':'walking','lighting':'daylight',
        'motion':'slow zoom in','transition':'cut',
        'camera':{'shot':'medium wide','angle':'eye level','composition':'Mira foreground right'}} for i,s in enumerate(context['sentences'])]}


class CompactWireTest(unittest.TestCase):
    def test_final_response_does_not_concatenate_analysis_or_preview_slots(self):
        completed={'output':[{'type':'message','role':'assistant','channel':'analysis','content':[{'type':'output_text','text':'{"wrong":1}'}]},
            {'type':'message','role':'assistant','channel':'final','content':[{'type':'output_text','text':'{"right":2}'}]}]}
        self.assertEqual(final_response_text(completed,['{"wrong":1}','{"right":2}']),'{'+'"right":2}')
        completed['output'][0]['channel']='final'
        with self.assertRaisesRegex(ValueError,'conflicting'):final_response_text(completed,[])

    def test_roundtrip_nested_ids_keeps_narrative_and_full_project_keys(self):
        context={'people':[{'id':'person-long-id','name':'Mira'}],
                 'state':{'person-long-id':{'outfit':'red coat'}},'text':'person-long-id holds the key.'}
        schema=obj({'shots':arr(obj({'characterId':{'type':'string','enum':['person-long-id']},'action':STR}))})
        codec=CompactDirectorWire(context,schema)
        self.assertEqual(codec.context['text'],context['text'])
        self.assertEqual(codec.context['people'][0]['name'],'Mira')
        value={codec.keys['shots']:[{codec.keys['characterId']:codec.ids['person-long-id'],codec.keys['action']:'Mira holds the key.'}]}
        validate_schema(value,codec.schema)
        self.assertEqual(codec.decode(value),{'shots':[{'characterId':'person-long-id','action':'Mira holds the key.'}]})
        self.assertEqual(context['people'][0]['id'],'person-long-id')

    def test_source_alias_collision_is_avoided(self):
        codec=CompactDirectorWire({'id':'abc','text':'P0'},obj({'id':STR,'text':STR}))
        self.assertNotEqual(codec.ids['abc'],'P0')
        self.assertEqual(codec.decode({codec.keys['id']:codec.ids['abc'],codec.keys['text']:'P0'}),{'id':'abc','text':'P0'})

    def test_composition_ids_become_names_and_literal_source_tokens_are_protected(self):
        context={'people':[{'id':'long-identity','name':'Mira'}],'text':'Room P0 is quiet.'}
        codec=CompactDirectorWire(context,obj({'characters':arr({'type':'string','enum':['long-identity']}),'composition':STR}))
        alias=codec.ids['long-identity']
        self.assertNotEqual(alias,'P0')
        decoded=codec.decode({codec.keys['characters']:[alias],codec.keys['composition']:alias+' foreground left; room P0 behind.'})
        self.assertEqual(decoded['characters'],['long-identity'])
        self.assertEqual(decoded['composition'],'Mira foreground left; room P0 behind.')

    def test_unknown_compact_key_rejected(self):
        codec=CompactDirectorWire({},obj({'text':STR}))
        with self.assertRaises(ValueError):codec.decode({'evil':'text'})

    def test_api_adapter_decodes_before_validated_cache_and_counts_actual_usage(self):
        with tempfile.TemporaryDirectory() as root:
            provider=OpenAIDirector({'openaiCompactWire':True},root)
            context={'people':[{'id':'person-long-id'}]};schema=obj({'characterId':STR,'action':STR})
            codec=CompactDirectorWire(context,schema);seen=[];timings=[]
            def response(req,**kwargs):
                body=json.loads(req.data);seen.append(body)
                value={codec.keys['characterId']:codec.ids['person-long-id'],codec.keys['action']:'holds the key'}
                events=[{'type':'response.created','response':{'id':'r1'}},
                    {'type':'response.output_text.delta','delta':json.dumps(value)},
                    {'type':'response.completed','response':{'id':'r1','status':'completed',
                        'usage':{'input_tokens':100,'output_tokens':30,'output_tokens_details':{'reasoning_tokens':5}}}}]
                return io.BytesIO(b''.join(b'data: '+json.dumps(e).encode()+b'\n\n' for e in events))
            provider.timing_callback=lambda *args:timings.append(args)
            with patch.object(provider,'key',return_value='synthetic-key'),patch('openai_director.urllib.request.urlopen',side_effect=response):
                value=provider.call('Test director',context,schema,lambda *a:None)
                cached=provider.call('Test director',context,schema,lambda *a:None)
            self.assertEqual(value,{'characterId':'person-long-id','action':'holds the key'})
            self.assertEqual(value,cached);self.assertEqual(len(seen),1)
            self.assertEqual(timings[0][2]['tokens'],30);self.assertTrue(timings[1][2]['reused'])


class StoryboardTest(unittest.TestCase):
    def test_injury_deltas_accumulate_without_claiming_one_quote_supports_all_wounds(self):
        changes=[{'characterId':'mira','type':'injury','to':{'injury':'cut on left cheek'},'reason':'Mira gets a cut on her left cheek.'},
            {'characterId':'mira','type':'injury','to':{'injury':'cut on right arm'},'reason':'Mira gets a cut on her right arm.'}]
        result=apply_source_changes({'characters':{'mira':{}}},changes,1,'scene')
        self.assertEqual(result['characters']['mira']['injury'],'cut on left cheek; cut on right arm')
        self.assertEqual(result['appearanceHistory'][0]['to'],{'injury':'cut on left cheek'})
        self.assertEqual(result['appearanceHistory'][1]['to'],{'injury':'cut on right arm'})
        cleared=apply_source_changes(result,[{'characterId':'mira','type':'injury','to':{'injury':'fully healed'},
            'reason':'All of Mira’s wounds fully healed.'}],2,'heal')
        self.assertEqual(cleared['characters']['mira']['injury'],'fully healed')

    def context(self):
        return {'people':[{'id':'mira','name':'Mira'}],
                'sentences':[{'text':'Mira walks.','index':0},{'text':'Mira takes the key.','index':1}],
                'requiredVisualChangeBoundaries':[1]}

    def test_restores_exact_coverage_camera_and_direction_without_inventing_cuts(self):
        context=self.context();value=storyboard(context);validate_schema(value,storyboard_schema(context))
        result=compile_storyboard(value,context)
        self.assertEqual([(s['startSentence'],s['endSentence']) for s in result['detail']['shots']],[(0,0),(1,1)])
        self.assertEqual(len(result['cameras']),2);self.assertEqual(len(result['prompts']),2)

    def test_omitted_change_and_duplicate_cut_rejected(self):
        context=self.context();value=storyboard(context)
        value['shots']=value['shots'][:1]
        with self.assertRaisesRegex(ValueError,'required'):compile_storyboard(value,context)
        value=storyboard(context);value['shots'][1]['startSentence']=0
        with self.assertRaisesRegex(ValueError,'required'):compile_storyboard(value,context)

    def test_unsorted_duplicate_directions_retain_alternatives_without_new_cuts(self):
        context=self.context();context['requiredVisualChangeBoundaries']=[];value=storyboard(context)
        value['shots'].reverse();duplicate=copy.deepcopy(value['shots'][1]);duplicate['action']='Alternate view of the same source moment.'
        value['shots'].append(duplicate)
        result=compile_storyboard(value,context);shots=result['detail']['shots']
        self.assertEqual([s['startSentence'] for s in shots],[0,1])
        self.assertEqual(len(shots[0]['alternateDirections']),1)
        self.assertEqual(shots[0]['alternateDirections'][0]['action'],'Alternate view of the same source moment.')

    def test_frozen_fact_snapshot_carries_object_before_visual_groups_execute(self):
        p=ChapterDirectorAnalysisTest().fixture();chapter=p['chapters'][0];cid=p['characters'][0]['id']
        groups=[[chapter['audio']['sentences'][0]],[chapter['audio']['sentences'][1]]]
        captured=[];visual_context=[]
        class Director:
            def analyzeFacts(self,context,gate):
                captured.append(copy.deepcopy(context));n=len(captured)-1
                return {'summary':'next','people':[],'locations':[],'beats':[],
                    'changes':[{'characterId':cid,'field':'key','value':'right hand','reason':context['sentences'][0]['text'],'sentence':0}] if n==0 else [],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):return {'objectChanges':[],'issues':[],'intentionalChanges':[]}
        class Service:
            director=Director()
            def gate(self,*args):pass
            def director_calls(self,project,ch,calls,expected):
                visual_context.extend(copy.deepcopy(calls))
                return [compile_storyboard(storyboard(payload),payload) for _,payload in calls]
        p['settings']['director']['executionMode']='staged'
        result=prepare_staged(Service(),p,chapter,groups,[{'id':cid,'name':'Mira'}],{'characters':{cid:{}}},{},'hash')
        self.assertEqual(captured[1]['priorState']['characters'][cid]['key'],'right hand')
        self.assertEqual(visual_context[1][1]['priorState']['characters'][cid]['key'],'right hand')
        self.assertEqual(len(result),2)

    def test_two_chapter_actual_pipeline_preserves_timeline_and_clothing_handoff(self):
        initial=ChapterDirectorAnalysisTest().fixture();seen=[]
        initial['settings']['customLayout']['imagesPerMinute']=12
        def reply(provider,role,context,schema,gate,vision=False):
            gate('synthetic stage');seen.append(role)
            if role.startswith('Casting supervisor'):return {'people':[]}
            if role.startswith('Source fact analyst'):
                cid=context['chapterCast'][0]['id'];sentence=context['sentences'][0]['text']
                field,value=('outfit','red coat') if 'red coat' in sentence else ('key','right hand')
                return {'summary':'Mira carries the key.','people':[],
                    'locations':[{'name':'school hallway','description':'school hallway'}],
                    'beats':[],'changes':[{'characterId':cid,'field':field,'value':value,'reason':sentence,'sentence':0}],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            if role.startswith('Source continuity reviewer'):return {'majorIssues':[],'objectChanges':[]}
            if role.startswith('Visual storyboard director:'):return storyboard(context)
            if role.startswith('Storyboard continuity supervisor'):return {'majorIssues':[],'advisories':[]}
            if role.startswith('Workflow planner'):return {'provider':'comfyui','model':'klein','workflow':'klein','referenceStrategy':'identity','reason':'configured'}
            raise AssertionError('Unexpected director role '+role[:30])
        with tempfile.TemporaryDirectory() as root:
            service=PlanningService(root,None,lambda:None,threading.RLock(),lambda:None,lambda:None,
                type('Q',(),{'snapshot':lambda self:{'current':None,'counts':{}}})())
            service.providers['comfyui']=PlanningProvider()
            initial['settings']['director'].update(parallelism=8,executionMode='staged-review')
            initial['settings']['autoContinue']=True
            initial=service.store.save(initial)
            try:
                with patch.object(OpenAIDirector,'_call',reply),patch.object(service,'enqueue') as image_queue:
                    for ch in initial['chapters']:
                        service.analyze_chapter(service.store.load(initial['id']),ch['id'],{'offlinePlanning':True,'managedPipeline':True})
                    image_queue.assert_not_called()
                saved=service.store.load(initial['id']);cid=saved['characters'][0]['id']
                self.assertEqual(saved['chapters'][1]['inputState']['characters'][cid]['key'],'right hand')
                self.assertEqual(saved['chapters'][1]['handoff']['state']['characters'][cid]['outfit'],'red coat')
                for ch in saved['chapters']:
                    shots=[s for scene in ch['scenes'] for s in scene['shots']]
                    self.assertEqual(len(shots),14);self.assertEqual(shots[-1]['end'],ch['audio']['duration'])
                    self.assertTrue(all(s['directorPrompt'] and s['prompt'] and s['camera'] for s in shots))
                self.assertTrue(any(role.startswith('Storyboard continuity supervisor') for role in seen))
                self.assertFalse(any(role.startswith('Image prompt engineer') for role in seen))
            finally:service.close()

    def test_cadence_bounds_and_continuing_scene_schema_roundtrip(self):
        context=self.context();context['cadenceTarget']={'approximateShots':2}
        schema=storyboard_schema(context);value=storyboard(context)
        value['shots'][1].update(newScene=False,purpose='',location='',mood='',pacingReason='')
        validate_schema(value,schema)
        codec=CompactDirectorWire(context,schema)
        encoded=codec.encode_values(value)
        # encode_values preserves context keys; response keys are compacted.
        def response(node):
            if isinstance(node,dict):return {codec.keys.get(k,k):response(v) for k,v in node.items()}
            if isinstance(node,list):return [response(v) for v in node]
            return node
        decoded=codec.decode(response(encoded));self.assertEqual(decoded,value)
        planned=compile_storyboard(decoded,context)
        self.assertEqual(len(planned['plan']['scenes']),1)
        with self.assertRaises(ValueError):validate_schema({'shots':value['shots'][:1]},schema)

    def test_visual_slices_bound_output_without_reducing_cadence(self):
        p=ChapterDirectorAnalysisTest().fixture();ch=p['chapters'][0];cid=p['characters'][0]['id']
        p['settings']['director'].update(executionMode='staged',cadencePerMinute=12)
        contexts=[]
        class Director:
            def analyzeFacts(self,context,gate):
                return {'summary':'Mira holds her key.','people':[],'locations':[],'beats':[],'changes':[],
                    'environmentChanges':[],'objects':['key'],'goals':[],'unresolved':[]}
            def checkSourceFacts(self,context,gate):return {'objectChanges':[],'issues':[],'intentionalChanges':[]}
        class Service:
            director=Director()
            def gate(self,*args):pass
            def director_calls(self,p,ch,calls,expected):
                contexts.extend(payload for _,payload in calls)
                return [compile_storyboard(storyboard(payload),payload) for _,payload in calls]
        results=prepare_staged(Service(),p,ch,[ch['audio']['sentences']],[{'id':cid,'name':'Mira'}],{}, {},'hash')
        self.assertGreater(len(contexts),1)
        self.assertTrue(all(c['cadenceTarget']['approximateShots']<=8 for c in contexts))
        self.assertEqual(len(results[0]['storyboard']['detail']['shots']),14)
        self.assertEqual(results[0]['storyboard']['detail']['shots'][-1]['endSentence'],13)


if __name__=='__main__':unittest.main()
