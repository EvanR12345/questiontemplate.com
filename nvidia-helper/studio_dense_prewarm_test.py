"""Offline envelope and event-controlled startup race tests; no provider calls."""
import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from studio_dense_prewarm import envelope, validate_final, Startup


class EnvelopeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        Image.new('RGB', (8, 8), 'gray').save(self.root/'face.png')
        self.settings = {'model': 'exact-model', 'workflow': 'exact-workflow', 'width': 1280, 'height': 720}
        self.person = {'id': 'hero', 'name': 'Hero', 'type': 'main', 'references': [{'path': 'face.png', 'kind': 'face'}]}
        self.chapter = {'id': 'final', 'sourceText': 'The hero waits.', 'scenes': [], 'people': [], 'audio': {'signature': 'audio'}}
        self.project = {'id': 'p', 'chapters': [self.chapter], 'characters': [self.person], 'locations': [],
                        'styleReferences': [], 'intro': {'enabled': False},
                        'settings': {'lockImageModel': True, 'image': self.settings, 'maxImageRetries': 2}}
        self.prepared = [{'visualTimings': [{'text': 'one'}, {'text': 'two'}],
            'analysis': {'people': [{'id': 'detected', 'name': 'Hero', 'type': 'main'}]},
            'storyboard': {'detail': {'shots': [{'characters': ['detected'], 'sceneIndex': 0}]},
                           'cameras': [{'shot': 'medium', 'angle': 'eye level'}],
                           'plan': {'scenes': [{'location': 'Room'}]}}}]
        provider = SimpleNamespace(validateSettings=lambda settings, **kwargs: copy.deepcopy(settings),
            getModelCapabilities=lambda model: {'maxReferenceImages': 2, 'supportsStyleReference': True})
        self.store = SimpleNamespace(asset=lambda pid, path: self.root/path, folder=lambda pid: self.root)
        self.service = SimpleNamespace(provider=lambda name: provider, store=self.store,
            saved_narration_ready=lambda p, c: True, analysis_is_current=lambda p, c, sig: True,
            saved_intro_audio_ready=lambda p: True)
        self.disk = patch('studio_deferred_qc.shutil.disk_usage', return_value=SimpleNamespace(free=20*2**30))
        self.disk.start()

    def tearDown(self):
        self.disk.stop()
        self.temp.cleanup()

    def proof(self):
        return envelope(self.service, self.project, self.chapter, self.prepared, [self.person])

    def final(self):
        p = copy.deepcopy(self.project)
        p['chapters'][0]['scenes'] = [{'shots': [{'id': 'new', 'imageProvider': 'comfyui',
            'imageModel': self.settings['model'], 'workflow': self.settings['workflow'],
            'generationSettings': self.settings}]}]
        return p

    def test_exact_frozen_units_bound_all_repairs_and_retains_reference_bytes(self):
        proof = self.proof()
        self.assertEqual(proof['maximumImages'], 2)
        self.assertEqual(proof['provisionalImages'], 1)
        self.assertEqual(proof['capacity']['maximumDescriptors'], 6)
        self.assertEqual(set(proof['references']['assets']), {'face.png'})
        validate_final(proof, self.final(), self.store)

    def test_unknown_main_incomplete_intro_manual_proposed_unlocked_fail_closed(self):
        changes = [lambda: self.prepared[0]['analysis']['people'].append({'id': 'new', 'name': 'Unknown', 'type': 'main'}),
            lambda: self.project['intro'].update(enabled=True, sourceBrief='Unknown'),
            lambda: self.chapter.update(scenes=[{'manual': True, 'shots': []}]),
            lambda: self.chapter.update(proposedPlan={'scenes': []}),
            lambda: self.project['settings'].update(lockImageModel=False)]
        for change in changes:
            old_p, old_prepared = copy.deepcopy(self.project), copy.deepcopy(self.prepared)
            change()
            with self.assertRaises(ValueError): self.proof()
            self.project, self.prepared = old_p, old_prepared
            self.chapter = self.project['chapters'][0]

    def test_not_final_and_no_work_and_unbounded_units_reject(self):
        with self.assertRaises(ValueError): envelope(self.service, self.project, self.chapter, [], [self.person])
        self.project['chapters'].append({**self.chapter, 'id': 'later'})
        with self.assertRaises(ValueError): self.proof()
        self.project['chapters'].pop()
        self.prepared[0]['visualTimings'] = []
        with self.assertRaises(ValueError): self.proof()

    def test_all_reference_alternatives_are_checked_before_startup(self):
        self.person['references'].append({'path': 'missing-side.png', 'kind': 'side'})
        with self.assertRaises(OSError): self.proof()

    def test_incompatible_identity_reference_slots_fail_before_spend(self):
        p = {'id': 'other', 'name': 'Other', 'type': 'main', 'references': [{'path': 'face.png'}]}
        self.project['characters'] += [p, {**p, 'id': 'third', 'name': 'Third'}]
        self.prepared[0]['storyboard']['detail']['shots'][0]['characters'] = ['hero', 'other', 'third']
        with self.assertRaises(ValueError): self.proof()

    def test_descriptor_limit_and_disk_fail_before_spend(self):
        self.prepared[0]['visualTimings'] *= 1400
        with self.assertRaisesRegex(ValueError, 'descriptor'): self.proof()
        self.prepared[0]['visualTimings'] = [{}]
        with patch('studio_deferred_qc.shutil.disk_usage', return_value=SimpleNamespace(free=1)):
            with self.assertRaisesRegex(ValueError, 'free disk'): self.proof()

    def test_final_reference_bytes_count_and_dimensions_cannot_escape_proof(self):
        proof = self.proof()
        p = self.final()
        p['chapters'][0]['scenes'][0]['shots'] *= 3
        with self.assertRaisesRegex(ValueError, 'count'): validate_final(proof, p, self.store)
        p = self.final()
        p['chapters'][0]['scenes'][0]['shots'][0]['generationSettings'] = {**self.settings, 'width': 1920}
        with self.assertRaisesRegex(ValueError, 'settings'): validate_final(proof, p, self.store)
        Image.new('RGB', (8, 8), 'red').save(self.root/'face.png')
        with self.assertRaisesRegex(ValueError, 'bytes'): validate_final(proof, self.final(), self.store)


