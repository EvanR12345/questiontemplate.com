import json
import struct
import threading
import unittest
import tempfile
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from server import make_handler
from studio_data import new_project

KEY = 'a' * 64
ORIGIN = 'https://questiontemplate.com'


class FakeEngine:
    def __init__(self):
        self.lock = threading.Lock()
        self.calls = []

    def health(self):
        return {'protocol': 1, 'backend': 'cuda', 'gpu': 'Test GPU', 'sampleRate': 24000, 'vocab': {'h': 1}}

    def prepare(self, voice):
        self.calls.append(('prepare', voice))

    def synthesize(self, phonemes, voice, speed):
        self.calls.append((phonemes, voice, speed))
        return struct.pack('<fff', -.5, 0, .5)


class BridgeTest(unittest.TestCase):
    def test_trace_export_is_authenticated_and_does_not_expose_project_text(self):
        service = self.server.RequestHandlerClass.studio_service
        project = service.store.save(new_project())
        with service.trace.span(project['id'], 'Test stage', {'prompt': 'PRIVATE STORY'}):
            pass
        path = '/studio/trace?project=' + project['id']
        self.assertEqual(self.request(path=path, key='bad')[0], 401)
        code, _, raw = self.request(path=path)
        self.assertEqual(code, 200)
        self.assertNotIn(b'PRIVATE STORY', raw)
        self.assertEqual(json.loads(raw)['spans'][0]['stage'], 'Test stage')

    def setUp(self):
        self.engine = FakeEngine()
        self.directory = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.engine, KEY, self.directory.name))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.RequestHandlerClass.image_queue.close()
        self.server.RequestHandlerClass.studio_service.close()
        self.server.server_close()
        self.thread.join()
        self.directory.cleanup()

    def request(self, path='/health', body=None, key=KEY, origin=ORIGIN, method=None, extra_headers=None):
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        headers = {'Origin': origin, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
        headers.update(extra_headers or {})
        client.request(method or ('GET' if body is None else 'POST'), path, None if body is None else json.dumps(body), headers)
        response = client.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        client.close()
        return result

    def test_authentication_and_origins(self):
        self.assertEqual(self.request(key='bad')[0], 401)
        code, headers, _ = self.request(origin='https://unrelated.example')
        self.assertEqual(code, 403)
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        self.assertEqual(self.request()[0], 200)
        self.assertFalse(self.engine.calls)

    def test_preflight_and_cuda_metadata(self):
        code, headers, _ = self.request(method='OPTIONS', key='')
        self.assertEqual(code, 200)
        self.assertEqual(headers['Access-Control-Allow-Origin'], ORIGIN)
        self.assertEqual(headers['Access-Control-Allow-Private-Network'], 'true')
        code, _, data = self.request()
        self.assertEqual(json.loads(data)['backend'], 'cuda')

    def test_exact_binary_samples_and_voice(self):
        code, headers, data = self.request('/synthesize', {'phonemes': 'h', 'voice': 'am_michael', 'speed': 1})
        self.assertEqual(code, 200)
        self.assertEqual(headers['X-Sample-Rate'], '24000')
        self.assertEqual(struct.unpack('<fff', data), (-.5, 0, .5))
        self.assertEqual(self.engine.calls, [('h', 'am_michael', 1)])

    def test_reject_truncation_path_traversal_and_invalid_speed(self):
        for body in [
            {'phonemes': 'h' * 279, 'voice': 'am_michael', 'speed': 1},
            {'phonemes': 'h', 'voice': '../private', 'speed': 1},
            {'phonemes': 'h', 'voice': 'am_michael', 'speed': True},
            {'phonemes': 'h', 'voice': 'am_michael', 'speed': 11},
        ]:
            self.assertEqual(self.request('/synthesize', body)[0], 400)
        client = HTTPConnection('127.0.0.1', self.server.server_port, timeout=2)
        client.request('POST', '/synthesize', b'', {'Origin': ORIGIN, 'Authorization': 'Bearer '+KEY, 'Content-Length': str(32*1024*1024+1)})
        self.assertEqual(client.getresponse().status, 413)
        client.close()
        self.assertFalse(self.engine.calls)

    def test_one_inference_at_a_time(self):
        self.engine.lock.acquire()
        try:
            self.assertEqual(self.request('/image/generate', {'prompt': 'test'})[0], 409)
            self.assertEqual(self.request()[0], 200)
        finally:
            self.engine.lock.release()
        self.assertEqual(self.request('/prepare', {'voice': 'af_heart'})[0], 200)

    def test_aac_audition_media_ticket_serves_saved_bytes(self):
        store = self.server.RequestHandlerClass.studio_service.store
        p = store.save(new_project())
        store.asset(p['id'], 'audition.m4a').write_bytes(b'saved-aac-audition')
        code, _, body = self.request('/studio/media-link', {'project':p['id'], 'path':'audition.m4a'})
        self.assertEqual(code, 200)
        ticket = json.loads(body)['url'].split('/studio/media?',1)[1]
        code, headers, content = self.request('/studio/media?'+ticket)
        self.assertEqual(code, 200)
        self.assertTrue(headers['Content-Type'].startswith('audio/'))
        self.assertEqual(content, b'saved-aac-audition')
        self.assertEqual(self.request('/studio/media-link', {'project':p['id'], 'path':'../../private.m4a'})[0],400)
        self.assertFalse(self.engine.calls)

    def test_parallel_previews_reuse_one_ticket_and_expiry_still_applies(self):
        store = self.server.RequestHandlerClass.studio_service.store
        project = store.save(new_project())
        store.asset(project['id'], 'preview.mp4').write_bytes(b'saved-video')
        def link(_):
            return json.loads(self.request('/studio/media-link', {'project':project['id'], 'path':'preview.mp4'})[2])
        with ThreadPoolExecutor(4) as pool:links=list(pool.map(link, range(16)))
        self.assertEqual(len({item['url'] for item in links}), 1)
        ticket_path='/studio/media?'+links[0]['url'].split('/studio/media?',1)[1]
        self.assertEqual(self.request(ticket_path)[2], b'saved-video')
        with patch('server.time.time', return_value=links[0]['expires']+1):
            self.assertEqual(self.request(ticket_path)[0], 401)
            replacement=link(0)
            self.assertNotEqual(replacement['url'], links[0]['url'])

    def test_unexpected_errors_preserve_diagnostics_without_credentials_in_logs(self):
        secret='sk-proj-'+ 'examplecredential' * 3
        log=io.StringIO()
        with patch.object(self.engine, 'prepare', side_effect=RuntimeError('Upstream HTTP 429; Bearer '+secret)), redirect_stdout(log):
            code, _, raw=self.request('/prepare', {'voice':'af_heart'})
        self.assertEqual(code, 503)
        self.assertIn(b'HTTP 429', raw)
        self.assertNotIn(secret.encode(), raw)
        self.assertNotIn(secret, log.getvalue())

    def test_media_seeks_reject_bad_ranges_without_breaking_playback(self):
        store = self.server.RequestHandlerClass.studio_service.store
        project = store.save(new_project())
        store.asset(project['id'], 'seek.mp4').write_bytes(b'0123456789')
        _, _, body = self.request('/studio/media-link', {'project':project['id'], 'path':'seek.mp4'})
        path = '/studio/media?' + json.loads(body)['url'].split('/studio/media?', 1)[1]
        for value in ('bytes=-', 'bytes=-0', 'bytes=10-', 'bytes=8-2',
                      'bytes=0-1,4-5', 'bytes=' + '9' * 5000 + '-'):
            with self.subTest(value=value[:32]):
                self.assertEqual(self.request(path, extra_headers={'Range':value})[0], 416)
        for value, expected, content_range in (
            ('bytes=2-4', b'234', 'bytes 2-4/10'),
            ('bytes=-3', b'789', 'bytes 7-9/10'),
            ('bytes=7-', b'789', 'bytes 7-9/10'),
            ('bytes=8-99', b'89', 'bytes 8-9/10'),
        ):
            with self.subTest(value=value):
                code, headers, content = self.request(path, extra_headers={'Range':value})
                self.assertEqual((code, content), (206, expected))
                self.assertEqual(headers['Content-Range'], content_range)
        self.assertEqual(self.request(path)[2], b'0123456789')
        self.assertFalse(self.engine.calls)


if __name__ == '__main__':
    unittest.main()
