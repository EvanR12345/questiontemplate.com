import json
import struct
import threading
import unittest
import tempfile
import io
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from server import make_handler, main
from studio_data import new_project

KEY = 'a' * 64
ORIGIN = 'https://questiontemplate.com'


class StartupTest(unittest.TestCase):
    def check_startup(self, hidden):
        with tempfile.TemporaryDirectory() as directory:
            key_path = Path(directory) / '.pairing-key'
            key_path.write_text(KEY)
            output = io.StringIO()
            with patch('server.__file__', str(Path(directory) / 'server.py')), \
                    patch('server.CudaEngine') as engine, \
                    patch('server.make_handler'), \
                    patch('server.ThreadingHTTPServer'), \
                    patch('subprocess.run'), \
                    patch('server.webbrowser.open') as browser, \
                    patch.dict('os.environ', {'QT_NO_BROWSER': '1' if hidden else '0'}), \
                    redirect_stdout(output):
                engine.return_value.gpu = 'Test GPU'
                main()
            self.assertEqual(key_path.read_text(), KEY)
            if hidden:
                self.assertNotIn(KEY, output.getvalue())
                self.assertNotIn('#native=', output.getvalue())
                browser.assert_not_called()
            else:
                self.assertIn('#native=' + KEY, output.getvalue())
                browser.assert_called_once_with('https://questiontemplate.com/studio.html#native=' + KEY)

    def test_hidden_startup_keeps_pairing_key_out_of_redirected_output(self):
        self.check_startup(True)

    def test_interactive_startup_preserves_pairing_and_saved_key(self):
        self.check_startup(False)


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
    def test_production_readiness_reports_blocks_without_paid_requests(self):
        from unittest.mock import Mock
        service=self.server.RequestHandlerClass.studio_service
        p=service.store.save(new_project('Readiness only'))
        with patch.object(service,'storage',Mock(status=lambda *_:{'enabled':True,'provider':'Google Cloud Storage'})), \
             patch('studio_service.LocalQwenDirector.healthCheck',return_value={'installed':True}), \
             patch.object(service,'provider') as provider:
            code,_,raw=self.request('/studio/production-readiness?project='+p['id'])
        self.assertEqual(code,200)
        report=json.loads(raw)
        self.assertFalse(report['ready']);self.assertEqual(report['paidRequestsSubmitted'],0)
        self.assertEqual(report['voiceLocation'],'local')
        self.assertFalse(next(x for x in report['checks'] if x['name']=='chapters')['ready'])
        provider.assert_not_called()

    def test_preflight_revision_rejects_changed_story_before_queue_admission(self):
        service=self.server.RequestHandlerClass.studio_service
        p=new_project('Changed after checks');p['chapters'][0]['sourceText']='Story.'
        p=service.store.save(p);revision=p['revision']
        service.store.mutate(p['id'],lambda q:q.update(name='Later edit'))
        before=service.snapshot()['jobs']
        code,_,raw=self.request('/studio/jobs',{'project':p['id'],'kind':'produce-story',
            'options':{'preflightRevision':revision}})
        self.assertEqual(code,400)
        self.assertIn('Project changed',json.loads(raw)['error'])
        self.assertEqual(service.snapshot()['jobs'],before)

    def test_ready_cloud_project_checks_worker_without_starting_generation(self):
        from unittest.mock import Mock
        service=self.server.RequestHandlerClass.studio_service
        p=new_project('Ready project');p['chapters'][0]['sourceText']='Mira carries the key.'
        p['settings'].update(cloudImagesOnly=True,qcCheckLevel='off')
        p['settings']['image']['provider']='comfyui'
        p['settings']['director']['provider']='openai-luna'
        p['settings']['budget']={'openaiUSD':1}
        p=service.store.save(p)
        worker=Mock();worker.healthCheck.return_value={'installed':True}
        with patch.object(service,'storage',Mock(status=lambda *_:{'enabled':True,'provider':'Google Cloud Storage'})), \
             patch('studio_service.OpenAIDirector.healthCheck',return_value={'installed':True}), \
             patch.object(service,'provider',return_value=worker), \
             patch.dict(service.config,{'ffmpeg':__file__}):
            code,_,raw=self.request('/studio/production-readiness?project='+p['id']+'&overlap=true')
        report=json.loads(raw)
        self.assertEqual(code,200);self.assertTrue(report['ready'])
        self.assertEqual(report['paidRequestsSubmitted'],0)
        worker.healthCheck.assert_called_once();worker.generateImage.assert_not_called()

    def test_unavailable_cloud_worker_blocks_readiness_without_fallback_generation(self):
        from unittest.mock import Mock
        service=self.server.RequestHandlerClass.studio_service
        p=new_project('Unavailable project');p['chapters'][0]['sourceText']='Mira carries the key.'
        p['settings']['image']['provider']='comfyui';p=service.store.save(p)
        worker=Mock();worker.healthCheck.side_effect=TimeoutError('private upstream detail')
        with patch.object(service,'provider',return_value=worker):
            report=service.production_readiness(p['id'])
        self.assertFalse(report['ready']);worker.generateImage.assert_not_called()
        self.assertNotIn('private upstream detail',json.dumps(report))
        self.assertFalse(next(x for x in report['checks'] if x['name']=='images')['ready'])

    def test_cloud_attachment_link_does_not_restore_video_to_laptop(self):
        from unittest.mock import Mock
        service=self.server.RequestHandlerClass.studio_service
        storage=Mock(enabled=True,provider='Google Cloud Storage')
        storage.media_url.return_value='https://storage.googleapis.com/private/signed'
        with patch.object(service,'storage',storage),patch.object(service.store,'asset') as restore:
            code,_,raw=self.request('/studio/media-link',{'project':'pr-0123456789abcdef','path':'story.mp4','download':'Part 1.mp4'})
            self.assertEqual(code,200)
            self.assertEqual(json.loads(raw)['provider'],'Google Cloud Storage')
            storage.media_url.assert_called_once_with('pr-0123456789abcdef','story.mp4',download_name='Part 1.mp4')
            restore.assert_not_called()

    def test_new_project_reuses_selected_cloud_profile_without_old_story_or_audio_assets(self):
        service=self.server.RequestHandlerClass.studio_service
        original=new_project('Existing story')
        original['settings']['director']['provider']='openai-luna'
        original['settings']['image'].update(provider='comfyui',model='flux2-klein-4b',workflow='saved-cloud-workflow')
        original['settings']['video']['fps']=60
        original['settings'].update(cloudImagesOnly=True,qcCheckLevel='sampled')
        original['settings']['engagement'].update(outroAudioPath='old-outro.wav',outroAudioSignature='old-signature')
        original['settings']['image']['controlnets']=[{'image':'old-pose.png'}]
        original['chapters'][0]['sourceText']='Keep the existing story.'
        original['intro'].update(enabled=True,audioPath='old-intro.wav')
        original=service.store.save(original)
        code,_,raw=self.request('/studio/create',{'name':'New story','sourceProject':original['id']})
        self.assertEqual(code,200)
        created=json.loads(raw)
        self.assertEqual(created['settings']['director']['provider'],'openai-luna')
        self.assertEqual(created['settings']['image']['provider'],'comfyui')
        self.assertEqual(created['settings']['image']['model'],'flux2-klein-4b')
        self.assertEqual(created['settings']['video']['fps'],60)
        self.assertTrue(created['settings']['cloudImagesOnly'])
        self.assertEqual(created['settings']['qcCheckLevel'],'sampled')
        self.assertEqual(created['settings']['image']['controlnets'],[])
        self.assertNotIn('outroAudioPath',created['settings']['engagement'])
        self.assertEqual(created['chapters'][0]['sourceText'],'')
        self.assertFalse(created['intro']['enabled'])
        self.assertEqual(created['intro']['audioPath'],'')
        self.assertNotEqual(created['id'],original['id'])
        self.assertEqual(service.store.load(original['id']),original)

    def test_changing_intro_placement_invalidates_old_chapter_exports(self):
        service=self.server.RequestHandlerClass.studio_service
        project=new_project();project['intro'].update(enabled=True,placement='every_chapter')
        project['chapters'][0].update(renderStale=False,render={'path':'saved.mp4'})
        project=service.store.save(project)
        intro=project['intro'] | {'placement':'full_story_only'}
        code,_,_=self.request('/studio/edit',{'project':project['id'],'scope':'project','id':project['id'],'patch':{'intro':intro}})
        self.assertEqual(code,200)
        saved=service.store.load(project['id'])
        self.assertTrue(saved['chapters'][0]['renderStale'])
        self.assertEqual(saved['chapters'][0]['render']['path'],'saved.mp4')

    def test_editing_legacy_intro_text_records_old_audio_text_identity(self):
        from studio_data import digest
        service=self.server.RequestHandlerClass.studio_service
        project=new_project();project['intro'].update(voiceText='Original hook.',audioPath='intro.wav')
        project=service.store.save(project)
        code,_,_=self.request('/studio/edit',{'project':project['id'],'scope':'project','id':project['id'],'patch':{'intro':project['intro'] | {'voiceText':'Rewritten hook.'}}})
        self.assertEqual(code,200)
        saved=service.store.load(project['id'])
        self.assertEqual(saved['intro']['audioTextDigest'],digest('Original hook.'))
        self.assertEqual(saved['intro']['voiceText'],'Rewritten hook.')

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

    def test_missing_project_is_distinct_from_an_invalid_saved_snapshot(self):
        service=self.server.RequestHandlerClass.studio_service
        pid='pr-0123456789abcdef'
        code,_,data=self.request('/studio/project?id='+pid)
        self.assertEqual(code,404);self.assertEqual(json.loads(data)['code'],'PROJECT_NOT_FOUND')
        folder=service.store.folder(pid);folder.mkdir();snapshot=folder/'project.json';snapshot.write_text('{broken')
        code,_,data=self.request('/studio/project?id='+pid)
        self.assertEqual(code,400);self.assertNotEqual(json.loads(data).get('code'),'PROJECT_NOT_FOUND')
        self.assertEqual(snapshot.read_text(),'{broken')
        code,_,data=self.request('/studio/project?id=invalid')
        self.assertEqual(code,400)

    def test_unchanged_editor_save_does_not_rewrite_or_archive_the_whole_project(self):
        from unittest.mock import Mock
        service=self.server.RequestHandlerClass.studio_service
        p=service.store.save(new_project('Unchanged settings'))
        file=service.store.folder(p['id'])/'project.json';before=file.read_bytes()
        archive=Mock();archive.enqueue=Mock()
        with patch.object(service.store,'archive',archive):
            code,_,data=self.request('/studio/edit',{'project':p['id'],'scope':'project','patch':{'name':p['name'],'settings':p['settings'],'intro':p['intro']}})
        self.assertEqual(code,200);self.assertEqual(json.loads(data)['revision'],p['revision'])
        self.assertEqual(file.read_bytes(),before);archive.enqueue.assert_not_called()
        code,_,data=self.request('/studio/edit',{'project':p['id'],'scope':'project','patch':{'name':'Actual update'}})
        self.assertEqual(code,200);self.assertEqual(json.loads(data)['revision'],p['revision']+1)

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
