import copy
import unittest
from storyboard_review import bound_context,restore_findings,validate_findings
from director_provider import DirectorProvider
from openai_director import OpenAIDirector


class BoundReviewTest(unittest.TestCase):
    def fixture(self):
        return {'sentences':[{'index':n,'text':f'source {n}'} for n in range(5)],
                'priorState':{'watch':'left wrist'},
                'shots':[{'startSentence':0,'endSentence':1,'action':'listens'},
                         {'startSentence':2,'endSentence':4,'action':'speaks'}]}

    def test_keys_and_full_source_survive_sparse_review(self):
        c=self.fixture();original=copy.deepcopy(c);full,mapping=bound_context(c)
        self.assertEqual(list(mapping),['s0-e1','s2-e4'])
        self.assertEqual(full['shots'][1]['ownNarration'][0],{'index':2,'text':'source 2'})
        sparse={**c,'reviewShotIndices':[1],'shots':[{**c['shots'][1],'shotIndex':1}]}
        checked,sm=bound_context(sparse)
        self.assertEqual(list(sm),['s2-e4']);self.assertEqual(checked['sentences'],c['sentences'])
        self.assertEqual(c,original)

    def test_cross_shot_citation_cannot_trigger_wrong_repair_or_cache(self):
        checked,_=bound_context(self.fixture())
        bad={'majorIssues':[{'shotKey':'s0-e1','sentence':3,'issue':'wrong speaker'}],'advisories':[]}
        with self.assertRaisesRegex(ValueError,'outside'):validate_findings(checked,bad)
        from director_provider import obj,arr,short_text
        schema=obj({'majorIssues':arr(obj({'shotKey':short_text(50),'sentence':{'type':'integer'},'issue':short_text(300)})),
                    'advisories':arr(short_text(300))})
        with self.assertRaisesRegex(ValueError,'outside'):
            OpenAIDirector.validate_result('Storyboard continuity supervisor:',checked,bad,schema)

    def test_findings_restore_global_index_and_exact_quote_without_mutating_receipt(self):
        c=self.fixture();c.update(reviewShotIndices=[9],shots=[{**c['shots'][1],'shotIndex':9}])
        checked,_=bound_context(c)
        raw={'majorIssues':[{'shotKey':'s2-e4','sentence':3,'issue':'wrong speaker'}],'advisories':[]}
        result=restore_findings(checked,raw)
        self.assertEqual(result['majorIssues'][0]['shotIndex'],9)
        self.assertEqual(result['majorIssues'][0]['sourceQuote'],'source 3')
        self.assertNotIn('shotIndex',raw['majorIssues'][0])
        self.assertEqual(result['coverage']['shotIndices'],[9])

    def test_review_request_uses_source_key_and_bound_quote(self):
        class Capture(DirectorProvider):
            def call(self,role,context,schema,gate):
                self.seen=context;self.role=role
                return {'majorIssues':[{'shotKey':'s2-e4','sentence':3,'issue':'wrong speaker'}],'advisories':[]}
        p=Capture();r=p.checkStoryboard(self.fixture(),lambda *_:None)
        self.assertIn('ownNarration',p.seen['shots'][0]);self.assertIn('Never count array positions',p.role)
        self.assertEqual(r['majorIssues'][0]['shotIndex'],1)

    def test_duplicate_or_invalid_cuts_fail_before_provider(self):
        c=self.fixture();c['shots'].append(copy.deepcopy(c['shots'][0]))
        with self.assertRaisesRegex(ValueError,'duplicate'):bound_context(c)
        c=self.fixture();c['shots'][1]['endSentence']=5
        with self.assertRaisesRegex(ValueError,'range'):bound_context(c)

    def test_exact_named_dialogue_protects_listener_and_catches_wrong_speaker(self):
        c={'sentences':[{'text':'"What do you mean?"'},{'text':'Jonathan questioned with a frown.'}],
           'people':[{'id':'j','name':'Jonathan Turner'},{'id':'c','name':'Christopher Davis'}],
           'speakerHints':[{'sentence':0,'cueSentence':1,'characterId':'j'}],
           'shots':[{'startSentence':0,'endSentence':1,'action':'Christopher listens as Jonathan asks, "What do you mean?"'}]}
        checked,_=bound_context(c)
        raw={'majorIssues':[{'shotKey':'s0-e1','sentence':0,'issue':'wrong speaker','category':'speaker'}],'advisories':[]}
        self.assertEqual(restore_findings(checked,raw)['majorIssues'],[])
        raw['majorIssues'][0]['category']='timing'
        self.assertEqual(restore_findings(checked,raw)['majorIssues'],[])
        raw['majorIssues'][0]['category']='object'
        self.assertEqual(len(restore_findings(checked,raw)['majorIssues']),1)
        c['shots'][0]['action']='Christopher says, "What do you mean?"'
        checked,_=bound_context(c)
        r=restore_findings(checked,{'majorIssues':[],'advisories':[]})
        self.assertEqual(r['majorIssues'][0]['verifiedBy'],'explicit-source-cue')
        c['speakerHints'][0]['characterId']='c' # Untrusted hint contradicts source.
        checked,_=bound_context(c)
        self.assertEqual(checked['shots'][0]['verifiedDialogue'],[])

    def test_multi_sentence_quotation_requires_immediate_named_source_cue(self):
        c={'sentences':[{'text':'"Listen.'},{'text':'I still hold the key.'},{'text':'Do not forget."'},
                        {'text':'The Boss, Jonathan chuckled lightly.'}],
           'people':[{'id':'j','name':'Jonathan Turner'},{'id':'c','name':'Christopher Davis'}],
           'shots':[{'startSentence':1,'endSentence':1,'action':'Christopher speaks from his chair.'}]}
        checked,_=bound_context(c)
        r=restore_findings(checked,{'majorIssues':[],'advisories':[]})
        self.assertEqual(r['majorIssues'][0]['shotIndex'],0)
        self.assertEqual(r['majorIssues'][0]['sentence'],1)
        self.assertEqual(r['majorIssues'][0]['sourceQuote'],'I still hold the key.')
        c['sentences'][3]['text']='He chuckled lightly.'
        checked,_=bound_context(c)
        self.assertEqual(restore_findings(checked,{'majorIssues':[],'advisories':[]})['majorIssues'],[])
        c['sentences'][3]['text']='Jonathan and Christopher watched in silence.'
        checked,_=bound_context(c)
        self.assertEqual(checked['shots'][0]['verifiedSpeech'],[])

if __name__=='__main__':unittest.main()
