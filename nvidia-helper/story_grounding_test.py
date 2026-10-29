import copy
import unittest
from unittest.mock import Mock
from story_grounding import (owner_conflicts,require_owners,casting_gaps,
    set_environment_location,apply_environment_event,visual_environment)
from director_provider import DirectorProvider
from image_provider import format_prompt,reference_prompt,visible_appearance
from studio_data import new_project,character


class StoryGroundingTest(unittest.TestCase):
    def setUp(self):
        self.people=[{'id':'hero','name':'Rowan','description':'A black-haired man'},
                     {'id':'attendant','name':'Woman holding plate','type':'temporary','description':'A woman on a chair','evidence':'The woman holds a plate.'},
                     {'id':'rear','name':'Woman with wine','type':'temporary','description':'The other woman behind the chair','evidence':'Another woman holds a glass.'}]

    def event(self,quote,cid='hero',field='plate',value='held'):
        return {'characterId':cid,'field':field,'value':value,'sentence':0,'reason':quote}

    def test_explicit_role_owner_does_not_leak_to_neighboring_main_character(self):
        for quote in ("In her hands, the woman was holding a plate.",
                      "The plate was in the woman's hands.",
                      'The glass was in the hand of the woman standing behind the chair.'):
            event=self.event(quote)
            self.assertEqual(owner_conflicts([event],self.people)[0]['changeIndex'],0)
            with self.assertRaisesRegex(ValueError,'ownership'):require_owners([event],self.people)
            event['characterId']='attendant';self.assertEqual(owner_conflicts([event],self.people),[])
        self.assertTrue(owner_conflicts([self.event('The other woman standing behind the chair had a glass of red wine in her hand.',field='glass',value='held')],self.people))

    def test_direct_named_holder_requires_same_character(self):
        people=self.people+[{'id':'mira','name':'Mira','description':'A traveler'}]
        event=self.event('Mira holds the key.','hero','key')
        self.assertTrue(owner_conflicts([event],people))
        event['characterId']='mira';self.assertEqual(owner_conflicts([event],people),[])

    def test_shared_generic_aliases_do_not_turn_correct_female_holders_into_each_other(self):
        people=[{'id':'plate','name':'Seated attendant','aliases':['woman'],'description':'A seated woman'},
                {'id':'glass','name':'Standing attendant','aliases':['woman'],'description':'A standing woman'}]
        events=[self.event('The woman was holding a plate.','plate','plate'),
                self.event('The woman held a glass.','glass','glass')]
        original=copy.deepcopy((events,people))
        self.assertEqual(owner_conflicts(events,people),[])
        require_owners(events,people)
        self.assertEqual((events,people),original)
        self.assertEqual([e['characterId'] for e in events],['plate','glass'])

    def test_shared_normalized_names_and_aliases_are_not_unique_owner_proof(self):
        people=[{'id':'first','name':'First performer','aliases':['Ｃｏｕｒｔ　Ｓｉｎｇｅｒ']},
                {'id':'second','name':'Second performer','aliases':['Court Singer']}]
        event=self.event('Court Singer holds a bell.','first','bell')
        self.assertEqual(owner_conflicts([event],people),[])
        people=[{'id':'first','name':'Alex','aliases':[]},{'id':'second','name':'ALEX','aliases':[]}]
        self.assertEqual(owner_conflicts([self.event('Alex holds a bell.','first','bell')],people),[])

    def test_bare_role_is_not_a_named_identity_even_with_only_one_matching_alias(self):
        for label in ('woman','the woman',' A WOMAN '):
            people=[{'id':'first','name':'First attendant','aliases':[label],'description':'A woman'},
                    {'id':'second','name':'Second attendant','aliases':[],'description':'A woman'},
                    {'id':'male','name':'Male attendant','aliases':[],'description':'A man'}]
            quote='The woman holds a plate.'
            self.assertEqual(owner_conflicts([self.event(quote,'second')],people),[])
            with self.assertRaisesRegex(ValueError,'ownership'):require_owners([self.event(quote,'male')],people)

    def test_unique_name_alias_and_distinguishing_role_still_reject_wrong_holder(self):
        people=[{'id':'first','name':'Mira','aliases':['Quartermaster','attendant in blue'],'description':'A woman'},
                {'id':'second','name':'Nora','aliases':['woman'],'description':'A woman'}]
        for label in ('Mira','Quartermaster','attendant in blue'):
            event=self.event(label+' holds a key.','second','key')
            self.assertTrue(owner_conflicts([event],people))
            with self.assertRaisesRegex(ValueError,'ownership'):require_owners([event],people)
            event['characterId']='first'
            self.assertEqual(owner_conflicts([event],people),[])
        # Repeated cast records for the same accepted ID do not make its unique
        # alias ambiguous; the index counts distinct people rather than rows.
        people.append(copy.deepcopy(people[0]))
        self.assertTrue(owner_conflicts([self.event('Quartermaster holds a key.','second','key')],people))

    def test_ambiguous_or_unknown_labels_never_assign_or_invent_an_owner(self):
        people=[{'id':'first','name':'First attendant','aliases':['woman'],'description':'A woman'},
                {'id':'second','name':'Second attendant','aliases':['woman'],'description':'A woman'}]
        for chosen in ('first','second','unknown'):
            event=self.event('A woman held the key in her hand.',chosen,'key')
            original=copy.deepcopy(event)
            self.assertEqual(owner_conflicts([event],people),[])
            self.assertEqual(event,original)

    def test_correct_shared_alias_source_review_does_not_buy_a_spurious_correction(self):
        people=[{'id':'plate','name':'Seated attendant','aliases':['woman'],'description':'A woman'},
                {'id':'glass','name':'Standing attendant','aliases':['woman'],'description':'A woman'}]
        event=self.event('The woman was holding a plate.','plate')
        context={'people':people,'objects':['plate'],'changes':[event],
                 'sentences':[{'text':event['reason']}],'knownState':{}}
        original=copy.deepcopy(context)
        provider=DirectorProvider();provider.call=Mock(return_value={'majorIssues':[],'objectChanges':[]})
        result=provider.checkSourceFacts(context,lambda *_:None)
        self.assertEqual(provider.call.call_count,1)
        self.assertNotIn('sourceRepair',result)
        self.assertEqual(context,original)

    def test_pronouns_transfers_separate_objects_and_injuries_are_not_guessed(self):
        people=self.people+[{'id':'mira','name':'Mira'}]
        events=[self.event('He held it in his hand.'),
                self.event('Mira handed Rowan the key.','hero','key'),
                self.event('Mira holds a key while Rowan holds a plate.'),
                self.event('The woman was holding a knife when Rowan was injured.','hero','injury','cut shoulder')]
        self.assertEqual(owner_conflicts(events,people),[])

    def test_bounded_casting_repair_creates_temporary_roles_only_from_exact_evidence(self):
        text='The woman was holding a plate on his lap. The other woman behind the chair had a glass of wine.'
        provider=DirectorProvider()
        detected=[dict(self.people[1],evidence='The woman was holding a plate',aliases=[]),
                  dict(self.people[2],evidence='The other woman behind the chair',aliases=[])]
        provider.call=Mock(side_effect=[{'people':[]},{'people':detected}])
        result=provider.resolvePeople({'chapterText':text,'knownPeople':self.people[:1]},lambda *_:None)
        self.assertEqual(result['people'],detected);self.assertEqual(provider.call.call_count,2)
        self.assertIn('coverageEvidence',provider.call.call_args.args[1])
        self.assertTrue(all(p['type']=='temporary' for p in result['people']))

    def test_casting_coverage_does_not_group_two_foreground_holders_or_repeat_requests(self):
        text='The woman holds a plate. Another woman holds a glass.'
        self.assertTrue(casting_gaps(text,[self.people[1]]))
        self.assertEqual(casting_gaps(text,self.people),[])
        provider=DirectorProvider();provider.call=Mock(return_value={'people':self.people[1:]})
        provider.resolvePeople({'chapterText':text,'knownPeople':self.people[:1]},lambda *_:None)
        self.assertEqual(provider.call.call_count,1)
        self.assertEqual(casting_gaps('A crowd of women watches the parade.',[]),[])
        absent_main={'id':'mother','name':'Evelyn','description':'A woman with green eyes','type':'main'}
        self.assertTrue(casting_gaps(text,[absent_main,self.people[1]]))

    def test_casting_failed_repair_is_bounded_and_never_invents_from_regex(self):
        provider=DirectorProvider();provider.call=Mock(return_value={'people':[]})
        with self.assertRaisesRegex(ValueError,'after one correction'):
            provider.resolvePeople({'chapterText':'The woman holds a plate.','knownPeople':[]},lambda *_:None)
        self.assertEqual(provider.call.call_count,2)

    def test_casting_unquoted_repair_is_rejected(self):
        provider=DirectorProvider();provider.call=Mock(side_effect=[{'people':[]},
            {'people':[dict(self.people[1],evidence='Invented event.',aliases=[])]}])
        with self.assertRaisesRegex(ValueError,'exact source evidence'):
            provider.resolvePeople({'chapterText':'The woman holds a plate.','knownPeople':[]},lambda *_:None)

    def test_source_reviewer_repairs_explicit_wrong_owner_even_if_model_does_not_flag_it(self):
        event=self.event('The woman was holding a plate.')
        provider=DirectorProvider();provider.call=Mock(side_effect=[
            {'majorIssues':[],'objectChanges':[]},
            {'corrections':[{'changeIndex':0,'keep':True,'characterId':'attendant','field':'plate','value':'held','sentence':0}]},
            {'majorIssues':[],'objectChanges':[]}])
        context={'people':self.people,'objects':['plate'],'changes':[event],
                 'sentences':[{'text':event['reason']}],'knownState':{}}
        original=copy.deepcopy(context)
        result=provider.checkSourceFacts(context,lambda *_:None)
        self.assertEqual(result['correctedChanges'][0]['characterId'],'attendant')
        self.assertEqual(result['correctedChanges'][0]['reason'],event['reason'])
        self.assertEqual(context,original);self.assertEqual(provider.call.call_count,3)

    def test_source_reviewer_cannot_publish_a_still_wrong_owner(self):
        event=self.event('The woman was holding a plate.')
        provider=DirectorProvider();provider.call=Mock(side_effect=[
            {'majorIssues':[],'objectChanges':[]},
            {'corrections':[{'changeIndex':0,'keep':True,'characterId':'hero','field':'plate','value':'held','sentence':0}]},
            {'majorIssues':[],'objectChanges':[]}])
        with self.assertRaisesRegex(ValueError,'ownership'):
            provider.checkSourceFacts({'people':self.people,'objects':['plate'],'changes':[event],
                'sentences':[{'text':event['reason']}],'knownState':{}},lambda *_:None)

    def test_environment_scope_preserves_history_and_clears_old_room(self):
        state={'environment':{'location':'party room','locationId':'party','weather':'rain','time':'night','pool of blood':'under victim','broken table':'corner'}}
        apply_environment_event(state,{'field':'pool of blood','value':'under victim','reason':'Blood pools.','sentence':0},1,'a')
        set_environment_location(state,'upper room','upper')
        self.assertNotIn('pool of blood',state['environment']);self.assertNotIn('broken table',state['environment'])
        self.assertEqual(state['environment']['weather'],'rain');self.assertEqual(state['environment']['time'],'night')
        self.assertEqual(state['environmentHistory'][0]['locationId'],'party')
        self.assertEqual(state['environmentScopes']['party']['pool of blood'],'under victim')
        set_environment_location(state,'party room','party')
        self.assertEqual(state['environment']['pool of blood'],'under victim')

    def test_name_only_scope_can_gain_id_without_erasing_current_room(self):
        state={'environment':{'location':'Party Room','pool of blood':'under victim'}}
        set_environment_location(state,'party room','party')
        self.assertEqual(state['environment']['pool of blood'],'under victim')
        self.assertEqual(state['environment']['locationId'],'party')

    def test_location_event_scopes_before_next_fact_group(self):
        state={'environment':{'location':'party room','pool of blood':'under victim'}}
        apply_environment_event(state,{'field':'location','value':'upper room','reason':'They enter the upper room.','sentence':1},2,'b')
        self.assertNotIn('pool of blood',state['environment'])
        self.assertEqual(state['environmentHistory'][0]['location'],'upper room')

    def test_legacy_snapshot_history_is_not_a_visual_instruction(self):
        shot={'location':'upper room','action':'Two men talk.','continuity':{'location':'upper room','pool of blood':'old room','time':'night'}}
        original=copy.deepcopy(shot)
        self.assertEqual(visual_environment(shot),{'time':'night'})
        shot.update(action='Show the pool of blood beside the fallen man.')
        self.assertIn('pool of blood',visual_environment(shot))
        self.assertEqual(original['continuity'],shot['continuity'])

    def test_invisible_inventory_props_are_filtered_but_clothing_injuries_and_accessories_stay(self):
        shot={'action':'Rowan looks toward the door.','camera':{'composition':'Close-up on Rowan'}}
        selected={'appearanceState':{'plate of steak':'held in hands','glass of wine':'held','outfit':'red coat',
                                    'injury':'cut cheek','restraint':'hands tied','watch':'left wrist','key':'right hand'}}
        original=copy.deepcopy(selected);visible=visible_appearance(shot,selected)
        for key in ('plate of steak','glass of wine','key'):self.assertNotIn(key,visible)
        for key in ('outfit','injury','restraint','watch'):self.assertEqual(visible[key],selected['appearanceState'][key])
        shot['action']='Rowan holds the key.';self.assertEqual(visible_appearance(shot,selected)['key'],'right hand')
        self.assertEqual(selected,original)

    def test_compiler_keeps_current_facts_without_redundant_reference_state_or_legacy_pool(self):
        p=new_project();person=character('Rowan');person['permanentIdentity']['naturalHair']='black'
        p['characters']=[person]
        shot={'chapterId':p['chapters'][0]['id'],'characters':[{'id':person['id'],'type':'main','appearanceState':{'outfit':'red coat','key':'right hand'}}],
              'camera':{'shot':'medium','angle':'eye level','composition':'Rowan near doorway'},'location':'upper room',
              'action':'Rowan holds the key.','pose':'standing','lighting':'warm lamp','expression':'calm',
              'continuity':{'location':'upper room','pool of blood':'old room','time':'night'}}
        provider=type('Provider',(),{'getCapabilities':lambda _: {'supportsNegativePrompt':False,'promptFormat':'natural-language'}})()
        prompt,_=format_prompt(p,shot,provider);shot['prompt']=prompt
        ref=reference_prompt(p,shot,[{'characterId':person['id']}])
        for fact in ('black','red coat','right hand','Rowan holds the key','upper room','night'):self.assertIn(fact,prompt)
        self.assertNotIn('pool of blood',prompt);self.assertNotIn('red coat',ref)
        self.assertIn('identity reference for Rowan',ref)
        shot.update(manual={'prompt':True},prompt='My exact manually edited prompt.',negativePrompt='Mine')
        self.assertEqual(format_prompt(p,shot,provider),(shot['prompt'],shot['negativePrompt']))

    def test_old_known_bad_owner_plan_blocks_new_image_purchase_until_corrected(self):
        from prompt_preflight import require_shot
        p=new_project();ch=p['chapters'][0];p['characters']=[];ch['people']=copy.deepcopy(self.people)
        ch['analysis']={'groups':[{'changes':[self.event('The woman was holding a plate.')]}]}
        shot={'chapterId':ch['id'],'prompt':'A drawn scene.','start':0,'end':2,'characters':[]}
        with self.assertRaisesRegex(ValueError,'wrong source person'):require_shot(p,shot)
        shot['imagePath']='saved.png';self.assertTrue(require_shot(p,shot)['passed'])
        with self.assertRaisesRegex(ValueError,'wrong source person'):require_shot(p,shot,new_image=True)
        # An explicit authored prompt is a user override, not an AI rewrite.
        shot['manual']={'prompt':True};self.assertTrue(require_shot(p,shot)['passed'])
        shot.pop('manual');ch['analysis']['groups'][0]['changes'][0]['characterId']='attendant'
        self.assertTrue(require_shot(p,shot)['passed'])

    def test_storyboard_compactness_never_erases_actor_target_weapon_relationships(self):
        from director_acceleration_test import storyboard
        from director_storyboard import plan_storyboard
        sentence='A black-suited attacker holds the knife and grazes Rowan\'s shoulder while Rowan twists away to defend himself.'
        context={'people':self.people[:1],'sentences':[{'index':0,'text':sentence,'start':0,'end':5}]}
        provider=DirectorProvider();provider.call=Mock(return_value=storyboard(context))
        result=plan_storyboard(provider,context,lambda *_:None)
        self.assertEqual(result['detail']['shots'][0]['action'],sentence)
        role=provider.call.call_args.args[0]
        self.assertIn('suggestions, not limits',role)
        self.assertIn('WHO acts on WHOM',role)
        self.assertIn('does not mean the target holds the knife',role)


if __name__=='__main__':unittest.main()
