import base64
import io
import json
import math
import os
from pathlib import Path
import shutil
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from voice_lab import MODELS, VoiceLab, WorkerClient, WorkerNotFound, WorkerUnavailable


def pcm(seconds=2):
    stream = io.BytesIO()
    with wave.open(stream, 'wb') as audio:
        audio.setparams((1, 2, 16000, 0, 'NONE', 'NONE'))
        audio.writeframes(b''.join(struct.pack('<h', int(4000 * math.sin(i * 2 * math.pi * 220 / 16000)))
                                   for i in range(int(seconds * 16000))))
    return stream.getvalue()


class FakeWorker:
    def __init__(self):
        self.jobs = {}
        self.submits = []
        self.offline = False
        self.ambiguous = False
        self.wav = pcm()

    def request(self, path, body=None, binary=False):
        if self.offline:
            raise WorkerUnavailable('Private voice worker is offline.')
        if path == '/health':
            return {'ready': True, 'activeModel': 'voxcpm2', 'startupSeconds': 9,
                    'models': [{'id': id, 'ready': True} for id in ('drama3', 'voxcpm2', 'qwen3')],
                    'token': 'must-not-reach-browser', 'url': 'https://private.invalid'}
        if path == '/jobs':
            self.submits.append(body)
            self.jobs[body['id']] = dict(id=body['id'], status='running')
            if self.ambiguous:
                raise WorkerUnavailable('Submission timed out after acceptance.')
            return self.jobs[body['id']]
        id = path.split('/')[2]
        if id not in self.jobs:
            raise WorkerNotFound('Missing')
        if path.endswith('/audio'):
            return self.wav
        if path.endswith('/cancel'):
            self.jobs[id]['status'] = 'cancelled'
        return self.jobs[id]


class VoiceLabTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.worker = FakeWorker()
        self.ffmpeg = os.environ.get('QT_TEST_FFMPEG') or shutil.which('ffmpeg')
        if not self.ffmpeg:
            try:
                import imageio_ffmpeg
                self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            except (ImportError, RuntimeError):
                pass
        self.config = {'ffmpeg': self.ffmpeg or 'mocked-output-does-not-encode', 'voiceLabHourlyUSD': 3.6}
        self.starter = patch.object(VoiceLab, '_start')
        self.starter.start()
        self.lab = VoiceLab(self.temp.name, self.config, self.worker)

    def tearDown(self):
        self.lab.close()
        self.starter.stop()
        self.temp.cleanup()

    def body(self, **change):
        return dict(model='voxcpm2', mode='default', text='A quiet test.', language='Auto', parameters={}) | change

    def reference(self, **change):
        if not self.ffmpeg:
            self.skipTest('FFmpeg not installed')
        return self.lab.reference(dict(audioBase64=base64.b64encode(pcm()).decode(), name='Reference',
                                       transcript='A quiet test.', rightsConfirmed=True) | change)

    def complete(self, id):
        self.worker.jobs[id].update(status='completed', metrics=dict(generationSeconds=4, loadSeconds=7, startupSeconds=9))

    def test_catalog_keeps_three_distinct_candidates_and_hides_worker_secrets(self):
        snapshot = self.lab.snapshot()
        self.assertEqual([m['id'] for m in snapshot['models']], ['drama3', 'voxcpm2', 'qwen3'])
        self.assertEqual(snapshot['worker']['model'], 'voxcpm2')
        self.assertNotIn('must-not-reach-browser', json.dumps(snapshot))
        self.assertNotIn('private.invalid', json.dumps(snapshot))
        self.assertNotIn('Cantonese', next(m for m in MODELS if m['id'] == 'qwen3')['languages'])
        self.assertEqual(snapshot['codec']['bitrateKbps'], 128)

    def test_offline_is_blocked_before_job_creation(self):
        self.worker.offline = True
        with self.assertRaisesRegex(WorkerUnavailable, 'offline'):
            self.lab.enqueue(self.body())
        self.assertEqual(self.lab.snapshot()['jobs'], [])

    def test_one_prepared_model_does_not_advertise_others_as_ready(self):
        original = self.worker.request
        def request(path, body=None, binary=False):
            result = original(path, body, binary)
            if path == '/health':
                result['models'] = [{'id': 'qwen3', 'ready': True}]
            return result
        self.worker.request = request
        models = {m['id']: m for m in self.lab.snapshot()['models']}
        self.assertTrue(models['qwen3']['ready'])
        self.assertFalse(models['voxcpm2']['ready'])
        with self.assertRaisesRegex(WorkerUnavailable, 'not prepared'):
            self.lab.enqueue(self.body())

    def test_only_verified_modes_parameters_and_language_are_accepted(self):
        for body in [self.body(model='s2-pro'), self.body(mode='unknown'), self.body(text='x' * 2501),
                     self.body(parameters={'speed': 2}), self.body(parameters={'seed': float('nan')}),
                     self.body(parameters={'inference_timesteps': 1.5}), self.body(language='Cantonese'),
                     self.body(model='qwen3', mode='design', language='English', description='')]:
            with self.subTest(body=body):
                with self.assertRaises(ValueError):
                    self.lab.enqueue(body)
        self.assertEqual(len(self.worker.submits), 0)

    def test_reference_requires_permission_and_valid_audio_and_is_stored_as_opus(self):
        with self.assertRaisesRegex(ValueError, 'permission'):
            self.reference(rightsConfirmed=False)
        with self.assertRaises(ValueError):
            self.reference(audioBase64='not base64')
        with self.assertRaises(ValueError):
            self.reference(audioBase64=base64.b64encode(b'not audio').decode())
        ref = self.reference()
        self.assertAlmostEqual(ref['durationSeconds'], 2, places=2)
        self.assertTrue((self.lab.root / (ref['id'] + '.opus')).is_file())
        self.assertEqual(list(self.lab.root.glob('*.upload')), [])
        self.assertEqual(list(self.lab.root.glob('*.wav')), [])

    def test_qwen_clone_requires_reference_transcript_and_never_accepts_path(self):
        ref = self.reference(transcript='')
        with self.assertRaisesRegex(ValueError, 'transcript'):
            self.lab.enqueue(self.body(model='qwen3', mode='clone', language='English', referenceId=ref['id']))
        with self.assertRaises(ValueError):
            self.lab.enqueue(self.body(mode='clone', referenceId='../studio-config.json'))

    def test_reference_forwarded_only_to_private_worker_and_not_stored_in_job_payload(self):
        ref = self.reference()
        job = self.lab.enqueue(self.body(mode='clone', referenceId=ref['id']))
        self.lab.tick()
        submission = self.worker.submits[0]
        self.assertEqual(submission['reference']['mime'], 'audio/ogg')
        self.assertEqual(submission['reference']['transcript'], 'A quiet test.')
        self.assertNotIn('audioBase64', json.dumps(self.lab.job(job['id'])))

    def test_unknown_submission_reconciles_original_id_without_duplicate(self):
        self.worker.ambiguous = True
        job = self.lab.enqueue(self.body())
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'UNKNOWN')
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'RUNNING')
        self.assertEqual(len(self.worker.submits), 1)
        self.assertEqual(self.worker.submits[0]['id'], job['id'])

    def test_cancel_during_ambiguous_submission_preserves_cancel_request(self):
        job = self.lab.enqueue(self.body())
        original = self.worker.request
        def request(path, body=None, binary=False):
            result = original(path, body, binary)
            if path == '/jobs':
                self.lab.cancel(job['id'])
                raise WorkerUnavailable('Response was lost after cancellation.')
            return result
        self.worker.request = request
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'CANCEL_REQUESTED')
        self.lab.tick()
        self.assertEqual(self.worker.jobs[job['id']]['status'], 'cancelled')
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'CANCELLED')
        self.assertEqual(len(self.worker.submits), 1)

    def test_restart_preserves_pending_identity_and_does_not_resubmit(self):
        job = self.lab.enqueue(self.body())
        self.lab._update(job['id'], 'SUBMITTING')
        self.worker.jobs[job['id']] = dict(id=job['id'], status='running')
        self.lab.close()
        self.lab = VoiceLab(self.temp.name, self.config, self.worker)
        self.assertEqual(self.lab.job(job['id'])['status'], 'UNKNOWN')
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'RUNNING')
        self.assertEqual(self.worker.submits, [])

    def test_missing_remote_identity_is_not_automatically_resubmitted(self):
        job = self.lab.enqueue(self.body())
        self.lab._update(job['id'], 'UNKNOWN')
        self.lab.tick()
        self.assertEqual(self.lab.job(job['id'])['status'], 'UNKNOWN')
        self.assertIn('not be resubmitted', self.lab.job(job['id'])['error'])
        self.assertEqual(self.worker.submits, [])

    def test_cancellation_never_becomes_completed_and_queued_jobs_never_submit(self):
        queued = self.lab.enqueue(self.body())
        self.assertEqual(self.lab.cancel(queued['id'])['status'], 'CANCELLED')
        self.lab.tick()
        self.assertEqual(self.worker.submits, [])
        running = self.lab.enqueue(self.body())
        self.lab.tick()
        self.assertEqual(self.lab.cancel(running['id'])['status'], 'CANCEL_REQUESTED')
        self.complete(running['id'])
        self.lab.tick()
        self.assertEqual(self.lab.job(running['id'])['status'], 'CANCELLED')
        self.assertFalse((self.lab.root / (running['id'] + '.opus')).exists())

    def test_opus_preview_and_costs_use_measured_audio_and_never_claim_total_bill(self):
        if not self.ffmpeg:
            self.skipTest('FFmpeg not installed')
        job = self.lab.enqueue(self.body())
        self.lab.tick()
        self.complete(job['id'])
        self.lab.tick()
        result = self.lab.job(job['id'])
        self.assertEqual(result['status'], 'COMPLETED')
        self.assertTrue(result['audioReady'])
        self.assertEqual(self.lab.audio(job['id']).suffix, '.opus')
        self.assertAlmostEqual(result['metrics']['audioSeconds'], 2, places=2)
        self.assertAlmostEqual(result['metrics']['warm30SecondsComputeUSD'], .06, places=4)
        self.assertAlmostEqual(result['metrics']['modelLoad30SecondsComputeUSD'], .067, places=4)
        self.assertAlmostEqual(result['metrics']['cold30SecondsComputeUSD'], .076, places=4)
        self.assertIn('Excludes downloads/setup', result['metrics']['costBasis'])
        self.assertEqual(list(self.lab.root.glob('*.wav')), [])
        selected = self.lab.select(dict(model='voxcpm2', jobId=job['id']))
        self.assertEqual(selected, self.lab.snapshot()['selected'])

    def test_costs_remain_unknown_without_rate_or_measured_durations(self):
        self.config.pop('voiceLabHourlyUSD')
        metrics = self.lab._metrics({}, 2)
        self.assertIsNone(metrics['warm30SecondsComputeUSD'])
        self.assertIsNone(metrics['cold30SecondsComputeUSD'])
        self.config['voiceLabHourlyUSD'] = 3.6
        metrics = self.lab._metrics(dict(generationSeconds=2, loadSeconds=3), 2)
        self.assertIsNotNone(metrics['warm30SecondsComputeUSD'])
        self.assertIsNone(metrics['cold30SecondsComputeUSD'])

    def test_non_audio_worker_output_is_rejected_without_exposing_its_contents(self):
        job = self.lab.enqueue(self.body())
        self.lab.tick()
        self.complete(job['id'])
        self.worker.wav = b'private unexpected response contents'
        self.lab.tick()
        result = self.lab.job(job['id'])
        self.assertEqual(result['status'], 'FAILED')
        self.assertFalse(result['audioReady'])
        self.assertNotIn('private unexpected', json.dumps(result))

    def test_private_client_rejects_remote_urls_and_credentials_never_reach_them(self):
        for url in ('https://example.com', 'http://127.0.0.1@evil.com', 'http://localhost/path',
                    'http://127.0.0.1?key=secret', 'file:///private'):
            with self.subTest(url=url), patch('voice_lab.build_opener') as opener:
                with self.assertRaises(WorkerUnavailable):
                    WorkerClient({'voiceLabWorkerURL': url}).request('/health')
                opener.assert_not_called()


