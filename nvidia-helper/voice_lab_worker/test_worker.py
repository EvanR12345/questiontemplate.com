"""CPU-only worker contract tests. No model download, paid requests or GPU import."""
import base64
import io
import json
from pathlib import Path
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
import wave
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import service


def body(**updates):
    value = {'id': 'audition-12345678', 'model': 'voxcpm2', 'mode': 'default', 'text': 'A short audition.', 'language': 'auto', 'parameters': {}}
    value.update(updates)
    return value


class PersistentJobs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = service.Store(self.temp.name)

    def tearDown(self):
        self.store.db.close()
        self.temp.cleanup()

    def test_exact_repeat_is_idempotent_and_changed_input_conflicts(self):
        first = self.store.submit(body())
        self.assertEqual(first, self.store.submit(body()))
        with self.assertRaises(service.Conflict):
            self.store.submit(body(text='Different text'))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)

    def test_inflight_restart_is_failed_and_never_silently_requeued(self):
        self.store.submit(body())
        self.assertEqual(self.store.next()['id'], body()['id'])
        self.store.db.close()
        self.store = service.Store(self.temp.name)
        job = self.store.get(body()['id'])
        self.assertEqual(job['status'], 'failed')
        self.assertIn('restarted', job['error'])
        self.assertIsNone(self.store.next())

    def test_queued_jobs_survive_restart(self):
        self.store.submit(body())
        self.store.db.close()
        self.store = service.Store(self.temp.name)
        self.assertEqual(self.store.next()['id'], body()['id'])

    def test_cancellation_wins_over_late_completion(self):
        self.store.submit(body())
        self.store.update(body()['id'], 'cancelled')
        self.store.update(body()['id'], 'completed', {'audioSeconds': 30})
        self.assertEqual(self.store.get(body()['id'])['status'], 'cancelled')
        self.assertIsNone(self.store.next())

    def test_invalid_input_has_no_queue_side_effects(self):
        bad = [body(id='../unsafe-name'), body(parameters={'seed': float('nan')}), body(parameters={'unknown': 5}),
               body(model='qwen3', mode='default'), body(model='drama3', mode='clone', reference={'audioBase64': '!!!'}),
               body(model='qwen3', mode='clone', reference={'audioBase64': base64.b64encode(b'0' * 32).decode(), 'transcript': ''}),
               body(parameters={'seed': 1.5}), body(language='cantonese'), body(text='x' * 2501)]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.store.submit(value)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)

    def test_worker_metric_uses_finished_audio_and_real_wall_time(self):
        class FakeSupervisor(service.Supervisor):
            def start_model(inner, model):
                def write(encoded):
                    payload = json.loads(encoded)
                    with wave.open(str(Path(payload['directory']) / 'audio.wav'), 'wb') as audio:
                        audio.setnchannels(1)
                        audio.setsampwidth(2)
                        audio.setframerate(100)
                        audio.writeframes(b'\0\0' * 3000)
                    inner.events.put({'event': 'done', 'id': payload['id'],
                                      'metrics': {'loadSeconds': .2, 'startupSeconds': None, 'generationSeconds': .1}})
                inner.process = types.SimpleNamespace(stdin=types.SimpleNamespace(write=write, flush=lambda: None))
        self.store.submit(body())
        supervisor = FakeSupervisor(self.temp.name, self.store, 3.6)
        with patch.object(service.time, 'monotonic', side_effect=[10.0, 10.0, 10.0, 12.0]):
            supervisor.run_one(self.store.next())
        result = self.store.get(body()['id'])
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['metrics']['audioSeconds'], 30)
        self.assertEqual(result['metrics']['workerWallSeconds'], 2)
        self.assertAlmostEqual(result['metrics']['workerEstimatedUSD'], result['metrics']['workerWallSeconds'] / 1000)
        self.assertIn('excludes GPU allocation', result['metrics']['costBasis'])

    def test_health_never_claims_unprepared_models_ready(self):
        status = service.Supervisor(self.temp.name, self.store).health()
        self.assertFalse(status['ready'])
        self.assertEqual({m['id'] for m in status['models']}, {'drama3', 'voxcpm2', 'qwen3'})
        self.assertIsNone(status['hourlyUSD'])


class HTTPContract(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = service.Store(self.temp.name)
        self.supervisor = service.Supervisor(self.temp.name, self.store)
        self.supervisor.prepared = lambda _: True
        self.token = 'private-test-token-' + 'a' * 32
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), service.handler(self.store, self.supervisor, self.token))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = 'http://127.0.0.1:' + str(self.server.server_port)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.store.db.close()
        self.temp.cleanup()

    def request(self, path, payload=None, token=True):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + self.token
        request = urllib.request.Request(self.url + path, None if payload is None else json.dumps(payload).encode(), headers)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_auth_covers_health_jobs_audio_and_cancel(self):
        for path, payload in [('/health', None), ('/jobs', body()), ('/jobs/audition-12345678', None),
                              ('/jobs/audition-12345678/audio', None), ('/jobs/audition-12345678/cancel', {})]:
            with self.subTest(path=path):
                self.assertEqual(self.request(path, payload, token=False)[0], 401)

    def test_submit_poll_cancel_audio_state_and_conflict(self):
        self.assertEqual(self.request('/jobs', body())[0], 200)
        self.assertEqual(self.request('/jobs', body(text='Changed'))[0], 409)
        self.assertEqual(self.request('/jobs/audition-12345678/audio')[0], 409)
        self.assertEqual(self.request('/jobs/audition-12345678/cancel', {})[1]['status'], 'cancelled')
        self.assertEqual(self.request('/jobs/audition-12345678')[1]['status'], 'cancelled')
        self.assertEqual(self.request('/jobs/../../secret/audio')[0], 404)

    def test_unprepared_submission_has_no_job(self):
        self.supervisor.prepared = lambda _: False
        self.assertEqual(self.request('/jobs', body())[0], 503)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM jobs').fetchone()[0], 0)


if __name__ == '__main__':
    unittest.main()
