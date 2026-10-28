"""Exact director guard reuse never changes source, state, or numeric identity."""
import copy
import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from studio_analysis_inputs import ExactAnalysisInputs,analysis_input_document,analysis_input_signature as canonical_signature
from studio_data import digest,get_chapter,state_before
from studio_service import StudioService
from director_analysis_test import ChapterDirectorAnalysisTest


def legacy_signature(project,chapter_id):
    chapter=get_chapter(project,chapter_id)
    state,memory=state_before(project,chapter_id)
    fields=('sourceText','name','scenes','people','narrationMode','cleanNarrationText','includeChapterLabel')
    return digest({'chapter':{key:chapter.get(key) for key in fields},
        'audio':chapter.get('audio',{}).get('signature'),'settings':project['settings'],
        'inputState':state,'inputMemory':memory,
        'identities':[{key:person.get(key) for key in ('id','name','description','aliases',
            'permanentIdentity','defaultAppearance','references')} for person in project['characters']]})


class ExactAnalysisInputsTest(unittest.TestCase):
    def fixture(self):
        project=ChapterDirectorAnalysisTest().fixture()
        project['chapters'][0]['handoff']={'state':{'environment':{'time':'day'}},'memory':{'majorEvents':['arrived']}}
        project['chapters'][1]['audio']['signature']='audio-original'
        return project,project['chapters'][1]['id']

    def test_builder_keeps_existing_signature_bytes_for_complete_document(self):
        p,chid=self.fixture()
        self.assertEqual(StudioService.analysis_input_signature(p,chid),legacy_signature(p,chid))
        document=analysis_input_document(p,chid)
        self.assertIn('inputMemory',document)
        self.assertEqual(document['chapter']['scenes'],get_chapter(p,chid)['scenes'])
        for path in ('settings','identities','inputState','inputMemory'):
            self.assertIn(path,document)

    def test_same_fresh_parsed_input_reuses_sha_but_keeps_changed_metadata(self):
        p,chid=self.fixture();p=json.loads(json.dumps(p));cache=ExactAnalysisInputs();seen=[]
        expected=legacy_signature(p,chid)
        fresh=json.loads(json.dumps(p));fresh['revision']=900
        fresh.setdefault('production',{})['timings']=[{'seconds':25}]
        def observed(document):seen.append(True);return digest(document)
        with patch('studio_analysis_inputs.digest',side_effect=observed):
            self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
            self.assertTrue(cache.matches(fresh,chid,expected,canonical_signature))
        self.assertEqual(len(seen),1)
        self.assertEqual(fresh['revision'],900)

    def test_all_existing_input_fields_invalidate_the_original_expectation(self):
        p,chid=self.fixture();expected=legacy_signature(p,chid)
        edits={
            'source':lambda q:get_chapter(q,chid).update(sourceText='new source'),
            'name':lambda q:get_chapter(q,chid).update(name='new title'),
            'scene':lambda q:get_chapter(q,chid)['scenes'].append({'id':'new-scene'}),
            'people':lambda q:get_chapter(q,chid)['people'].append({'name':'new speaker'}),
            'narrationMode':lambda q:get_chapter(q,chid).update(narrationMode='manual'),
            'cleanNarrationText':lambda q:get_chapter(q,chid).update(cleanNarrationText='changed words'),
            'includeChapterLabel':lambda q:get_chapter(q,chid).update(includeChapterLabel=not get_chapter(q,chid)['includeChapterLabel']),
            'audioSignature':lambda q:get_chapter(q,chid)['audio'].update(signature='new-audio'),
            'settings':lambda q:q['settings']['director'].update(parallelism=7),
            'causalState':lambda q:q['chapters'][0]['handoff']['state']['environment'].update(time='night'),
            'causalMemory':lambda q:q['chapters'][0]['handoff']['memory']['majorEvents'].append('left'),
        }
        for field in ('id','name','description','aliases','permanentIdentity','defaultAppearance','references'):
            edits['identity-'+field]=lambda q,field=field:q['characters'][0].update({field:'changed-'+field})
        for label,edit in edits.items():
            with self.subTest(label=label):
                cache=ExactAnalysisInputs();self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
                changed=copy.deepcopy(p);edit(changed)
                self.assertFalse(cache.matches(changed,chid,expected,canonical_signature))
                self.assertTrue(cache.matches(changed,chid,legacy_signature(changed,chid),canonical_signature))
                self.assertEqual(StudioService.analysis_input_signature(changed,chid),legacy_signature(changed,chid))

    def test_bool_integer_and_float_cannot_share_a_cached_authorization(self):
        p,chid=self.fixture();cache=ExactAnalysisInputs()
        p['settings']['customLayout']['imagesPerMinute']=1
        expected=legacy_signature(p,chid)
        self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
        for value in (1.0,True):
            with self.subTest(value=type(value).__name__):
                changed=copy.deepcopy(p);changed['settings']['customLayout']['imagesPerMinute']=value
                self.assertFalse(cache.matches(changed,chid,expected,canonical_signature))

    def test_cached_bytes_cannot_borrow_later_mutations_or_future_state(self):
        p,chid=self.fixture();cache=ExactAnalysisInputs();expected=legacy_signature(p,chid)
        self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
        p['chapters'][0]['handoff']['memory']['majorEvents'].append('new event')
        self.assertFalse(cache.matches(p,chid,expected,canonical_signature))
        p['chapters'][0]['handoff']['memory']['majorEvents'].pop()
        p['chapters'][1]['handoff']={'state':{'environment':{'time':'future'}},'memory':{}}
        self.assertTrue(cache.matches(p,chid,expected,canonical_signature))

    def test_object_order_difference_is_only_an_extra_original_sha_check(self):
        p,chid=self.fixture();cache=ExactAnalysisInputs();expected=legacy_signature(p,chid);seen=[]
        fresh=copy.deepcopy(p);fresh['settings']=dict(reversed(list(fresh['settings'].items())))
        def observed(document):seen.append(1);return digest(document)
        with patch('studio_analysis_inputs.digest',side_effect=observed):
            self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
            self.assertTrue(cache.matches(fresh,chid,expected,canonical_signature))
            self.assertEqual(len(seen),2)
            self.assertTrue(cache.matches(fresh,chid,expected,canonical_signature));self.assertEqual(len(seen),2)

    def test_concurrent_identical_inputs_compute_the_original_signature_once(self):
        p,chid=self.fixture();expected=legacy_signature(p,chid);cache=ExactAnalysisInputs()
        seen=[];barrier=threading.Barrier(4)
        def observed(document):seen.append(1);return digest(document)
        def check(_):
            fresh=json.loads(json.dumps(p));barrier.wait()
            return cache.matches(fresh,chid,expected,canonical_signature)
        with patch('studio_analysis_inputs.digest',side_effect=observed):
            with ThreadPoolExecutor(max_workers=4) as pool:self.assertEqual(list(pool.map(check,range(4))),[True]*4)
        self.assertEqual(len(seen),1)

    def test_byte_budget_entry_bound_and_oversized_fallback(self):
        p,chid=self.fixture();expected=legacy_signature(p,chid)
        probe=ExactAnalysisInputs();probe.matches(p,chid,expected,canonical_signature)
        size=probe.accounted_bytes;cache=ExactAnalysisInputs(max_bytes=size*2-1,max_entries=8)
        for n in range(3):
            fresh=copy.deepcopy(p);fresh['id']='same-length-project-'+str(n)
            self.assertTrue(cache.matches(fresh,chid,expected,canonical_signature))
            self.assertLessEqual(cache.accounted_bytes,cache.max_bytes)
        self.assertLessEqual(cache.entries,1)
        cache=ExactAnalysisInputs(max_bytes=1,max_entries=1);seen=[]
        def observed(document):seen.append(1);return digest(document)
        with patch('studio_analysis_inputs.digest',side_effect=observed):
            for _ in range(2):self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
        self.assertEqual(cache.entries,0);self.assertEqual(cache.accounted_bytes,0);self.assertEqual(len(seen),2)
        cache=ExactAnalysisInputs(max_entries=2)
        for n in range(4):
            fresh=copy.deepcopy(p);fresh['id']='project-'+str(n)
            self.assertTrue(cache.matches(fresh,chid,expected,canonical_signature))
        self.assertEqual(cache.entries,2)

    def test_failed_or_raising_signature_is_never_remembered(self):
        p,chid=self.fixture();expected=legacy_signature(p,chid);cache=ExactAnalysisInputs()
        self.assertFalse(cache.matches(p,chid,'wrong',legacy_signature));self.assertEqual(cache.entries,0)
        def failure(*_):raise OSError('signature unavailable')
        with self.assertRaises(OSError):cache.matches(p,chid,expected,failure)
        self.assertEqual(cache.entries,0)

    def test_custom_stateful_signature_is_never_bypassed_even_after_a_pure_hit(self):
        p,chid=self.fixture();cache=ExactAnalysisInputs();expected=legacy_signature(p,chid)
        self.assertTrue(cache.matches(p,chid,expected,canonical_signature))
        state=[expected];seen=[]
        def custom(q,c):seen.append(1);return state[0]
        self.assertTrue(cache.matches(p,chid,expected,custom))
        state[0]='changed external adapter state'
        self.assertFalse(cache.matches(p,chid,expected,custom))
        self.assertEqual(len(seen),2);self.assertEqual(cache.entries,1)

    def test_change_while_first_pure_signature_runs_cannot_publish_old_bytes(self):
        p,chid=self.fixture();cache=ExactAnalysisInputs();expected=legacy_signature(p,chid)
        def changed(document):
            result=digest(document)
            get_chapter(p,chid)['sourceText']='changed during signature computation'
            return result
        with patch('studio_analysis_inputs.digest',side_effect=changed):
            self.assertFalse(cache.matches(p,chid,expected,canonical_signature))
        self.assertEqual(cache.entries,0)


if __name__=='__main__':unittest.main()