class VoiceLabHTTPTests(unittest.TestCase):
    def setUp(self):
        import server_test
        self.bridge = server_test.BridgeTest()
        self.bridge.setUp()

    def tearDown(self):
        lab = self.bridge.server.RequestHandlerClass.voice_lab_service
        if lab:
            lab.close()
        self.bridge.tearDown()

    def test_all_lab_routes_require_pairing_before_initialization(self):
        for path, body in [('/studio/voice-lab', None), ('/studio/voice-lab/audio?id=' + 'a' * 32, None),
                           ('/studio/voice-lab/reference', {}), ('/studio/voice-lab/jobs', {}),
                           ('/studio/voice-lab/cancel', {}), ('/studio/voice-lab/select', {})]:
            with self.subTest(path=path):
                self.assertEqual(self.bridge.request(path, body, key='wrong')[0], 401)
        self.assertIsNone(self.bridge.server.RequestHandlerClass.voice_lab_service)
        code, _, raw = self.bridge.request('/studio/voice-lab')
        self.assertEqual(code, 200)
        self.assertFalse(json.loads(raw)['worker']['ready'])
        self.assertFalse(self.bridge.engine.calls)

    def test_runtime_configuration_hides_lab_token_and_tunnel_address(self):
        service = self.bridge.server.RequestHandlerClass.studio_service
        with patch.dict(service.config, {'voiceLabWorkerURL': 'http://127.0.0.1:9786',
                    'voiceLabWorkerTokenFile': 'C:/private/worker-token', 'voiceLabWorkerToken': 'private-token'}):
            code, _, raw = self.bridge.request('/studio/config')
        self.assertEqual(code, 200)
        self.assertNotIn(b'9786', raw)
        self.assertNotIn(b'worker-token', raw)
        self.assertNotIn(b'private-token', raw)

    def test_private_audio_route_uses_validated_job_identity(self):
        code, _, raw = self.bridge.request('/studio/voice-lab/audio?id=../../studio-config.json')
        self.assertEqual(code, 400)
        self.assertNotIn(b'runpodApiKey', raw)