class StartupTest(unittest.TestCase):
    def rental(self):
        entered, release = threading.Event(), threading.Event()
        rental = SimpleNamespace(gate=lambda *a, **k: None, state={'status': 'STOPPED'}, starts=0, stops=0, active=False)
        def ensure():
            rental.starts += 1
            rental.active = True
            entered.set()
            try:
                if not release.wait(5): raise AssertionError('Fixture release missing')
                rental.gate('Acquiring exact owned resource')
                rental.state = {'status': 'RUNNING'}
            finally: rental.active = False
        def stop(reason):
            self.assertFalse(rental.active, 'Stop raced the startup thread')
            rental.stops += 1
            rental.state = {'status': 'STOPPED'}
        rental.ensure, rental.stop = ensure, stop
        return rental, entered, release

    def test_single_future_parallel_starts_and_join_before_first_inference(self):
        rental, entered, release = self.rental()
        startup = Startup(rental, lambda *a: None)
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(lambda _: startup.start(), range(3)))
            self.assertTrue(entered.wait(2))
            waiting = pool.submit(startup.join)
            self.assertFalse(waiting.done())
            release.set()
            waiting.result(timeout=3)
        self.assertEqual(rental.starts, 1)
        self.assertEqual(rental.state['status'], 'RUNNING')
        startup.close()

    def test_failure_before_acquisition_aborts_then_joins_then_stops(self):
        rental, entered, release = self.rental()
        startup = Startup(rental, lambda *a: None)
        startup.start()
        self.assertTrue(entered.wait(2))
        with ThreadPoolExecutor(max_workers=1) as pool:
            close = pool.submit(startup.close, True)
            self.assertTrue(startup.abort.wait(2))
            self.assertEqual(rental.stops, 0)
            release.set()
            close.result(timeout=3)
        self.assertEqual(rental.starts, 1)
        self.assertEqual(rental.stops, 1)
        self.assertEqual(rental.state['status'], 'STOPPED')
        startup.close(True)
        self.assertEqual(rental.stops, 1)

    def test_unknown_startup_never_retries_and_shutdown_failure_visible(self):
        rental, entered, release = self.rental()
        def unknown():
            rental.starts += 1
            rental.state = {'status': 'UNKNOWN'}
            raise RuntimeError('Unknown provider response')
        rental.ensure = unknown
        startup = Startup(rental, lambda *a: None)
        startup.start()
        with self.assertRaisesRegex(RuntimeError, 'Unknown'): startup.join()
        startup.start()
        self.assertEqual(rental.starts, 1)
        rental.stop = lambda reason: None
        with self.assertRaisesRegex(RuntimeError, 'not confirmed'): startup.close(True)

    def test_assembly_deadline_retires_only_optional_window_and_restores_normal_gate(self):
        rental, entered, release = self.rental()
        now = [10]
        startup = Startup(rental, lambda *a: None, clock=lambda: now[0], maximum_assembly_seconds=120)
        startup.start()
        release.set()
        startup.join()
        now[0] = 131
        startup.check()
        self.assertTrue(startup.retired)
        self.assertEqual(rental.state['status'], 'STOPPED')
        self.assertEqual(rental.stops, 1)
        self.assertEqual(rental.starts, 1)
        self.assertIsNone(startup.thread_id)
        # Continued planning/new ordinary startup is permitted only after the
        # confirmed stop. The real broker carries the first window's spend.
        startup.check()
        startup.thread_id = threading.get_ident()  # Simulated OS/Python reuse.
        startup.check();startup.join()
        startup.thread_id = None
        rental.ensure()
        self.assertEqual(rental.starts, 2)
        self.assertEqual(rental.state['status'], 'RUNNING')
        rental.stop('ordinary final completion')

    def test_reentrant_startup_gate_never_retires_or_joins_its_own_future(self):
        rental, entered, release = self.rental()
        now = [0]
        startup = Startup(rental, lambda *a: None, clock=lambda: now[0])
        # Simulate GPUReservation's original gate being a LaneService gate that
        # re-enters overlap.check on the startup thread itself.
        rental.gate = lambda *a, **k: startup.check()
        startup.original_gate = rental.gate
        startup.start()
        self.assertTrue(entered.wait(2))
        now[0] = 121
        release.set()
        startup.future.result(timeout=2)
        self.assertFalse(startup.retired)
        startup.check()  # Only this owner thread may retire it.
        self.assertTrue(startup.retired)
        self.assertEqual(rental.stops, 1)

    def test_reentrant_abort_gate_during_owner_retirement_does_not_mutually_wait(self):
        rental, entered, release = self.rental()
        now = [0]
        startup = Startup(rental, lambda *a: None, clock=lambda: now[0])
        rental.gate = lambda *a, **k: startup.check()
        startup.original_gate = rental.gate
        startup.start()
        self.assertTrue(entered.wait(2))
        now[0] = 121
        with ThreadPoolExecutor(max_workers=1) as pool:
            retiring = pool.submit(startup.check)
            self.assertTrue(startup.abort.wait(2))
            release.set()
            retiring.result(timeout=3)
        self.assertTrue(startup.retired)
        self.assertEqual(rental.stops, 1)

    def test_retired_optional_window_is_carried_once_by_real_broker_under_same_cap(self):
        import json
        from studio_gpu_broker import GPUReservation
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'owned.json'
            controllers = []
            now = [0]
            def factory():
                state = json.loads(path.read_text()) if path.exists() else {'status': 'STOPPED'}
                controller = SimpleNamespace(state=state, owned=False, runner=None, gate=reservation.gate)
                controller.previous = state.get('spentByProject', {}).get('project', 0)
                def ensure():
                    controller.gate('Start exact owned worker')
                    controller.owned = True
                    controller.state['status'] = 'RUNNING'
                def stop(reason):
                    if not controller.owned:return
                    controller.owned = False
                    controller.state.update(status='STOPPED', spentByProject={'project': controller.previous+.02})
                    path.write_text(json.dumps(controller.state))
                controller.ensure, controller.stop = ensure, stop
                controller.save = lambda **kwargs: controller.state.update(kwargs)
                controllers.append(controller)
                return controller
            reservation = GPUReservation({'gpuProviderOrder': ['lium'], 'gpuImageVerifiedProviders': ['lium']},
                path, 'project', {'gpuBudgetUSD': 1}, None, lambda *a, **k: None, factories={'lium': factory})
            startup = Startup(reservation, lambda *a: None, clock=lambda: now[0])
            startup.start();startup.join()
            now[0] = 121;startup.check()
            self.assertTrue(startup.retired)
            self.assertEqual(reservation.state['spentByProject']['project'], .02)
            reservation.ensure();reservation.stop('ordinary burst complete')
            self.assertEqual(len(controllers), 2)
            self.assertEqual(controllers[1].previous, .02)
            self.assertEqual(reservation.budget, {'gpuBudgetUSD': 1})
            self.assertAlmostEqual(reservation.state['spentByProject']['project'], .04)


