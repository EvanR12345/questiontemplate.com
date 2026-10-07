import tempfile
import unittest
import json
from unittest.mock import patch
from decimal import Decimal
from pathlib import Path
from cost_control import SpendLedger, luna_cost

class CostGuardTest(unittest.TestCase):
    def test_flex_is_discounted_only_after_confirmed_tier_and_usage(self):
        usage={'input_tokens':1000,'output_tokens':200}
        with tempfile.TemporaryDirectory() as folder:
            ledger=SpendLedger(Path(folder)/'cost.json',.02)
            body={'model':'gpt-6-luna','max_output_tokens':1000,'input':[], 'service_tier':'flex'}
            bound=ledger.reserve(body)
            self.assertEqual(ledger.settle(usage,service_tier='flex'),luna_cost(usage)*.5)
            self.assertEqual(ledger.load()['receipts'][-1]['serviceTier'],'flex')
            ledger.reserve(body)
            self.assertEqual(ledger.settle(usage),luna_cost(usage))
            ledger.reserve(body)
            self.assertEqual(ledger.settle(service_tier='flex'),bound)
            ledger.reserve(body)
            self.assertEqual(ledger.settle(usage,service_tier='unrecognized'),bound)
            self.assertFalse(ledger.load()['receipts'][-1]['usageConfirmed'])

    def test_long_context_rates_are_conservative(self):
        usage={'input_tokens':300000,'output_tokens':2000}
        self.assertAlmostEqual(luna_cost(usage),.0615)
        self.assertAlmostEqual(luna_cost(usage,'flex'),.03075)
        self.assertAlmostEqual(luna_cost({'input_tokens':272000,'output_tokens':2000}),.0282)

    def test_cache_writes_are_not_double_counted_or_priced_as_ordinary_input(self):
        usage = {'input_tokens': 1000, 'output_tokens': 200,
                 'input_tokens_details': {'cached_tokens': 400, 'cache_write_tokens': 300}}
        self.assertAlmostEqual(luna_cost(usage), .0001715)
        self.assertAlmostEqual(luna_cost({'input_tokens': 1000, 'output_tokens': 0,
                                        'input_tokens_details': {'cache_write_tokens': 1000}}), .000125)
        with self.assertRaises(ValueError):
            luna_cost({'input_tokens': 100, 'input_tokens_details': {'cached_tokens': 60, 'cache_write_tokens': 60}})

    def test_text_content_has_no_image_allowance_and_cache_misses_are_reserved(self):
        with tempfile.TemporaryDirectory() as folder:
            body = {'model': 'gpt-6-luna', 'max_output_tokens': 100,
                    'input': [{'role': 'user', 'content': [{'type': 'input_text', 'text': 'story'}]}]}
            ledger = SpendLedger(Path(folder)/'cost.json', .01)
            import json
            bound = ledger.reserve(body)
            self.assertAlmostEqual(bound, (len(json.dumps(body).encode())*.125 + 100*.50)/1e6)
            ledger.settle(uncharged=True)
            image = {'type': 'input_image', 'image_url': 'data:image/png;base64,' + 'A'*100000}
            body['input'][0]['content'].append(image)
            image_bound = ledger.reserve(body)
            self.assertGreater(image_bound - bound, .001)
            self.assertLess(image_bound - bound, .0011)

    def test_usage_cache_and_restart_budget(self):
        self.assertAlmostEqual(luna_cost({'input_tokens':1000,'output_tokens':200,'input_tokens_details':{'cached_tokens':500}}), .000155)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            ledger = SpendLedger(path, .002)
            body = {'model':'gpt-6-luna','max_output_tokens':1000,'input':[{'role':'user','content':'hello'}]}
            ledger.reserve(body)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                SpendLedger(path,.002).reserve(body)
            ledger.settle({'input_tokens':1000,'output_tokens':2000})
            with self.assertRaisesRegex(RuntimeError, 'exceed'):
                ledger.reserve(body | {'max_output_tokens':4000})
            self.assertEqual(len(ledger.load()['receipts']),1)
    def test_failure_reserves_cost_without_false_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = SpendLedger(Path(folder)/'cost.json',.01)
            bound = ledger.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':[]})
            ledger.settle()
            self.assertEqual(ledger.load()['spentUSD'], bound)

    def test_missing_or_malformed_usage_keeps_conservative_charge(self):
        for usage in ({}, {'input_tokens': 10}, {'input_tokens': 10, 'output_tokens': -1},
                      {'input_tokens': 10, 'output_tokens': 0, 'input_tokens_details': {'cached_tokens': 20}}):
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as folder:
                ledger = SpendLedger(Path(folder)/'cost.json', .01)
                bound = ledger.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':[]})
                self.assertEqual(ledger.settle(usage), bound)
                receipt = ledger.load()['receipts'][-1]
                self.assertFalse(receipt['usageConfirmed'])
                self.assertEqual(receipt['usage'], usage)


