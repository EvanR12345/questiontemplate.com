"""Real lane/provider/journal integration with in-process transports only.

These fixtures buy no GPU or API work. They exercise receipt ownership and
admission races, rather than infer production throughput from synthetic time.
"""
import copy
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

from PIL import Image

from image_provider import ComfyImageProvider, RemoteGenerationUncertain, data_url
from studio_execution import ExecutionJournal, ImageLane, LaneProvider


class CancelledFixture(RuntimeError):
    pass


def png(color):
    stream = io.BytesIO()
    Image.new('RGB', (8, 8), color).save(stream, 'PNG')
    return stream.getvalue()


class InProcessComfy:
    """A second server job may finish while the first PNG is being read."""
    def __init__(self):
        self.lock = threading.Lock()
        self.posts = []
        self.calls = []
        self.posted = {name: threading.Event() for name in ('A', 'B', 'C')}
        self.delivering = {name: threading.Event() for name in ('A', 'B', 'C')}
        self.release = {name: threading.Event() for name in ('A', 'B', 'C')}
        self.colors = {'A': (40, 80, 120), 'B': (90, 130, 170), 'C': (140, 180, 220)}
        self.hold = set()
        self.fail_history = None
        self.before_post = None
        self.uploading = threading.Event()
        self.release_upload = threading.Event()
        self.hold_upload = False

    def json(self, url, body=None, **_kwargs):
        path = urlparse(url).path
        with self.lock:
            self.calls.append((path, copy.deepcopy(body)))
        if path == '/prompt':
            name = body['prompt']['1']['inputs']['text']
            if self.before_post:
                self.before_post(name)
            with self.lock:
                self.posts.append(copy.deepcopy(body))
            self.posted[name].set()
            return {'prompt_id': 'remote-' + name}
        if path.startswith('/history/'):
            name = path.rsplit('-', 1)[-1]
            if self.fail_history == name:
                if not self.posted['B'].wait(3):
                    raise AssertionError('Sibling did not reach its owned POST')
                raise OSError('Synthetic lost history response')
            remote = 'remote-' + name
            return {remote: {'status': {'status_str': 'success'}, 'outputs': {
                '1': {'images': [{'filename': name + '.png'}]}}}}
        if path == '/queue' and body is not None:
            return {}
        if path == '/queue':
            return {'queue_running': [[0, 'remote-B']]}
        raise AssertionError('Unexpected in-process route: ' + path)

    def open(self, request, **_kwargs):
        url = request.full_url if hasattr(request, 'full_url') else request
        parsed = urlparse(url)
        if parsed.path == '/upload/image':
            self.uploading.set()
            if self.hold_upload and not self.release_upload.wait(3):
                raise AssertionError('Upload release was not supplied')
            return io.BytesIO(json.dumps({'name': 'immutable-ref.png'}).encode())
        if parsed.path != '/view':
            raise AssertionError('Unexpected image route: ' + parsed.path)
        name = parse_qs(parsed.query)['filename'][0].removesuffix('.png')
        self.delivering[name].set()
        if name in self.hold and not self.release[name].wait(3):
            raise AssertionError('Delivery release was not supplied for ' + name)
        return io.BytesIO(png(self.colors[name]))

    def close(self):
        self.release_upload.set()
        for event in self.release.values():
            event.set()


def provider(backend, config=None, request_slots=2):
    result = ComfyImageProvider({'comfyPersistentHTTP': False, **(config or {})},
                               request_slots=request_slots)
    result.validateSettings = lambda settings, **_kwargs: copy.deepcopy(settings)
    result.healthCheck = lambda **_kwargs: {'installed': True}
    result.getCapabilities = lambda: {'maxReferenceImages': 2}
    result.template = lambda: {
        'model': 'flux', 'name': 'in-process-test',
        'prompt': {'1': {'class_type': 'SyntheticImage', 'inputs': {
            'text': '', 'seed': 0, 'image': ''}}},
        'bindings': {'prompt': [('1', 'text')], 'seed': [('1', 'seed')],
                     'reference0': [('1', 'image')]}}
    result._request_json = backend.json
    result._open = backend.open
    result.begin_worker_lifetime({'owned': True, 'ready': True,
        'podId': 'fixture', 'lifetimeId': 'fixture-1', 'endpoint': result.url})
    return result