class VoiceLabWorkerHTTPIntegrationTests(unittest.TestCase):
    """Real paired HTTP -> persistent worker HTTP -> codec -> private playback.

    Only the model inference is replaced by a generated PCM fixture. This does
    not prove model inference, model downloads, GPU readiness, or provider cost.
    """
    def setUp(self):
        import server_test
        from http.server import ThreadingHTTPServer
        from voice_lab_worker.service import Store, handler
        self.ffmpeg = os.environ.get('QT_TEST_FFMPEG') or shutil.which('ffmpeg')
        if not self.ffmpeg:
            self.skipTest('FFmpeg required for end-to-end Opus preview validation')
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = Store(root / 'worker')
        store = self.store
        class SupervisorFixture:
            def prepared(self, model):
                return model in ('drama3', 'voxcpm2', 'qwen3')
            def health(self):
                return {'ready': True, 'models': [{'id': id, 'ready': True} for id in ('drama3', 'voxcpm2', 'qwen3')],
                        'activeModel': None, 'startupSeconds': None}
            def cancel(self, id):
                store.update(id, 'cancelled')
                return store.get(id)
        token = 'integration-test-only-private-token'
        self.worker_server = ThreadingHTTPServer(('127.0.0.1', 0), handler(self.store, SupervisorFixture(), token))
        self.http_thread = threading.Thread(target=self.worker_server.serve_forever, daemon=True)
        self.http_thread.start()
        self.bridge = server_test.BridgeTest()
        self.bridge.setUp()
        token_file = root / 'private-worker-token'
        token_file.write_text(token)
        self.bridge.server.RequestHandlerClass.studio_service.config.update(
            voiceLabWorkerURL='http://127.0.0.1:' + str(self.worker_server.server_port),
            voiceLabWorkerTokenFile=str(token_file), voiceLabHourlyUSD=3.6, ffmpeg=self.ffmpeg)
        self.stop = threading.Event()
        self.errors = []
        self.received = []
        def inference_fixture():
            while not self.stop.wait(.01):
                try:
                    body = self.store.next()
                    if not body:
                        continue
                    self.received.append(body)
                    folder = self.store.root / 'jobs' / body['id']
                    folder.mkdir(parents=True, exist_ok=True)
                    (folder / 'audio.wav').write_bytes(pcm())
                    self.store.update(body['id'], 'completed', dict(generationSeconds=4, loadSeconds=7, startupSeconds=None))
                    lab = self.bridge.server.RequestHandlerClass.voice_lab_service
                    if lab:
                        lab.wake.set()
                except Exception as error:
                    self.errors.append(type(error).__name__)
        self.inference_thread = threading.Thread(target=inference_fixture, daemon=True)
        self.inference_thread.start()

    def tearDown(self):
        self.stop.set()
        self.inference_thread.join(2)
        lab = self.bridge.server.RequestHandlerClass.voice_lab_service
        if lab:
            lab.close()
        self.bridge.tearDown()
        self.worker_server.shutdown()
        self.worker_server.server_close()
        self.http_thread.join(2)
        self.store.db.close()
        self.temp.cleanup()

    def test_three_candidates_use_exact_worker_contract_and_private_opus_outputs(self):
        request = self.bridge.request
        self.assertEqual(request('/studio/voice-lab', key='wrong')[0], 401)
        code, _, raw = request('/studio/voice-lab')
        self.assertEqual(code, 200)
        self.assertTrue(all(m['ready'] for m in json.loads(raw)['models']))
        code, _, raw = request('/studio/voice-lab/reference', dict(name='Permitted fixture', transcript='A quiet test.',
            rightsConfirmed=True, audioBase64=base64.b64encode(pcm()).decode()))
        self.assertEqual(code, 200, raw)
        reference = json.loads(raw)
        jobs = []
        for model, mode, language in [('drama3', 'default', 'Auto'), ('voxcpm2', 'design', 'Auto'), ('qwen3', 'clone', 'English')]:
            code, _, raw = request('/studio/voice-lab/jobs', dict(model=model, mode=mode, language=language,
                text='A quiet test.', description='A calm, clear narrator.', referenceId=reference['id'], parameters={}))
            self.assertEqual(code, 200, raw)
            jobs.append(json.loads(raw))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            results = [json.loads(request('/studio/voice-lab/job?id=' + j['id'])[2]) for j in jobs]
            if all(j['status'] == 'COMPLETED' for j in results):
                break
            time.sleep(.025)
        self.assertEqual([r['status'] for r in results], ['COMPLETED'] * 3, results)
        self.assertEqual(self.errors, [])
        self.assertEqual([r['model'] for r in self.received], ['drama3', 'voxcpm2', 'qwen3'])
        self.assertEqual(self.received[2]['reference']['transcript'], 'A quiet test.')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 3)
        for result in results:
            code, headers, raw = request('/studio/voice-lab/audio?id=' + result['id'])
            self.assertEqual(code, 200)
            self.assertTrue(headers['Content-Type'].startswith('audio/'))
            self.assertTrue(raw.startswith(b'OggS'))
            self.assertAlmostEqual(result['metrics']['audioSeconds'], 2, places=2)
            self.assertAlmostEqual(result['metrics']['modelLoad30SecondsComputeUSD'], .067, places=4)
            self.assertIsNone(result['metrics']['cold30SecondsComputeUSD'])
            self.assertEqual(request('/studio/voice-lab/audio?id=' + result['id'], key='wrong')[0], 401)
        code, _, raw = request('/studio/voice-lab/select', dict(model='qwen3', jobId=results[2]['id']))
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(raw)['jobId'], results[2]['id'])
        config = self.bridge.server.RequestHandlerClass.studio_service.config
        self.assertNotIn('voiceEngine', config)
        snapshot = json.loads(request('/studio/voice-lab')[2])
        self.assertNotIn('integration-test-only-private-token', json.dumps(snapshot))
        self.assertNotIn('voiceLabWorkerURL', snapshot)


if __name__ == '__main__':
    unittest.main()