class ServiceIntegrationTest(unittest.TestCase):
    def setUp(self):
        import studio_overlap_test as fixtures
        fixtures.CloudOverlapTest.setUp(self)

    def tearDown(self):
        import studio_overlap_test as fixtures
        fixtures.CloudOverlapTest.tearDown(self)

    def prepared(self, enabled=True):
        import studio_dense_phase_test as fixtures
        self.service.store.mutate(self.project['id'], lambda p:p['settings'].update(lockImageModel=True))
        overlap = fixtures.DensePhaseTest.prepared(self, 1)
        self.service.config['studioDenseJITPrewarm'] = enabled
        overlap.main.config['studioDenseJITPrewarm'] = enabled
        rental, entered, release = StartupTest.rental(self)
        self.service._production_gpu = overlap.main._production_gpu = rental
        project = self.service.store.load(self.project['id'])
        chapter = project['chapters'][0]
        person = project['characters'][0]
        prepared = [{'visualTimings': [{'text': 'one'}], 'analysis': {'people': [person]},
            'storyboard': {'detail': {'shots': [{'characters': [person['id']], 'sceneIndex': 0}]},
                'cameras': [{'shot': 'medium', 'angle': 'eye level'}],
                'plan': {'scenes': [{'location': ''}]}}}]
        expected = overlap.main.analysis_input_signature(project, chapter['id'])
        return overlap, rental, entered, release, project, chapter, prepared, expected

    def test_real_service_starts_only_infrastructure_then_final_gate_joins_before_inference(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
        try:
            overlap.main.maybe_dense_prewarm(p, ch, staged, p['characters'], expected)
            self.assertTrue(entered.wait(3))
            self.assertEqual(self.provider.requests, [])
            self.assertFalse(overlap.dense_preflight_complete)
            with self.assertRaisesRegex(ValueError, 'does not authorize'):
                overlap.main.ensure_production_gpu()
            overlap.main.mark_dense_prewarm_assembly_ready(p['id'])
            with ThreadPoolExecutor(max_workers=1) as pool:
                final = pool.submit(overlap.main.prepare_dense_image_phase, p['id'])
                self.assertFalse(final.done())
                self.assertFalse(overlap.dense_preflight_complete)
                release.set()
                final.result(timeout=4)
            self.assertTrue(overlap.dense_preflight_complete)
            self.assertEqual(rental.starts, 1)
            self.assertEqual(self.provider.requests, [])
            marker = self.service.store.load(p['id'])['production']['scheduling']['densePrewarm']
            self.assertTrue(marker['eligible'])
            self.assertTrue(marker['startupEnabled'])
            self.assertGreaterEqual(marker['hiddenAssemblySeconds'], 0)
            self.assertGreaterEqual(marker['eligibilityProofSeconds'], 0)
            self.assertEqual(len(marker['proofSignature']), 64)
        finally:
            release.set()
            overlap.close('FAILED')
        self.assertEqual(rental.stops, 1)

    def test_failed_final_reference_proof_stops_joined_startup_without_any_image(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
        try:
            overlap.main.maybe_dense_prewarm(p, ch, staged, p['characters'], expected)
            self.assertTrue(entered.wait(3))
            Image.new('RGB', (16, 16), 'red').save(self.service.store.folder(p['id'])/'canonical.png')
            with self.assertRaisesRegex(ValueError, 'changed'):
                overlap.main.mark_dense_prewarm_assembly_ready(p['id'])
            with ThreadPoolExecutor(max_workers=1) as pool:
                close = pool.submit(overlap.close, 'FAILED')
                self.assertTrue(overlap.dense_prewarm.abort.wait(2))
                self.assertEqual(rental.stops, 0)
                release.set()
                close.result(timeout=4)
            self.assertFalse(overlap.dense_preflight_complete)
            self.assertEqual(self.provider.requests, [])
            self.assertEqual(rental.starts, 1)
            self.assertEqual(rental.stops, 1)
        finally:
            release.set()

    def test_opt_out_records_measured_eligibility_without_spending_or_changing_ordinary_gate(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared(False)
        try:
            overlap.main.maybe_dense_prewarm(p, ch, staged, p['characters'], expected)
            overlap.main.mark_dense_prewarm_assembly_ready(p['id'])
            overlap.main.prepare_dense_image_phase(p['id'])
            self.assertIsNone(overlap.dense_prewarm)
            self.assertEqual(rental.starts, 0)
            self.assertTrue(overlap.dense_preflight_complete)
        finally:
            release.set()
            overlap.close('COMPLETE')

    def test_proposed_final_plan_is_rejected_before_any_generation_then_startup_is_drained(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
        try:
            overlap.main.maybe_dense_prewarm(p, ch, staged, p['characters'], expected)
            self.assertTrue(entered.wait(3))
            self.service.store.mutate(p['id'], lambda q:q['chapters'][0].update(proposedPlan={'scenes': []}))
            with self.assertRaisesRegex(ValueError, 'accept every'):
                overlap.main.prepare_dense_image_phase(p['id'])
            self.assertFalse(overlap.dense_preflight_complete)
            self.assertEqual(self.provider.requests, [])
        finally:
            release.set()
            overlap.close('FAILED')
        self.assertEqual(rental.stops, 1)

    def test_expired_optional_window_then_mark_final_preflight_and_ordinary_startup(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
        stopped = rental.stop
        def stop(reason):
            stopped(reason)
            rental.state['spentByProject'] = {p['id']: .02}
        rental.stop = stop
        try:
            overlap.main.maybe_dense_prewarm(p, ch, staged, p['characters'], expected)
            self.assertTrue(entered.wait(3))
            release.set();overlap.dense_prewarm.join()
            overlap.dense_prewarm.clock = lambda: overlap.dense_prewarm.started+121
            overlap.main.mark_dense_prewarm_assembly_ready(p['id'])
            self.assertTrue(overlap.dense_prewarm.retired)
            self.assertIsNone(overlap.dense_prewarm_proof)
            self.assertEqual(rental.state['spentByProject'][p['id']], .02)
            # The retired future is deliberately retained as a no-reprewarm
            # marker, but cannot make the normal final gate validate None.
            overlap.main.prepare_dense_image_phase(p['id'])
            self.assertTrue(overlap.dense_preflight_complete)
            overlap.main.ensure_production_gpu()
            self.assertEqual(rental.starts, 2)
            self.assertEqual(self.provider.requests, [])
            marker = self.service.store.load(p['id'])['production']['scheduling']['densePrewarm']
            self.assertFalse(marker['eligible']);self.assertTrue(marker['retired'])
            rental.stop('ordinary completion')
        finally:
            release.set();overlap.close('COMPLETE')

    def test_abort_escapes_real_paused_service_and_lane_gates_without_unpausing(self):
        for lane_gate in (False, True):
            overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
            paused_inside = threading.Event()
            original = overlap.main.gate if lane_gate else self.service.gate
            def gate(*args, **kwargs):
                paused_inside.set()
                return original(*args, **kwargs)
            rental.gate = gate
            self.service.paused = True
            startup = Startup(rental, lambda *a: None)
            try:
                startup.start();self.assertTrue(entered.wait(2));release.set()
                self.assertTrue(paused_inside.wait(2))
                self.assertFalse(self.service.cancel)
                with ThreadPoolExecutor(max_workers=1) as pool:
                    closing = pool.submit(startup.close, True)
                    closing.result(timeout=2)
                self.assertTrue(self.service.paused)
                self.assertFalse(self.service.cancel)
                self.assertEqual(rental.stops, 1)
                self.assertEqual(self.provider.requests, [])
            finally:
                self.service.paused = False
                with self.service.cv:self.service.cv.notify_all()
                release.set();startup.close(True);overlap.close('FAILED')

    def test_independent_optional_bound_retires_while_planning_is_paused(self):
        overlap, rental, entered, release, p, ch, staged, expected = self.prepared()
        rental.gate = self.service.gate
        self.service.paused = True
        startup = Startup(rental, lambda *a: None, maximum_assembly_seconds=.05)
        try:
            startup.start();self.assertTrue(entered.wait(2));release.set()
            self.assertTrue(startup.closed_event.wait(2), 'Independent optional bound failed to retire paused startup')
            self.assertTrue(startup.retired)
            self.assertEqual(rental.state['status'], 'STOPPED')
            self.assertTrue(self.service.paused)
            self.assertFalse(self.service.cancel)
            self.assertEqual(self.provider.requests, [])
        finally:
            self.service.paused = False
            with self.service.cv:self.service.cv.notify_all()
            release.set();startup.close(True);overlap.close('FAILED')


if __name__ == '__main__': unittest.main()