def request(name, **changes):
    return {'prompt': name, 'settings': {'model': 'flux', 'seed': 17,
        'steps': 4, 'width': 1280, 'height': 720, 'guidance': 1}, **changes}


def owned_pool(backends):
    from studio_image_workers import ComfyWorkerPool
    workers = []
    def factory(config, request_slots):
        result = provider(backends[len(workers)], config, request_slots)
        workers.append(result)
        return result
    config = {'comfyPersistentHTTP': False, 'comfyModelCopies': 2,
              'comfyEndpoint': 'http://127.0.0.1:8188'}
    result = ComfyWorkerPool(config, factory=factory)
    result.confirm_workers({'gpu': 'NVIDIA GeForce RTX 5090', 'workers': [
        {'pid': 1001, 'port': 8188}, {'pid': 1002, 'port': 8189}]})
    result.begin_worker_lifetime({'owned': True, 'ready': True,
        'podId': 'fixture-pool', 'lifetimeId': 'fixture-pool-1',
        'endpoint': workers[0].url})
    return result, workers


class OneAheadLaneIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.journal = ExecutionJournal(Path(self.temp.name) / 'executions.sqlite3')
        self.backend = InProcessComfy()
        self.provider = provider(self.backend)
        self.lane = ImageLane(self.provider.client_capacity)
        self.contexts = {}
        self.budget_checks = {}

    def tearDown(self):
        self.backend.close()
        self.journal.close()
        self.temp.cleanup()

    def run_owned(self, name, gate=None, validator=None):
        context = self.journal.admit('story-job', 'project', 'chapter', name,
            'image-generation', 7, 'immutable-' + name, {'quality': 'same'})
        self.contexts[name] = context
        def budget_gate(*args):
            self.budget_checks[name] = self.budget_checks.get(name, 0) + 1
            if gate:
                gate(*args)
        wrapped = LaneProvider(self.provider, self.lane, budget_gate,
            lambda event, details: self.journal.observe(context.identity, event, details),
            validator=validator)
        original = request(name)
        before = copy.deepcopy(original)
        try:
            result = wrapped.generateImage(original, lambda *_args: budget_gate('image-checkpoint'))
            self.assertEqual(original, before)
            self.assertEqual(result['settings'], before['settings'])
            self.journal.finish(context.identity, 'COMPLETE')
            return result
        except BaseException:
            self.journal.finish(context.identity, 'FAILED')
            raise

    def test_second_job_executes_during_first_delivery_third_waits_and_ids_stay_owned(self):
        self.backend.hold = {'A', 'B'}
        waiting = threading.Event()
        def third_gate(*_args):
            waiting.set()
        def before_post(name):
            owned = next(r for r in self.journal.snapshot('story-job') if r['shot'] == name)
            self.assertEqual([op['state'] for op in owned['operations'].values()], ['SUBMITTED'])
            self.assertTrue(all('remoteId' not in op for op in owned['operations'].values()))
            self.assertGreaterEqual(self.budget_checks[name], 2,
                                    'A fresh dispatch must recheck its own gate before POST')
            if name == 'C':
                row = next(r for r in self.journal.snapshot('story-job') if r['shot'] == 'A')
                self.assertEqual([op['state'] for op in row['operations'].values()], ['COMPLETED'])
        self.backend.before_post = before_post
        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                a = pool.submit(self.run_owned, 'A')
                self.assertTrue(self.backend.delivering['A'].wait(2))
                b = pool.submit(self.run_owned, 'B')
                self.assertTrue(self.backend.delivering['B'].wait(2))
                c = pool.submit(self.run_owned, 'C', third_gate)
                self.assertTrue(waiting.wait(2))
                self.assertEqual(self.lane.active, 2)
                self.assertEqual(len(self.backend.posts), 2)
                self.assertFalse(self.backend.posted['C'].is_set())
                self.backend.release['A'].set()
                self.assertTrue(self.backend.posted['C'].wait(2))
                self.assertFalse(b.done(), 'The paid sibling still owns its separate delivery')
                self.backend.release['B'].set()
                results = [future.result(3) for future in (a, b, c)]
            finally:
                self.backend.close()
        self.assertEqual([r['pil'].getpixel((0, 0)) for r in results],
                         [self.backend.colors[name] for name in ('A', 'B', 'C')])
        rows = self.journal.snapshot('story-job')
        self.assertTrue(all(r['status'] == 'COMPLETE' for r in rows))
        operations = [next(iter(row['operations'].values())) for row in rows]
        self.assertEqual({op['remoteId'] for op in operations}, {'remote-A', 'remote-B', 'remote-C'})
        self.assertEqual(len({key for row in rows for key in row['operations']}), 3)
        self.assertTrue(all(self.budget_checks[name] >= 2 for name in ('A', 'B', 'C')))
        self.assertEqual(self.lane.active, 0)

    def test_cancelled_lane_waiter_submits_nothing_and_does_not_cancel_paid_siblings(self):
        self.backend.hold = {'A', 'B'}
        waiting = threading.Event()
        cancel = threading.Event()
        def gate(*_args):
            waiting.set()
            if cancel.is_set():
                raise CancelledFixture('Only this queued execution was cancelled')
        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                a = pool.submit(self.run_owned, 'A')
                self.assertTrue(self.backend.delivering['A'].wait(2))
                b = pool.submit(self.run_owned, 'B')
                self.assertTrue(self.backend.delivering['B'].wait(2))
                c = pool.submit(self.run_owned, 'C', gate)
                self.assertTrue(waiting.wait(2));cancel.set()
                with self.assertRaises(CancelledFixture):
                    c.result(2)
                self.assertEqual(len(self.backend.posts), 2)
                self.assertFalse(self.backend.posted['C'].is_set())
                self.backend.close()
                a.result(3);b.result(3)
            finally:
                self.backend.close()
        rows = {r['shot']: r for r in self.journal.snapshot('story-job')}
        self.assertEqual(rows['A']['status'], 'COMPLETE')
        self.assertEqual(rows['B']['status'], 'COMPLETE')
        self.assertEqual(rows['C']['operations'], {})
        self.assertFalse(any(path == '/interrupt' for path, _body in self.backend.calls))

    def test_unknown_owner_fences_new_posts_while_already_submitted_sibling_drains(self):
        self.backend.fail_history = 'A'
        self.backend.hold = {'B'}
        with ThreadPoolExecutor(max_workers=3) as pool:
            try:
                a = pool.submit(self.run_owned, 'A')
                self.assertTrue(self.backend.posted['A'].wait(2))
                b = pool.submit(self.run_owned, 'B')
                self.assertTrue(self.backend.delivering['B'].wait(2))
                with self.assertRaises(RemoteGenerationUncertain) as error:
                    a.result(2)
                self.assertEqual(error.exception.prompt_id, 'remote-A')
                c = pool.submit(self.run_owned, 'C')
                with self.assertRaises(RuntimeError):
                    c.result(2)
                self.assertEqual(len(self.backend.posts), 2)
                self.assertFalse(b.done())
                self.backend.release['B'].set()
                self.assertEqual(b.result(3)['remotePromptId'], 'remote-B')
            finally:
                self.backend.close()
        rows = {r['shot']: r for r in self.journal.snapshot('story-job')}
        self.assertEqual(rows['A']['status'], 'UNKNOWN')
        self.assertEqual(rows['B']['status'], 'COMPLETE')
        self.assertEqual(rows['C']['operations'], {})
        self.assertFalse(any(path == '/interrupt' for path, _body in self.backend.calls))

    def test_pool_quarantine_blocks_sibling_still_preparing_another_endpoint(self):
        backends = [InProcessComfy(), InProcessComfy()]
        preparing = threading.Event();release = threading.Event()
        pool_provider, workers = owned_pool(backends)
        original_json = workers[0]._request_json
        def lose_history(url, body=None, **kwargs):
            if '/history/' in url:
                if not preparing.wait(3):
                    raise AssertionError('Sibling did not enter preparation')
                raise OSError('First endpoint lost its accepted result')
            return original_json(url, body, **kwargs)
        workers[0]._request_json = lose_history
        validate = workers[1].validateSettings
        def hold_preparation(settings, **kwargs):
            preparing.set()
            if not release.wait(3):
                raise AssertionError('Preparation release was not supplied')
            return validate(settings, **kwargs)
        workers[1].validateSettings = hold_preparation
        self.provider = pool_provider;self.lane = ImageLane(pool_provider.client_capacity)
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                a = executor.submit(self.run_owned, 'A')
                self.assertTrue(backends[0].posted['A'].wait(2))
                b = executor.submit(self.run_owned, 'B')
                self.assertTrue(preparing.wait(2))
                with self.assertRaises(RemoteGenerationUncertain):
                    a.result(2)
                self.assertTrue(pool_provider.quarantined)
                release.set()
                with self.assertRaises(RuntimeError):
                    b.result(2)
                self.assertEqual(backends[1].posts, [])
            finally:
                release.set()
                for backend in backends:
                    backend.close()
        rows = {r['shot']: r for r in self.journal.snapshot('story-job')}
        self.assertEqual(rows['A']['status'], 'UNKNOWN')
        self.assertEqual(rows['B']['operations'], {})

    def test_pool_quarantine_preserves_an_already_posted_siblings_receipt_collection(self):
        backends = [InProcessComfy(), InProcessComfy()]
        pool_provider, workers = owned_pool(backends)
        collecting = threading.Event();release = threading.Event();history_calls = []
        a_json = workers[0]._request_json;b_json = workers[1]._request_json
        def lose_history(url, body=None, **kwargs):
            if '/history/' in url:
                if not collecting.wait(3):
                    raise AssertionError('Paid sibling did not begin result collection')
                raise OSError('Synthetic first-endpoint history loss')
            return a_json(url, body, **kwargs)
        def hold_history(url, body=None, **kwargs):
            if '/history/' in url:
                history_calls.append(url)
                if len(history_calls) == 1:
                    collecting.set()
                    if not release.wait(3):
                        raise AssertionError('Paid receipt release was not supplied')
                    return {}
            return b_json(url, body, **kwargs)
        workers[0]._request_json = lose_history;workers[1]._request_json = hold_history
        self.provider = pool_provider;self.lane = ImageLane(pool_provider.client_capacity)
        with ThreadPoolExecutor(max_workers=2) as executor:
            try:
                a = executor.submit(self.run_owned, 'A')
                self.assertTrue(backends[0].posted['A'].wait(2))
                b = executor.submit(self.run_owned, 'B')
                self.assertTrue(collecting.wait(2))
                with self.assertRaises(RemoteGenerationUncertain):
                    a.result(2)
                self.assertTrue(pool_provider.quarantined)
                release.set()
                self.assertEqual(b.result(3)['remotePromptId'], 'remote-B')
            finally:
                release.set()
                for backend in backends:
                    backend.close()
        self.assertEqual(len(history_calls), 2)
        rows = {r['shot']: r for r in self.journal.snapshot('story-job')}
        self.assertEqual(rows['A']['status'], 'UNKNOWN')
        self.assertEqual(rows['B']['status'], 'COMPLETE')
        self.assertEqual(len(backends[1].posts), 1)
        self.assertFalse(any(path == '/interrupt' for backend in backends
                             for path, _body in backend.calls))


