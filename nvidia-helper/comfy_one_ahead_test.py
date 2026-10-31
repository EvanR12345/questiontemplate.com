"""Offline admission, ownership and cancellation races; no real HTTP or GPUs."""
import base64
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit
from pathlib import Path

from PIL import Image
from image_provider import ComfyImageProvider, ImageAdmissionBlocked, RemoteGenerationUncertain
from studio_execution import ExecutionJournal


def png(color):
    out = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(out, 'PNG')
    return out.getvalue()


class OneAheadTest(unittest.TestCase):
    def fixture(self, slots=2, verified=True):
        p = ComfyImageProvider({'comfyPersistentHTTP': False}, request_slots=slots)
        template = {'model': 'flux', 'name': 'test', 'prompt': {
            '1': {'class_type': 'Test', 'inputs': {'text': '', 'seed': 0}},
            '2': {'class_type': 'LoadImage', 'inputs': {'image': ''}}},
            'bindings': {'prompt': [['1', 'text']], 'seed': [['1', 'seed']],
                         'reference0': [['2', 'image']]}}
        p.validateSettings = lambda s: s
        p.healthCheck = lambda **_: {'installed': True}
        p.template = lambda: template
        p.getCapabilities = lambda: {'maxReferenceImages': 1}
        if verified and slots == 2:p.begin_worker_lifetime(self.proof(p))
        colors = {'a': (150, 30, 90), 'b': (40, 170, 60), 'c': (60, 80, 200)}
        calls = []
        call_lock = threading.Lock()

        def network(url, body=None, **_):
            with call_lock:calls.append((url, copy.deepcopy(body)))
            if url.endswith('/prompt'):
                return {'prompt_id': body['prompt']['1']['inputs']['text']}
            if '/history/' in url:
                identity = url.rsplit('/', 1)[1]
                return {identity: {'status': {'status_str': 'success'}, 'outputs': {
                    '1': {'images': [{'filename': identity + '.png'}]}}}}
            if url.endswith('/queue'):return {}
            raise AssertionError(url)

        def read(request, timeout):
            url = request if isinstance(request, str) else request.full_url
            if '/view?' in url:
                name = parse_qs(urlsplit(url).query)['filename'][0].removesuffix('.png')
                return io.BytesIO(png(colors[name]))
            if url.endswith('/upload/image'):return io.BytesIO(b'{"name":"ref.png"}')
            raise AssertionError(url)

        p._request_json, p._open = network, read
        return p, calls, network, read, template, colors

    @staticmethod
    def proof(p, identity='lifetime-1'):
        return {'owned': True, 'ready': True, 'podId': 'fixture-pod',
                'lifetimeId': identity, 'endpoint': p.url}

    @staticmethod
    def request(name, reference=False):
        value = {'prompt': name, 'settings': {'model': 'flux', 'seed': 17, 'steps': 4}}
        if reference:value['referenceImages'] = ['data:image/png;base64,' + base64.b64encode(png((90, 40, 200))).decode()]
        return value

    @staticmethod
    def posts(calls):
        return [body for url, body in calls if url.endswith('/prompt')]

    def test_capacity_validation_and_two_slots_need_owned_verified_lifetime(self):
        for value in (True, False, 0, 3, 2.0, '2', None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ComfyImageProvider({}, request_slots=value)
        p, calls, *_ = self.fixture(verified=False)
        with self.assertRaisesRegex(RuntimeError, 'verified owned'):
            p.generateImage(self.request('a'), lambda *_: None)
        self.assertEqual(self.posts(calls), [])
        for bad in ({}, self.proof(p) | {'owned': False}, self.proof(p) | {'ready': False},
                    self.proof(p) | {'endpoint': 'http://127.0.0.1:8189'},
                    self.proof(p) | {'podId': ''}, self.proof(p) | {'lifetimeId': ''}):
            with self.subTest(proof=bad), self.assertRaises(ValueError):p.begin_worker_lifetime(bad)
        p.begin_worker_lifetime(self.proof(p))
        self.assertEqual(p.generateImage(self.request('a'), lambda *_: None)['requestSlots'], 2)
        self.assertEqual(ComfyImageProvider({}).client_capacity, 1)

    def test_two_requests_overlap_and_third_waits_without_mixing_graphs_or_pixels(self):
        p, calls, network, _, template, colors = self.fixture()
        original = copy.deepcopy(template)
        accepted = threading.Event(); release_a = threading.Event(); release_b = threading.Event()
        two = threading.Event(); waiting_c = threading.Event(); post_names = []

        def transport(url, body=None, **kwargs):
            if url.endswith('/prompt'):
                post_names.append(body['prompt']['1']['inputs']['text'])
                if len(post_names) == 2:two.set()
            if '/history/a' in url:
                accepted.set()
                if not release_a.wait(3):raise TimeoutError('Fixture a did not release')
            if '/history/b' in url and not release_b.wait(3):raise TimeoutError('Fixture b did not release')
            return network(url, body, **kwargs)

        p._request_json = transport
        with ThreadPoolExecutor(max_workers=3) as pool:
            a = pool.submit(p.generateImage, self.request('a'), lambda *_: None)
            self.assertTrue(accepted.wait(2))
            b = pool.submit(p.generateImage, self.request('b'), lambda *_: None)
            self.assertTrue(two.wait(2))
            c = pool.submit(p.generateImage, self.request('c'), lambda *_: waiting_c.set())
            try:
                self.assertTrue(waiting_c.wait(2)); self.assertEqual(post_names, ['a', 'b'])
                self.assertEqual(p._active_requests, 2)
                with self.assertRaisesRegex(RuntimeError, 'active'):
                    p.begin_worker_lifetime(self.proof(p, 'lifetime-2'))
                release_a.set(); outcomes = [a.result(2)]
                release_b.set(); outcomes.extend((b.result(2), c.result(2)))
            finally:release_a.set(); release_b.set()
        self.assertEqual(post_names, ['a', 'b', 'c'])
        for name, result in zip(('a', 'b', 'c'), outcomes):
            self.assertEqual(result['remotePromptId'], name)
            self.assertEqual(result['pil'].getpixel((0, 0)), colors[name])
            self.assertEqual(result['settings'], self.request(name)['settings'])
            self.assertEqual(result['resolvedWorkflow']['1']['inputs'], {'text': name, 'seed': 17})
        self.assertEqual(template, original); self.assertEqual(p._active_requests, 0)

    def test_unknown_fences_waiters_while_an_already_submitted_sibling_drains(self):
        p, calls, network, *_ = self.fixture()
        a_history = threading.Event(); b_posted = threading.Event(); allow_b = threading.Event()

        def transport(url, body=None, **kwargs):
            if url.endswith('/prompt') and body['prompt']['1']['inputs']['text'] == 'b':b_posted.set()
            if '/history/a' in url:
                a_history.set()
                if not b_posted.wait(3):raise TimeoutError('Fixture b not posted')
                raise OSError('Accepted a lost its history reply')
            if '/history/b' in url and not allow_b.wait(3):raise TimeoutError('Fixture b not released')
            return network(url, body, **kwargs)

        p._request_json = transport
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(p.generateImage, self.request('a'), lambda *_: None)
            self.assertTrue(a_history.wait(2))
            b = pool.submit(p.generateImage, self.request('b'), lambda *_: None)
            try:
                with self.assertRaises(RemoteGenerationUncertain) as error:a.result(2)
                self.assertEqual(error.exception.prompt_id, 'a')
                with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                    p.generateImage(self.request('c'), lambda *_: None)
                allow_b.set(); self.assertEqual(b.result(2)['remotePromptId'], 'b')
            finally:allow_b.set()
        self.assertEqual([x['prompt']['1']['inputs']['text'] for x in self.posts(calls)], ['a', 'b'])
        self.assertFalse(any(url.endswith('/interrupt') for url, _ in calls))

    def test_preparing_sibling_is_fenced_before_any_prompt_post(self):
        p, calls, network, read, *_ = self.fixture()
        a_history = threading.Event(); upload_entered = threading.Event(); upload_release = threading.Event()

        def transport(url, body=None, **kwargs):
            if '/history/a' in url:
                a_history.set()
                if not upload_entered.wait(3):raise TimeoutError('Fixture upload did not start')
                raise OSError('Unknown accepted a')
            return network(url, body, **kwargs)

        def upload(request, timeout):
            if not isinstance(request, str) and request.full_url.endswith('/upload/image'):
                upload_entered.set()
                if not upload_release.wait(3):raise TimeoutError('Fixture upload not released')
            return read(request, timeout)

        p._request_json, p._open = transport, upload
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(p.generateImage, self.request('a'), lambda *_: None)
            self.assertTrue(a_history.wait(2))
            b = pool.submit(p.generateImage, self.request('b', True), lambda *_: None)
            try:
                with self.assertRaises(RemoteGenerationUncertain):a.result(2)
                upload_release.set()
                with self.assertRaisesRegex(RuntimeError, 'unresolved'):b.result(2)
            finally:upload_release.set()
        self.assertEqual(len(self.posts(calls)), 1)
        self.assertEqual(p._uploads, {})

    def test_fence_during_durable_intent_settles_only_unused_sibling_without_adopting_id(self):
        p, calls, network, *_ = self.fixture()
        a_history = threading.Event(); b_intent = threading.Event()
        with tempfile.TemporaryDirectory() as folder:
            journal = ExecutionJournal(Path(folder) / 'requests.sqlite3')
            contexts = {name: journal.admit('job', 'project', 'chapter', name, 'image', 0, name, {})
                        for name in ('a', 'b')}
            def transport(url, body=None, **kwargs):
                if '/history/a' in url:
                    a_history.set()
                    if not b_intent.wait(3):raise TimeoutError('Fixture b intent missing')
                    raise OSError('a has an unknown accepted result')
                return network(url, body, **kwargs)
            def observer(name):
                def observe(event, details):
                    journal.observe(contexts[name].identity, event, details)
                    if name == 'b' and event == 'submitting':
                        b_intent.set()
                        if not p._uncertain.wait(3):raise TimeoutError('a fence did not arrive')
                return observe
            p._request_json = transport
            with ThreadPoolExecutor(max_workers=2) as pool:
                a = pool.submit(p.generateImage, self.request('a') | {'_executionObserver': observer('a')}, lambda *_: None)
                self.assertTrue(a_history.wait(2))
                b = pool.submit(p.generateImage, self.request('b') | {'_executionObserver': observer('b')}, lambda *_: None)
                with self.assertRaises(ImageAdmissionBlocked) as blocked:b.result(2)
                self.assertFalse(blocked.exception.submitted); self.assertIsNone(blocked.exception.prompt_id)
                with self.assertRaises(RemoteGenerationUncertain) as unknown:a.result(2)
                self.assertEqual(unknown.exception.prompt_id, 'a')
            self.assertEqual(journal.finish(contexts['a'].identity, 'FAILED'), 'UNKNOWN')
            self.assertEqual(journal.finish(contexts['b'].identity, 'FAILED'), 'FAILED')
            rows = {row['shot']: row for row in journal.snapshot('job')}
            first = next(iter(rows['a']['operations'].values()))
            second = next(iter(rows['b']['operations'].values()))
            self.assertEqual(first['remoteId'], 'a'); self.assertEqual(second['state'], 'REJECTED')
            self.assertNotIn('remoteId', second)
            self.assertFalse(set(rows['a']['operations']) & set(rows['b']['operations']))
            journal.close()
        self.assertEqual(len(self.posts(calls)), 1)

    def test_pool_fence_never_waits_for_worker_mutex_or_interrupts_accepted_sibling(self):
        p, calls, network, *_ = self.fixture()
        locked = threading.Event(); release = threading.Event()
        def hold():
            with p._admission:
                locked.set()
                if not release.wait(3):raise TimeoutError('Fixture mutex not released')
        with ThreadPoolExecutor(max_workers=2) as pool:
            holder = pool.submit(hold); self.assertTrue(locked.wait(2))
            fence = pool.submit(p.block_new_dispatch)
            try:
                fence.result(.5)
                self.assertTrue(p._uncertain.is_set())
            finally:release.set()
            holder.result(2)
        with self.assertRaises(ImageAdmissionBlocked):p.generateImage(self.request('a'), lambda *_: None)
        self.assertEqual(self.posts(calls), [])

    def test_cancelling_third_slot_waiter_buys_nothing_and_leaves_two_receipts_intact(self):
        p, calls, network, *_ = self.fixture()
        both = threading.Event(); waiting = threading.Event(); cancel = threading.Event(); release = threading.Event()
        posted = []
        def transport(url, body=None, **kwargs):
            if url.endswith('/prompt'):
                posted.append(body['prompt']['1']['inputs']['text'])
                if len(posted) == 2:both.set()
            if '/history/' in url and not release.wait(3):raise TimeoutError('Fixture history not released')
            return network(url, body, **kwargs)
        def checkpoint(*_):
            waiting.set()
            if cancel.is_set():raise InterruptedError('Third request cancelled before admission')
        p._request_json = transport
        with ThreadPoolExecutor(max_workers=3) as pool:
            a = pool.submit(p.generateImage, self.request('a'), lambda *_: None)
            b = pool.submit(p.generateImage, self.request('b'), lambda *_: None)
            self.assertTrue(both.wait(2))
            c = pool.submit(p.generateImage, self.request('c'), checkpoint)
            try:
                self.assertTrue(waiting.wait(2)); cancel.set()
                with self.assertRaises(InterruptedError):c.result(2)
                self.assertFalse(p._uncertain.is_set()); self.assertCountEqual(posted, ['a', 'b'])
                release.set(); self.assertEqual({a.result(2)['remotePromptId'], b.result(2)['remotePromptId']}, {'a', 'b'})
            finally:release.set()
        self.assertFalse(any(url.endswith('/queue') or url.endswith('/interrupt') for url, _ in calls))

    def test_final_source_validator_after_reference_preparation_buys_nothing(self):
        p, calls, *_ = self.fixture(); observed = []
        request = self.request('a', True)
        request['_executionObserver'] = lambda event, details: observed.append(event)
        def changed():raise RuntimeError('Selected input bytes changed')
        request['_preDispatchValidator'] = changed
        with self.assertRaisesRegex(RuntimeError, 'Selected input'):
            p.generateImage(request, lambda *_: None)
        self.assertEqual(self.posts(calls), []); self.assertEqual(observed, [])
        self.assertFalse(p._uncertain.is_set())

    def test_observer_and_invalid_png_failures_fence_before_a_replacement_dispatch(self):
        for stage in ('submitting', 'accepted', 'completed', 'invalid-png', 'unexpected-history-error'):
            with self.subTest(stage=stage):
                p, calls, network, read, *_ = self.fixture(); request = self.request('a')
                def observer(event, details):
                    if event == stage:raise RuntimeError('Journal write failed')
                request['_executionObserver'] = observer
                if stage == 'invalid-png':p._open = lambda *_args, **_kwargs: io.BytesIO(b'not-a-png')
                if stage == 'unexpected-history-error':
                    def fail(url, body=None, **kwargs):
                        if '/history/' in url:raise RuntimeError('Unexpected protocol failure')
                        return network(url, body, **kwargs)
                    p._request_json = fail
                with self.assertRaises(RemoteGenerationUncertain):p.generateImage(request, lambda *_: None)
                before = len(self.posts(calls))
                self.assertEqual(before, 0 if stage == 'submitting' else 1)
                with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                    p.generateImage(self.request('b'), lambda *_: None)
                self.assertEqual(len(self.posts(calls)), before)
                self.assertEqual(p._active_requests, 0)

    def test_known_rejection_and_failed_history_are_terminal_without_false_quarantine(self):
        for failure in ('rejected', 'failed'):
            with self.subTest(failure=failure):
                p, calls, network, *_ = self.fixture(); events = []
                def transport(url, body=None, **kwargs):
                    if url.endswith('/prompt') and body['prompt']['1']['inputs']['text'] == 'a' and failure == 'rejected':
                        calls.append((url, body))
                        raise urllib.error.HTTPError(url, 400, 'Rejected', {}, None)
                    if '/history/a' in url and failure == 'failed':
                        return {'a': {'status': {'status_str': 'error'}, 'outputs': {}}}
                    return network(url, body, **kwargs)
                p._request_json = transport
                request = self.request('a') | {'_executionObserver': lambda event, details: events.append(event)}
                with self.assertRaises((urllib.error.HTTPError, RuntimeError)):
                    p.generateImage(request, lambda *_: None)
                self.assertEqual(events[-1], failure)
                self.assertFalse(p._uncertain.is_set())
                self.assertEqual(p.generateImage(self.request('b'), lambda *_: None)['remotePromptId'], 'b')

    def test_queued_cancel_deletes_only_its_id_and_never_global_interrupt(self):
        p, calls, network, *_ = self.fixture()
        def cancel(*args):
            if args[-1] == 'ComfyUI generation':raise InterruptedError('User cancelled a')
        with self.assertRaises(RemoteGenerationUncertain) as error:p.generateImage(self.request('a'), cancel)
        self.assertEqual(error.exception.prompt_id, 'a')
        self.assertEqual([(url.rsplit('/', 1)[1], body) for url, body in calls if url.endswith('/queue')],
                         [('queue', {'delete': ['a']})])
        self.assertFalse(any(url.endswith('/interrupt') for url, _ in calls))
        self.assertTrue(p._uncertain.is_set())

    def test_metadata_refresh_cannot_clear_unknown_or_replay_lifetime_proof(self):
        p, calls, network, *_ = self.fixture()
        p._request_json = lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('Lost acknowledgement'))
        with self.assertRaises(RemoteGenerationUncertain):p.generateImage(self.request('a'), lambda *_: None)
        p.invalidate_backend_cache()
        with self.assertRaisesRegex(RuntimeError, 'unresolved'):
            p.generateImage(self.request('b'), lambda *_: None)
        with self.assertRaisesRegex(RuntimeError, 'same worker'):p.begin_worker_lifetime(self.proof(p))
        p.begin_worker_lifetime(self.proof(p, 'fresh-verified-start'))
        p._request_json = network
        self.assertEqual(p.generateImage(self.request('b'), lambda *_: None)['remotePromptId'], 'b')

    def test_identical_reference_upload_is_single_flight_for_two_request_owners(self):
        p, calls, _, read, *_ = self.fixture()
        uploading = threading.Event(); release = threading.Event(); waiting = threading.Event(); uploads = []
        def upload(request, timeout):
            if not isinstance(request, str) and request.full_url.endswith('/upload/image'):
                uploads.append(request.data); uploading.set()
                if not release.wait(3):raise TimeoutError('Fixture upload not released')
            return read(request, timeout)
        def gate(*args):
            if args[-1] == 'Waiting for the identical character reference upload':waiting.set()
        p._open = upload
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(p.generateImage, self.request('a', True), lambda *_: None)
            self.assertTrue(uploading.wait(2))
            b = pool.submit(p.generateImage, self.request('b', True), gate)
            try:
                self.assertTrue(waiting.wait(2)); self.assertEqual(len(uploads), 1)
                release.set(); rows = [a.result(2), b.result(2)]
            finally:release.set()
        self.assertEqual(len(uploads), 1)
        self.assertEqual(sum(row['providerTimings']['referenceUploadCacheHits'] for row in rows), 1)
        self.assertTrue(all(body['prompt']['2']['inputs']['image'] == 'ref.png' for body in self.posts(calls)))

    def test_late_old_upload_cannot_repopulate_cache_after_invalidation(self):
        p, calls, _, read, *_ = self.fixture()
        uploading = threading.Event(); release = threading.Event(); uploads = []
        def upload(request, timeout):
            if not isinstance(request, str) and request.full_url.endswith('/upload/image'):
                uploads.append(request.data)
                if len(uploads) == 1:
                    uploading.set()
                    if not release.wait(3):raise TimeoutError('Fixture old upload not released')
                    return io.BytesIO(b'{"name":"old.png"}')
                return io.BytesIO(b'{"name":"new.png"}')
            return read(request, timeout)
        p._open = upload
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(p.generateImage, self.request('a', True), lambda *_: None)
            self.assertTrue(uploading.wait(2)); p.invalidate_backend_cache()
            b = pool.submit(p.generateImage, self.request('b', True), lambda *_: None)
            try:
                self.assertEqual(b.result(2)['remotePromptId'], 'b')
                release.set()
                with self.assertRaisesRegex(RuntimeError, 'metadata changed'):a.result(2)
            finally:release.set()
        self.assertEqual(list(p._uploads.values()), ['new.png'])
        self.assertEqual(len(self.posts(calls)), 1)
        self.assertEqual(self.posts(calls)[0]['prompt']['2']['inputs']['image'], 'new.png')


if __name__ == '__main__':unittest.main()