class RequestOwnershipTest(unittest.TestCase):
    body = {'model':'gpt-6-luna', 'max_output_tokens':1000, 'input':[]}
    usage = {'input_tokens':100, 'output_tokens':25}

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)/'ledger.json'
        self.ledger = SpendLedger(self.path, .02, allow_multiple=True)

    def tearDown(self):
        self.folder.cleanup()

    def reserve(self, rid='a', owner='chapter-a'):
        return self.ledger.reserve(self.body, request_id=rid, owner=owner)

    def submit(self, rid='a', owner='chapter-a'):
        self.ledger.mark_submitted(rid, owner=owner)

    def test_cap_includes_every_pending_owner_and_releases_only_settled_difference(self):
        bound = SpendLedger.estimate(self.body)
        self.ledger = SpendLedger(self.path, str(2*Decimal(str(bound))+Decimal('.0000225')), allow_multiple=True)
        self.reserve('a', 'owner-a'); self.reserve('b', 'owner-b')
        with self.assertRaisesRegex(RuntimeError, 'exceed'):
            self.reserve('c', 'owner-c')
        self.assertEqual(self.ledger.load()['pending']['count'], 2)
        self.ledger.settle(self.usage, request_id='a', owner='owner-a')
        self.reserve('c', 'owner-c')
        state = self.ledger.load()
        self.assertEqual(set(state['requests']), {'b','c'})
        self.assertAlmostEqual(state['liabilityUSD'], 2*bound + .0000225)

    def test_default_mode_remains_serial_even_with_explicit_ids(self):
        self.ledger = SpendLedger(self.path, .02)
        self.reserve()
        with self.assertRaisesRegex(RuntimeError,'unresolved'):
            self.reserve('b','chapter-b')

    def test_stale_owner_cannot_expand_a_shared_allowance(self):
        self.reserve()
        stale=SpendLedger(self.path,.2,allow_multiple=True)
        with self.assertRaisesRegex(RuntimeError,'cap changed'):
            stale.reserve(self.body,request_id='stale-request',owner='stale-owner')
        with self.assertRaisesRegex(RuntimeError,'cap changed'):
            stale.mark_submitted('a',owner='chapter-a')
        self.assertEqual(set(self.ledger.load()['requests']),{'a'})

    def test_unknown_response_survives_restart_and_retains_full_liability(self):
        bound = self.reserve(); self.submit()
        self.ledger.bind_response('a','resp-a',owner='chapter-a')
        self.assertEqual(self.ledger.settle(None,request_id='a',owner='chapter-a'),bound)
        reopened = SpendLedger(self.path,.02,allow_multiple=True)
        state = reopened.load()
        self.assertEqual(state['requests']['a']['status'],'UNKNOWN')
        self.assertEqual(state['requests']['a']['responseId'],'resp-a')
        self.assertEqual(state['spentUSD'],0)
        self.assertEqual(state['liabilityUSD'],bound)
        self.assertEqual(state['receipts'],[])
        reopened.settle(self.usage,request_id='a',owner='chapter-a')
        self.assertIsNone(reopened.load()['pending'])
        self.assertAlmostEqual(reopened.load()['spentUSD'],.0000225)

    def test_cancellation_before_and_after_dispatch_have_different_liability(self):
        self.reserve('a','owner-a'); bound=self.reserve('b','owner-b')
        self.submit('b','owner-b')
        self.assertTrue(self.ledger.cancel('a',owner='owner-a'))
        self.assertFalse(self.ledger.cancel('b',owner='owner-b'))
        state=self.ledger.load()
        self.assertEqual(set(state['requests']),{'b'})
        self.assertEqual(state['requests']['b']['status'],'UNKNOWN')
        self.assertEqual(state['liabilityUSD'],bound)
        self.assertTrue(state['receipts'][0]['uncharged'])
        self.assertFalse(self.ledger.cancel('a',owner='owner-a'))
        self.assertEqual(len(self.ledger.load()['receipts']),1)

    def test_owner_cannot_settle_cancel_inspect_or_bind_another_request(self):
        self.reserve()
        before=self.path.read_bytes()
        actions=[lambda:self.ledger.settle(self.usage,request_id='a',owner='another-owner'),
            lambda:self.ledger.cancel('a',owner='another-owner'),
            lambda:self.ledger.request_state('a',owner='another-owner'),
            lambda:self.ledger.bind_response('a','resp-a',owner='another-owner'),
            lambda:self.ledger.mark_submitted('a',owner='another-owner'),
            lambda:self.ledger.mark_unknown('a',owner='another-owner'),
            lambda:self.ledger.reject('a',401,owner='another-owner')]
        for action in actions:
            with self.assertRaises(PermissionError): action()
            self.assertEqual(self.path.read_bytes(),before)

    def test_legacy_cleanup_cannot_settle_an_explicit_or_ambiguous_request(self):
        self.reserve()
        with self.assertRaisesRegex(RuntimeError,'Explicit request ID'):
            self.ledger.settle()
        self.reserve('b','chapter-b')
        with self.assertRaisesRegex(RuntimeError,'Explicit request ID'):
            self.ledger.settle(uncharged=True)
        self.assertEqual(set(self.ledger.load()['requests']),{'a','b'})

    def test_receipts_are_idempotent_across_restart_and_conflicts_halt_dispatch(self):
        self.reserve(); self.submit()
        cost=self.ledger.settle(self.usage,request_id='a',owner='chapter-a')
        old_receipt=self.ledger.load()['receipts'][0]
        reopened=SpendLedger(self.path,.02,allow_multiple=True)
        self.assertEqual(reopened.settle(self.usage,request_id='a',owner='chapter-a'),cost)
        self.assertEqual(reopened.load()['receipts'],[old_receipt])
        with self.assertRaisesRegex(ValueError,'Conflicting duplicate'):
            reopened.settle(self.usage|{'output_tokens':26},request_id='a',owner='chapter-a')
        self.assertEqual(reopened.load()['receipts'],[old_receipt])
        with self.assertRaisesRegex(RuntimeError,'halted'):
            reopened.reserve(self.body,request_id='b',owner='chapter-b')

    def test_free_receipt_is_idempotent_but_cannot_release_submitted_work(self):
        self.reserve()
        self.ledger.settle(uncharged=True,request_id='a',owner='chapter-a')
        self.assertEqual(self.ledger.settle(uncharged=True,request_id='a',owner='chapter-a'),0)
        self.reserve('b','chapter-b'); self.submit('b','chapter-b')
        with self.assertRaisesRegex(ValueError,'rejection evidence'):
            self.ledger.settle(uncharged=True,request_id='b',owner='chapter-b')
        self.assertIn('b',self.ledger.load()['requests'])

    def test_provider_overrun_is_recorded_and_freezes_already_held_dispatch(self):
        self.reserve(); self.reserve('b','chapter-b'); self.submit()
        usage={'input_tokens':100,'output_tokens':10000}
        with self.assertRaisesRegex(RuntimeError,'exceeded'):
            self.ledger.settle(usage,request_id='a',owner='chapter-a')
        state=self.ledger.load()
        self.assertAlmostEqual(state['spentUSD'],.00501)
        self.assertEqual(state['halted'],'RESERVATION_EXCEEDED')
        with self.assertRaisesRegex(RuntimeError,'halted'):
            self.submit('b','chapter-b')
        self.assertEqual(self.ledger.settle(usage,request_id='a',owner='chapter-a'),.00501)
        self.assertEqual(len(self.ledger.load()['receipts']),1)

    def test_opaque_request_id_cannot_be_reused_with_same_or_different_body(self):
        self.reserve()
        for body in (self.body,self.body|{'max_output_tokens':2000}):
            with self.assertRaisesRegex(ValueError,'already exists'):
                self.ledger.reserve(body,request_id='a',owner='chapter-a')
        self.ledger.settle(self.usage,request_id='a',owner='chapter-a')
        with self.assertRaisesRegex(ValueError,'already exists'): self.reserve()

    def test_second_submit_never_dispatches_an_unknown_or_completed_request(self):
        self.reserve(); self.submit()
        with self.assertRaisesRegex(RuntimeError,'reconcile'): self.submit()
        self.ledger.mark_unknown('a',owner='chapter-a')
        with self.assertRaisesRegex(RuntimeError,'reconcile'): self.submit()
        self.ledger.settle(self.usage,request_id='a',owner='chapter-a')
        with self.assertRaisesRegex(RuntimeError,'reconcile'): self.submit()

    def test_response_identity_is_owned_and_cannot_be_charged_to_two_requests(self):
        self.reserve(); self.submit()
        self.ledger.bind_response('a','resp-shared',owner='chapter-a')
        self.ledger.bind_response('a','resp-shared',owner='chapter-a')
        self.reserve('b','chapter-b'); self.submit('b','chapter-b')
        with self.assertRaisesRegex(ValueError,'Conflicting provider'):
            self.ledger.bind_response('b','resp-shared',owner='chapter-b')
        self.assertEqual(set(self.ledger.load()['requests']),{'a','b'})
        with self.assertRaisesRegex(RuntimeError,'halted'): self.reserve('c','chapter-c')

    def test_binding_a_response_is_acceptance_evidence_not_a_free_held_cancel(self):
        bound=self.reserve()
        self.ledger.bind_response('a','resp-a',owner='chapter-a')
        self.assertFalse(self.ledger.cancel('a',owner='chapter-a'))
        self.assertEqual(self.ledger.load()['liabilityUSD'],bound)

    def test_late_binding_never_rewrites_a_settled_historical_receipt(self):
        self.reserve(); self.ledger.settle(self.usage,request_id='a',owner='chapter-a')
        receipt=self.ledger.load()['receipts'][0]
        with self.assertRaisesRegex(ValueError,'Conflicting provider'):
            self.ledger.bind_response('a','resp-late',owner='chapter-a')
        self.assertEqual(self.ledger.load()['receipts'],[receipt])

    def test_definite_http_rejection_releases_only_its_own_request(self):
        self.reserve(); self.reserve('b','chapter-b'); self.submit()
        self.assertTrue(self.ledger.reject('a',401,owner='chapter-a'))
        self.assertEqual(set(self.ledger.load()['requests']),{'b'})
        self.assertEqual(self.ledger.load()['spentUSD'],0)

    def test_http_timeout_conflict_and_server_error_retain_reservations(self):
        for status in (408,409,500,502,503,504):
            rid=f'http-{status}'; bound=self.reserve(rid,'http-owner'); self.submit(rid,'http-owner')
            self.assertFalse(self.ledger.reject(rid,status,owner='http-owner'))
            self.assertEqual(self.ledger.request_state(rid,owner='http-owner')['status'],'UNKNOWN')
            self.assertEqual(self.ledger.request_state(rid,owner='http-owner')['reservedUSD'],bound)

    def test_rejection_after_response_created_is_not_assumed_uncharged(self):
        self.reserve(); self.submit()
        self.ledger.bind_response('a','resp-a',owner='chapter-a')
        self.assertFalse(self.ledger.reject('a',429,owner='chapter-a'))
        self.assertIn('a',self.ledger.load()['requests'])

    def test_malformed_usage_never_frees_an_explicit_reservation(self):
        cases=[None,{}, {'input_tokens':1}, {'input_tokens':True,'output_tokens':0},
            {'input_tokens':10,'output_tokens':-1},
            {'input_tokens':10,'output_tokens':1,'input_tokens_details':None},
            {'input_tokens':10,'output_tokens':1,'input_tokens_details':{'cached_tokens':True}},
            {'input_tokens':10,'output_tokens':1,'input_tokens_details':{'cache_write_tokens':1.5}},
            {'input_tokens':10,'output_tokens':1,'input_tokens_details':{'cached_tokens':'1'}},
            {'input_tokens':10,'output_tokens':1,'input_tokens_details':{'cached_tokens':11}}]
        for index,usage in enumerate(cases):
            rid=f'malformed-{index}'; bound=self.reserve(rid,'usage-owner')
            self.submit(rid,'usage-owner')
            self.assertEqual(self.ledger.settle(usage,request_id=rid,owner='usage-owner'),bound)
            self.assertEqual(self.ledger.request_state(rid,owner='usage-owner')['status'],'UNKNOWN')
        self.assertEqual(self.ledger.load()['receipts'],[])

    def test_corrupt_or_future_ledger_fails_before_dispatch(self):
        for payload in ('{',json.dumps({'schemaVersion':99,'spentUSD':0,'receipts':[]}),
                json.dumps({'spentUSD':-1,'receipts':[],'pending':None}),
                json.dumps({'schemaVersion':2,'spentUSD':0,'receipts':[]})):
            with self.subTest(payload=payload):
                self.path.write_text(payload)
                with self.assertRaises((ValueError,KeyError)): self.reserve()
                self.assertEqual(self.path.read_text(),payload)

    def test_corrupt_money_mirrors_never_reduce_a_preserved_reservation(self):
        self.reserve()
        original=json.loads(self.path.read_text())
        corrupted=[]
        negative=json.loads(json.dumps(original));negative['spentNanoUSD']=-1;corrupted.append(negative)
        mismatch=json.loads(json.dumps(original));mismatch['spentNanoUSD']=1;corrupted.append(mismatch)
        hidden=json.loads(json.dumps(original));hidden['requests']['a']['reservedUSD']=0;corrupted.append(hidden)
        for state in corrupted:
            self.path.write_text(json.dumps(state))
            before=self.path.read_bytes()
            with self.assertRaises(ValueError): self.reserve('b','chapter-b')
            self.assertEqual(self.path.read_bytes(),before)

    def test_older_writer_pending_cannot_hide_a_reservation_in_a_v2_ledger(self):
        self.reserve(); self.ledger.cancel('a',owner='chapter-a')
        state=json.loads(self.path.read_text())
        self.assertEqual(state['requests'],{})
        # An older helper preserves unknown fields but only writes `pending`.
        state['pending']={'reservedUSD':.001,'model':'gpt-6-luna','started':100}
        self.path.write_text(json.dumps(state))
        before=self.path.read_bytes()
        with self.assertRaisesRegex(ValueError,'pending reservation'):
            self.reserve('b','chapter-b')
        self.assertEqual(self.path.read_bytes(),before)

    def test_pending_mirror_mismatch_fails_closed_without_repairing_saved_state(self):
        self.reserve()
        original=json.loads(self.path.read_text())
        for changed in (None,original['pending']|{'status':'UNKNOWN'}):
            state=original|{'pending':changed}
            self.path.write_text(json.dumps(state))
            before=self.path.read_bytes()
            with self.assertRaisesRegex(ValueError,'pending reservation'):
                self.ledger.load()
            self.assertEqual(self.path.read_bytes(),before)

    def test_fsync_failure_keeps_previous_file_and_prevents_dispatch(self):
        self.reserve();before=self.path.read_bytes()
        with patch('cost_control.os.fsync',side_effect=OSError('synthetic fsync failure')):
            with self.assertRaises(OSError): self.submit()
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(self.ledger.request_state('a',owner='chapter-a')['status'],'HELD')
        self.assertFalse(list(self.path.parent.glob('*.writing')))

    def test_legacy_pending_and_receipts_migrate_without_repricing_history(self):
        old_receipt={'estimatedUSD':.001,'usage':{'input_tokens':1000,'output_tokens':2000},
            'pricingDate':'2026-10-02','customHistoricalField':'preserve me'}
        original={'spentUSD':.001,'receipts':[old_receipt],
            'pending':{'reservedUSD':.002,'model':'gpt-6-luna','started':100}}
        self.path.write_text(json.dumps(original))
        bytes_before=self.path.read_bytes()
        state=self.ledger.load()
        self.assertEqual(self.path.read_bytes(),bytes_before)
        self.assertEqual(state['receipts'],[old_receipt])
        self.assertEqual(state['liabilityUSD'],.003)
        self.ledger.settle()
        self.assertEqual(self.ledger.load()['receipts'][0],old_receipt)
        self.assertEqual(self.ledger.load()['spentUSD'],.003)

    def test_failed_atomic_replace_never_creates_an_undurable_reservation(self):
        self.reserve(); before=self.path.read_bytes()
        with patch('cost_control.os.replace',side_effect=OSError('simulated disk failure')):
            with self.assertRaises(OSError): self.reserve('b','chapter-b')
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(set(self.ledger.load()['requests']),{'a'})
        self.assertFalse(list(self.path.parent.glob('*.writing')))

    def test_request_records_contain_hashes_not_story_image_bytes_or_credentials(self):
        body=self.body|{'input':[{'role':'user','content':[{'type':'input_text','text':'private-fiction-marker'},
            {'type':'input_image','image_url':'data:image/png;base64,PRIVATE_IMAGE_MARKER'}]}]}
        self.ledger.reserve(body,request_id='private-request',owner='private-owner')
        saved=self.path.read_text()
        self.assertNotIn('private-fiction-marker',saved)
        self.assertNotIn('PRIVATE_IMAGE_MARKER',saved)
        self.assertNotIn('data:image',saved)
        self.assertEqual(len(self.ledger.load()['requests']['private-request']['requestHash']),64)

if __name__ == '__main__':
    unittest.main()