class OneAheadImmutableCloneIntegrationTest(unittest.TestCase):
    """Exercise the actual CloudStoryOverlap clone validator, not a stand-in."""
    def test_real_generation_does_not_retry_or_fallback_for_pool_admission_failure(self):
        import studio_dense_phase_test as dense
        import studio_overlap_test as fixtures
        from image_provider import ImageAdmissionBlocked
        for defect in ('unverified', 'quarantined'):
            with self.subTest(defect=defect):
                fixtures.CloudOverlapTest.setUp(self)
                backends = [InProcessComfy(), InProcessComfy()]
                try:
                    old = dense.DensePhaseTest.prepared(self, 1);old.close('COMPLETE')
                    pool_provider, _workers = owned_pool(backends)
                    if defect == 'unverified':
                        pool_provider.prepare_worker_lifetime()
                    else:
                        pool_provider.quarantined = True
                    self.service.providers['comfyui'] = pool_provider
                    self.project['settings'].update(maxImageRetries=2, unattendedProduction=False)
                    self.project['settings']['director'].update(executionMode='classic', parallelism=1)
                    self.project['settings']['image'].update(fallbackEnabled=True,
                        fallback={'provider': 'native-flux', 'model': 'must-not-be-selected'})
                    self.project = self.service.store.save(self.project)
                    chid = self.project['chapters'][0]['id']
                    sid = self.project['chapters'][0]['scenes'][0]['shots'][0]['id']
                    with patch('studio_service.require_shot', return_value={'status': 'PASSED'}), \
                         patch.object(pool_provider, 'generateImage', wraps=pool_provider.generateImage) as generate, \
                         patch.object(pool_provider, 'unload', side_effect=AssertionError('Fallback unload was attempted')):
                        with self.assertRaises(ImageAdmissionBlocked):
                            self.service.generate(self.project, chid, sid, 17, {})
                    self.assertEqual(generate.call_count, 1)
                    self.assertEqual([backend.posts for backend in backends], [[], []])
                finally:
                    for backend in backends:
                        backend.close()
                    fixtures.CloudOverlapTest.tearDown(self)

    def test_nondense_paid_image_collection_survives_sibling_failure_but_not_user_or_budget_stop(self):
        import studio_dense_phase_test as dense
        import studio_overlap_test as fixtures
        from studio_data import get_shot
        from studio_overlap import CloudStoryOverlap
        from studio_service import JobCancelled
        from studio_shot_snapshot import shot_snapshot
        fixtures.CloudOverlapTest.setUp(self)
        overlap = None
        try:
            old = dense.DensePhaseTest.prepared(self, 1);old.close('COMPLETE')
            overlap = CloudStoryOverlap(self.service, self.project,
                {'overlap': True, 'generationStrategy': 'legacy'})
            snapshot = shot_snapshot(self.project)
            chid = snapshot['chapters'][0]['id']
            sid = snapshot['chapters'][0]['scenes'][0]['shots'][0]['id']
            shot = get_shot(snapshot, chid, sid)
            context = overlap.admit(snapshot, chid, sid, 'image-qc',
                self.service.visual_spec_signature(snapshot, shot))
            clone = overlap.clone(context, snapshot=snapshot)
            observer = clone.providers['comfyui'].observer
            observer('submitting', {'operationId': 'paid-operation', 'provider': 'comfyui'})
            observer('accepted', {'operationId': 'paid-operation', 'remoteId': 'paid-remote'})
            overlap._collecting_after_failure = True
            context.cancelled.set()
            with patch.object(self.rental, 'check') as budget:
                clone.checkpoint(0, 4, 'Collecting already submitted image')
            budget.assert_called_once_with()
            waiting = overlap.admit(snapshot, chid, sid + '-new', 'image-qc', 'new-input')
            waiting_clone = overlap.clone(waiting)
            waiting.cancelled.set()
            with self.assertRaises(JobCancelled):
                waiting_clone.checkpoint(0, 4, 'Preparing new image')
            with self.service.cv:
                self.service.cancel = True
            with self.assertRaises(JobCancelled):
                clone.checkpoint(0, 4, 'Explicit user cancellation')
            with self.service.cv:
                self.service.cancel = False
            with patch.object(self.rental, 'check', side_effect=RuntimeError('Rental cap reached')):
                with self.assertRaisesRegex(RuntimeError, 'Rental cap reached'):
                    clone.checkpoint(0, 4, 'Collecting within rental budget')
            self.assertEqual(overlap.coordinator.call(overlap.journal.finish,
                context.identity, 'FAILED'), 'UNKNOWN')
            overlap.coordinator.call(overlap.journal.finish, waiting.identity, 'FAILED')
        finally:
            with self.service.cv:
                self.service.cancel = False
            if overlap:
                overlap.close('FAILED')
            fixtures.CloudOverlapTest.tearDown(self)

    def test_source_or_selected_bytes_change_during_reference_upload_blocks_dispatch(self):
        import studio_dense_phase_test as dense
        import studio_overlap_test as fixtures
        from studio_data import get_shot
        from studio_overlap import CloudStoryOverlap, StaleExecution
        from studio_shot_snapshot import shot_snapshot
        for defect in ('source', 'selected-bytes'):
            with self.subTest(defect=defect):
                fixtures.CloudOverlapTest.setUp(self)
                overlap = None
                backend = InProcessComfy();backend.hold_upload = True
                try:
                    old = dense.DensePhaseTest.prepared(self, 1)
                    old.close('COMPLETE')
                    self.provider = provider(backend)
                    self.service.providers['comfyui'] = self.provider
                    pid = self.project['id'];chid = self.project['chapters'][0]['id']
                    sid = self.project['chapters'][0]['scenes'][0]['shots'][0]['id']
                    selected = self.service.store.folder(pid) / 'selected.png'
                    selected.write_bytes(png((110, 120, 130)))
                    self.service.store.mutate(pid, lambda p: get_shot(p, chid, sid).update(imagePath='selected.png'))
                    self.project = self.service.store.load(pid)
                    overlap = CloudStoryOverlap(self.service, self.project,
                        {'overlap': True, 'generationStrategy': 'dense'})
                    snapshot = shot_snapshot(self.project)
                    shot = get_shot(snapshot, chid, sid)
                    context = overlap.admit(snapshot, chid, sid, 'image-generation',
                        self.service.visual_spec_signature(snapshot, shot))
                    clone = overlap.clone(context, snapshot=snapshot)
                    ref = self.service.store.folder(pid) / 'canonical.png'
                    with ThreadPoolExecutor(max_workers=1) as pool:
                        future = pool.submit(clone.providers['comfyui'].generateImage,
                            request('A', referenceImages=[data_url(ref)]), clone.checkpoint)
                        try:
                            self.assertTrue(backend.uploading.wait(2))
                            if defect == 'source':
                                self.service.store.mutate(pid, lambda p: p['chapters'][0].update(
                                    sourceText='A newer chapter entered during reference upload.'))
                            else:
                                selected.write_bytes(png((190, 150, 110)))
                            backend.release_upload.set()
                            with self.assertRaises((StaleExecution, ValueError)):
                                future.result(3)
                        finally:
                            backend.close()
                    self.assertEqual(backend.posts, [])
                    row = next(r for r in self.service.execution_journal.snapshot(overlap.parent)
                               if r['id'] == context.identity)
                    self.assertEqual(row['operations'], {})
                    overlap.coordinator.call(overlap.journal.finish, context.identity, 'FAILED')
                    if defect == 'source':
                        self.assertEqual(self.service.store.load(pid)['chapters'][0]['sourceText'],
                                         'A newer chapter entered during reference upload.')
                    else:
                        self.assertEqual(selected.read_bytes(), png((190, 150, 110)))
                finally:
                    backend.close()
                    if overlap:
                        overlap.close('FAILED')
                    fixtures.CloudOverlapTest.tearDown(self)


if __name__ == '__main__':
    unittest.main()
