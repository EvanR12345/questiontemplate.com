"""No-cost tests for grounded opt-in guidance and per-image receipt measurements."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from studio_data import new_project,character
from image_provider import format_prompt,reference_prompt
from director_provider import DirectorProvider
from prompt_quality import execution_evidence,generation_measurements
from studio_service import StudioService

class PromptQualityTest(unittest.TestCase):
    def fixture(self):
        p=new_project();p['settings'].update(style='full-color manhwa',focusedPrompts=True)
        man=character('Jonathan');woman=character('Rear attendant')
        man.update(gender='man',approximateAge='adult');woman.update(gender='woman',approximateAge='adult')
        man['permanentIdentity'].update(naturalHair='short black',skin='light');woman['permanentIdentity'].update(naturalHair='brown')
        p['characters']=[man,woman]
        s={'id':'fixture-shot','chapterId':p['chapters'][0]['id'],'characters':[{'id':x['id'],'type':'main','appearanceState':{'outfit':'white suit' if x is man else 'navy dress'}} for x in (man,woman)],
           'action':'Exactly three adults: Jonathan seated, one woman on his lap holding the steak plate, another woman behind holding the wine glass.',
           'pose':'Seated with the woman on his lap.','expression':'calm','camera':{'shot':'medium','angle':'eye level','composition':'rear woman behind the chair'},
           'lighting':'warm interior','location':'ship hall','imageModel':'klein','manual':{},'continuity':{}}
        provider=Mock();provider.getModelCapabilities.return_value={'promptFormat':'natural-language','supportsNegativePrompt':False}
        return p,s,provider

    def test_no_fixed_quota_all_critical_facts_and_known_genders_survive(self):
        p,s,provider=self.fixture();original=copy.deepcopy((p,s));prompt,_=format_prompt(p,s,provider)
        for fact in ('Exactly three adults','woman on his lap holding the steak plate','another woman behind holding the wine glass','gender: woman','gender: man','white suit','navy dress','short black','rear woman behind the chair'):
            self.assertIn(fact,prompt)
        self.assertEqual((p,s),original)
        s['action']+=' '+ 'Precise supplied story fact. '*250
        long_prompt,_=format_prompt(p,s,provider)
        self.assertGreater(len(long_prompt.split()),1000)
        self.assertIn(s['action'],long_prompt,'Never truncate source facts to achieve a word target')

    def test_unknown_gender_is_not_guessed_and_manual_prompts_are_unchanged(self):
        p,s,provider=self.fixture();p['characters'][1]['gender']=''
        prompt,_=format_prompt(p,s,provider);self.assertNotIn('gender: woman',prompt)
        s.update(prompt='Hand-authored prompt stays exactly as typed.',negativePrompt='my negatives',manual={'prompt':True})
        self.assertEqual(format_prompt(p,s,provider),(s['prompt'],s['negativePrompt']))
        metadata=[{'characterId':p['characters'][0]['id']}]
        actual=reference_prompt(p,s,metadata);legacy=copy.deepcopy(p);legacy['settings']['focusedPrompts']=False
        self.assertEqual(actual,reference_prompt(legacy,s,metadata))

    def test_compact_reference_roles_preserve_edit_offset_identity_and_current_appearance(self):
        p,s,_=self.fixture();metadata=[{'characterId':p['characters'][0]['id']},{'locationId':'ship'},{'referenceType':'style'}]
        compact=reference_prompt(p,s,metadata,'edit');legacy=copy.deepcopy(p);legacy['settings']['focusedPrompts']=False
        self.assertLess(len(compact.split()),len(reference_prompt(legacy,s,metadata,'edit').split()))
        for fact in ('Image 1 is the shot to edit','Image 2: Jonathan permanent face identity','white suit','image 3 for the location architecture','image 4 for visual style only'):
            self.assertIn(fact,compact)

    def test_director_guidance_is_opt_in_for_existing_local_and_cloud_adapters(self):
        director=DirectorProvider();director.call=Mock(return_value={'shots':[]})
        director.planScenes({'people':[]},lambda *a:None);original=director.call.call_args.args[0]
        director.focused_prompts=True;director.planScenes({'people':[]},lambda *a:None)
        role=director.call.call_args.args[0]
        self.assertTrue(role.startswith(original));self.assertIn('known gender of each visible person',role);self.assertIn('people count when the narration establishes it',role)
        director.writeImagePrompt({},lambda *a:None);self.assertIn('Return only a short',director.call.call_args.args[0]);self.assertIn('no fixed word quota',director.call.call_args.args[0])
        for principle in ('natural-language relationships','who holds what','where light comes from','without repeating or dropping supplied story facts','keep their meanings intact'):
            self.assertIn(principle,director.call.call_args.args[0])
        from openai_director import OpenAIDirector
        cloud=OpenAIDirector({},Path('.'));cloud.call=Mock(return_value={'prompts':[]});cloud.focused_prompts=True
        cloud.writeImagePrompt({},lambda *a:None);self.assertIn('Unknown facts stay unknown',cloud.call.call_args.args[0])

    def test_actual_server_cache_and_time_receipts_are_separate_from_unknown_tokens_and_rental_overhead(self):
        graph={'1':{'class_type':'CLIPTextEncode'},'2':{'class_type':'SamplerCustomAdvanced'},'3':{'class_type':'VAEDecode'}}
        receipt={'status':{'messages':[['execution_start',{'timestamp':1000}],['execution_cached',{'nodes':['1']}],['execution_success',{'timestamp':3500}]]}}
        evidence=execution_evidence(receipt,graph)
        self.assertEqual(evidence['serverExecutionSeconds'],2.5);self.assertTrue(evidence['actualTextEncoderCacheHit']);self.assertFalse(evidence['actualSamplerOrDecodeCacheHit'])
        p=new_project();p['settings']['cloudWindow']={'gpuHourlyUSD':1.118}
        result={'provider':'comfyui','providerTimings':{'totalClientSeconds':4},'serverExecutionEvidence':evidence}
        m=generation_measurements(p,'Image1 reference: a woman holding wine.',result)
        self.assertEqual(m['promptWords'],6);self.assertAlmostEqual(m['estimatedImageIntervalRentalUSD'],4*1.118/3600)
        self.assertFalse(m['sharedOverheadIncluded']);self.assertIsNone(m['actualIndividualInvoiceUSD']);self.assertIsNone(m['rawTokenizerTokens'])
        unknown=execution_evidence({},graph);self.assertIsNone(unknown['serverExecutionSeconds']);self.assertIsNone(unknown['actualTextEncoderCacheHit'])
        result['providerTimings']['totalClientSeconds']=float('nan');self.assertIsNone(generation_measurements(p,'x',result)['estimatedImageIntervalRentalUSD'])
        result['provider']='existing';self.assertIsNone(generation_measurements(p,'x',result)['recordedRentalHourlyUSD'])

    def test_short_visual_supplements_can_be_selected_without_changing_scene_fact_guidance(self):
        director=DirectorProvider();director.call=Mock(return_value={'shots':[]})
        director.planScenes({'people':[]},lambda *a:None);original_scene_role=director.call.call_args.args[0]
        director.concise_prompt_supplement=True
        director.planScenes({'people':[]},lambda *a:None)
        self.assertEqual(director.call.call_args.args[0],original_scene_role)
        director.writeImagePrompt({},lambda *a:None)
        self.assertIn('Return only a short',director.call.call_args.args[0])
        self.assertIn('grounded draft is retained',director.call.call_args.args[0])

if __name__=='__main__':unittest.main()
