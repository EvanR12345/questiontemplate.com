"""CPU-only worker contract tests. No model download, paid requests or GPU import."""
import base64
import io
import json
from pathlib import Path, PurePosixPath
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
import adapter
import prepare
import preflight


def body(**updates):
    value = {'id': 'audition-12345678', 'model': 'voxcpm2', 'mode': 'default', 'text': 'A short audition.', 'language': 'auto', 'parameters': {}}
    value.update(updates)
    return value


def cuda_fixture(root, development_names=True):
    toolkit = Path(root) / 'envs/drama3/lib/python3.11/site-packages/nvidia/cu13'
    for name in ['bin/nvcc', 'include/cuda_runtime.h', 'nvvm/libdevice/libdevice.10.bc',
                 'lib/libcudart.so.13', 'lib64/stubs/libcuda.so']:
        target = toolkit / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'nonempty-test-fixture')
    if development_names:
        for name in ['lib/libcudart.so', 'lib64/libcudart.so']:
            (toolkit / name).write_bytes(b'nonempty-test-fixture')
    return toolkit


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


class GenerationLimits(unittest.TestCase):
    def test_drama_jit_uses_its_pinned_cuda_toolkit_instead_of_older_base_image(self):
        with tempfile.TemporaryDirectory() as directory:
            toolkit = cuda_fixture(directory, development_names=False)
            inherited = {'CUDA_HOME': '/usr/local/cuda12.8', 'PATH': '/usr/local/cuda12.8/bin', 'LD_LIBRARY_PATH': '/old/lib'}
            # Native hard links emulate the resolved file target on Windows,
            # whose test account has no symlink privilege. Production uses
            # relative symlinks on the Linux worker.
            with patch.object(preflight, '_create_relative_link', side_effect=lambda link, target: link.hardlink_to(target)):
                environment = adapter.drama_cuda_environment(directory, inherited)
            self.assertEqual(environment['CUDA_HOME'], str(toolkit))
            self.assertEqual(environment['CUDACXX'], str(toolkit / 'bin/nvcc'))
            self.assertTrue(environment['PATH'].startswith(str(toolkit / 'bin') + adapter.os.pathsep))
            self.assertEqual(inherited['CUDA_HOME'], '/usr/local/cuda12.8')
            self.assertTrue((toolkit / 'lib64/libcudart.so').is_file())
            self.assertTrue((toolkit / 'lib/libcudart.so').is_file())

    def test_drama_requires_normal_finish_reason(self):
        self.assertEqual(adapter.check_drama_finish({'X-Finish-Reason': 'stop', 'X-Completion-Tokens': '300'}, 1300), 'stop')
        for headers in ({}, {'X-Finish-Reason': 'length'}, {'X-Finish-Reason': 'unknown'},
                        {'X-Finish-Reason': 'stop', 'X-Completion-Tokens': '1300'}):
            with self.subTest(headers=headers), self.assertRaises(adapter.AuditionLimit):
                adapter.check_drama_finish(headers, 1300)

    def test_qwen_cap_is_rejected_before_wrapper_decodes_and_method_restored(self):
        model = types.SimpleNamespace(generate=lambda **kwargs: ([types.SimpleNamespace(shape=(750, 16))], []))
        original = model.generate
        with self.assertRaises(adapter.AuditionLimit):
            with adapter.qwen_limit_guard(model):
                model.generate(max_new_tokens=750)
        self.assertIs(model.generate, original)
        model.generate = lambda **kwargs: ([types.SimpleNamespace(shape=(300, 16))], [])
        with adapter.qwen_limit_guard(model):
            self.assertEqual(model.generate(max_new_tokens=750)[0][0].shape[0], 300)

    def test_vox_checks_actual_dynamic_cap_not_just_outer_requested_limit(self):
        def generate(**kwargs):
            yield None, types.SimpleNamespace(shape=(kwargs['max_len'], 64)), 0
        model = types.SimpleNamespace(_inference=generate)
        original = model._inference
        with self.assertRaises(adapter.AuditionLimit):
            with adapter.vox_limit_guard(model):
                next(model._inference(max_len=42, streaming=False))
        self.assertIs(model._inference, original)


class PreparationQuota(unittest.TestCase):
    def test_slow_nfs_scan_retries_with_practical_bounded_timeout_then_uses_real_total(self):
        timeout = prepare.subprocess.TimeoutExpired('du', 120)
        stable = types.SimpleNamespace(returncode=0, stdout='105000000000\t/unused\n', stderr='')
        with patch.object(prepare.subprocess, 'run', side_effect=[timeout, stable]) as scan, \
                patch.object(prepare.time, 'sleep'):
            self.assertEqual(prepare.quota_usage(PurePosixPath('/unused')), 105000000000)
            self.assertEqual(scan.call_args_list[0].kwargs['timeout'], 120)
            self.assertEqual(scan.call_count, 2)

    def test_repeated_nfs_timeouts_fail_closed_with_diagnostics(self):
        with patch.object(prepare.subprocess, 'run', side_effect=prepare.subprocess.TimeoutExpired('du', 120)) as scan, \
                patch.object(prepare.time, 'sleep'):
            with self.assertRaises(prepare.QuotaScanError) as error:
                prepare.quota_usage(PurePosixPath('/unused'))
            self.assertEqual(scan.call_count, prepare.QUOTA_SCAN_ATTEMPTS)
            self.assertEqual(error.exception.returncode, 124)
            self.assertIn('timed out without a complete numeric total', str(error.exception))

    def test_fts_enoent_without_total_retries_until_a_complete_numeric_scan(self):
        race = types.SimpleNamespace(returncode=1, stdout='',
                                      stderr='du: fts_read failed: /unused/cache/uv/.tmp123: No such file or directory\n')
        stable = types.SimpleNamespace(returncode=0, stdout='17000000000\t/unused\n', stderr='')
        with patch.object(prepare.subprocess, 'run', side_effect=[race, race, stable]) as scan, \
                patch.object(prepare.time, 'sleep'):
            self.assertEqual(prepare.quota_usage(PurePosixPath('/unused')), 17000000000)
            self.assertEqual(scan.call_count, 3)

    def test_fts_races_never_invent_a_total_when_every_scan_aborts(self):
        race = types.SimpleNamespace(returncode=1, stdout='',
                                      stderr='du: fts_read failed: /unused/cache/uv/.tmp123: No such file or directory\n')
        with patch.object(prepare.subprocess, 'run', return_value=race) as scan, \
                patch.object(prepare.time, 'sleep'):
            with self.assertRaises(prepare.QuotaScanError) as error:
                prepare.quota_usage(PurePosixPath('/unused'))
            self.assertEqual(scan.call_count, prepare.QUOTA_SCAN_ATTEMPTS)
            self.assertIn('fts_read failed', str(error.exception))
            self.assertEqual(error.exception.stdout, '')

    def test_fts_permission_io_and_outside_root_errors_are_not_retried(self):
        for message in ['du: fts_read failed: /unused/cache/uv/.tmp123: Permission denied\n',
                        'du: fts_read failed: /unused/cache/uv/.tmp123: Input/output error\n',
                        'du: fts_read failed: /outside/.tmp123: No such file or directory\n',
                        'du: fts_read failed: /unused/../outside/.tmp123: No such file or directory\n']:
            result = types.SimpleNamespace(returncode=1, stdout='', stderr=message)
            with self.subTest(message=message), patch.object(prepare.subprocess, 'run', return_value=result) as scan:
                with self.assertRaises(prepare.QuotaScanError):
                    prepare.quota_usage(PurePosixPath('/unused'))
                self.assertEqual(scan.call_count, 1)

    def test_du_disappearing_file_is_retried_without_aborting_a_live_install(self):
        missing = types.SimpleNamespace(returncode=1, stdout='70000000000\t/unused\n',
                                        stderr="du: cannot access '/unused/envs/qwen3/old.py': No such file or directory\n")
        stable = types.SimpleNamespace(returncode=0, stdout='69000000000\t/unused\n', stderr='')
        with patch.object(prepare.subprocess, 'run', side_effect=[missing, stable]) as scan, \
                patch.object(prepare.time, 'sleep'):
            self.assertEqual(prepare.quota_usage(PurePosixPath('/unused')), 70000000000)
            self.assertEqual(scan.call_count, 2)
            self.assertEqual(scan.call_args.kwargs['env']['LC_ALL'], 'C')

    def test_continuous_du_unlink_races_use_bounded_conservative_estimate(self):
        def result(total):
            return types.SimpleNamespace(returncode=1, stdout=str(total) + '\t/unused\n',
                                         stderr="du: cannot access '/unused/envs/qwen3/old.py': No such file or directory\n")
        with patch.object(prepare.subprocess, 'run', side_effect=[result(70), result(80), result(60)]) as scan, \
                patch.object(prepare.time, 'sleep'):
            self.assertEqual(prepare.quota_usage(PurePosixPath('/unused')), 80 + prepare.QUOTA_RACE_BUFFER_BYTES)
            self.assertEqual(scan.call_count, 3)

    def test_du_permission_io_missing_root_and_invalid_totals_still_stop_setup(self):
        invalid = [
            types.SimpleNamespace(returncode=1, stdout='70\t/unused\n', stderr="du: cannot read directory '/unused/envs': Permission denied\n"),
            types.SimpleNamespace(returncode=1, stdout='70\t/unused\n', stderr="du: cannot access '/unused/envs': Input/output error\n"),
            types.SimpleNamespace(returncode=1, stdout='70\t/unused\n', stderr="du: cannot access '/unused': No such file or directory\n"),
            types.SimpleNamespace(returncode=1, stdout='', stderr="du: cannot access '/unused/envs/old.py': No such file or directory\n"),
            types.SimpleNamespace(returncode=1, stdout='70\t/unused\n', stderr="du: cannot access '/outside/old.py': No such file or directory\n"),
        ]
        for value in invalid:
            with self.subTest(value=value), patch.object(prepare.subprocess, 'run', return_value=value), \
                    self.assertRaises(prepare.subprocess.CalledProcessError):
                prepare.quota_usage(PurePosixPath('/unused'))
        with patch.object(prepare.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='not a total', stderr='')), \
                self.assertRaisesRegex(RuntimeError, 'valid quota total'):
            prepare.quota_usage(PurePosixPath('/unused'))

    def test_declared_volume_quota_controls_preparation_not_shared_filesystem_df(self):
        with patch.object(prepare, 'QUOTA_BYTES', 100), patch.object(prepare, 'RESERVE_BYTES', 10), \
                patch.object(prepare, 'quota_usage', return_value=70):
            self.assertEqual(prepare.quota_guard(Path('/unused'), needed=20), (70, 30))
            with self.assertRaisesRegex(RuntimeError, 'quota'):
                prepare.quota_guard(Path('/unused'), needed=21)

    def test_near_quota_stops_before_starting_a_child_install(self):
        with patch.object(prepare, 'QUOTA_ROOT', Path('/unused')), \
                patch.object(prepare, 'quota_guard', side_effect=RuntimeError('near quota')), \
                patch.object(prepare.subprocess, 'Popen') as process:
            with self.assertRaisesRegex(RuntimeError, 'quota'):
                prepare.run(['uv', 'pip', 'install', 'unused'])
            process.assert_not_called()


class CUDARuntimeDevelopmentLayout(unittest.TestCase):
    def test_missing_unversioned_runtime_is_repaired_and_versioned_file_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            toolkit = cuda_fixture(directory, development_names=False)
            versioned = toolkit / 'lib/libcudart.so.13'
            original = versioned.read_bytes()
            self.assertFalse((toolkit / 'lib64/libcudart.so').exists())
            with patch.object(preflight, '_create_relative_link', side_effect=lambda link, target: link.hardlink_to(target)) as link:
                result = preflight.ensure_cuda_layout(directory)
                again = preflight.ensure_cuda_layout(directory)
            self.assertEqual(link.call_count, 2)
            self.assertEqual(len(result['cudaRuntimeDevelopmentLinksCreated']), 2)
            self.assertEqual(again['cudaRuntimeDevelopmentLinksCreated'], [])
            self.assertEqual(versioned.read_bytes(), original)
            self.assertEqual((toolkit / 'lib64/libcudart.so').read_bytes(), original)

    def test_empty_versioned_runtime_does_not_create_a_false_development_link(self):
        with tempfile.TemporaryDirectory() as directory:
            toolkit = cuda_fixture(directory, development_names=False)
            (toolkit / 'lib/libcudart.so.13').write_bytes(b'')
            with self.assertRaisesRegex(RuntimeError, 'empty'):
                preflight.ensure_cuda_layout(directory)
            self.assertFalse((toolkit / 'lib64/libcudart.so').exists())

    def test_compile_link_probe_exercises_lcudart_and_never_executes_the_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            toolkit = cuda_fixture(directory, development_names=False)
            calls = []
            def compiler(command, **kwargs):
                calls.append(command)
                if command[0] == str(toolkit / 'bin/nvcc'):
                    source = Path(command[command.index('-c') + 1]).read_text()
                    self.assertIn('cudaGetDeviceCount', source)
                else:
                    self.assertEqual(command[0], 'c++')
                    self.assertIn('-lcudart', command)
                    self.assertIn('-L' + str(toolkit / 'lib64'), command)
                    if not (toolkit / 'lib64/libcudart.so').exists():
                        raise preflight.subprocess.CalledProcessError(1, command, stderr='cannot find -lcudart')
                return types.SimpleNamespace(returncode=0)
            # Simulate the observed linker failure using the actual fixture's
            # missing development filename, then prove layout repair unblocks
            # that same compile/link contract. No CUDA compiler or GPU is used.
            with patch.object(preflight.subprocess, 'run', side_effect=compiler):
                with self.assertRaises(preflight.subprocess.CalledProcessError):
                    preflight.compile_cuda_runtime_probe(toolkit, {})
                with patch.object(preflight, '_create_relative_link', side_effect=lambda link, target: link.hardlink_to(target)):
                    preflight.ensure_cuda_layout(directory)
                calls.clear()
                result = preflight.compile_cuda_runtime_probe(toolkit, {})
            self.assertTrue(result['cudaRuntimeCompileLink'])
            self.assertFalse(result['cudaRuntimeProbeExecuted'])
            self.assertEqual(len(calls), 2)


class ModelControlForwarding(unittest.TestCase):
    def test_qwen_presets_use_customvoice_variant_and_native_speaker_api(self):
        self.assertEqual(adapter.qwen_variant('preset'), 'preset')
        self.assertEqual(adapter.qwen_variant('design'), 'design')
        self.assertEqual(adapter.qwen_variant('clone'), 'base')
        with self.assertRaises(ValueError):
            adapter.qwen_variant('default')
        custom = [item for item in service.MANIFEST['models']['qwen3'] if item['folder'] == 'qwen3-preset']
        self.assertEqual(len(custom), 1)
        self.assertEqual(custom[0]['repo'], 'Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice')
        captured = {}
        def generate_custom_voice(**kwargs):
            captured.update(kwargs)
            return [object()], 24000
        runtime = adapter.Runtime('qwen3')
        runtime.model = types.SimpleNamespace(model=types.SimpleNamespace(generate=lambda **kwargs: ([], [])),
                                               generate_custom_voice=generate_custom_voice)
        runtime.load = lambda mode: 0.0
        fake_torch = types.SimpleNamespace(manual_seed=lambda _: None,
                                            cuda=types.SimpleNamespace(manual_seed_all=lambda _: None, synchronize=lambda: None))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            job = body(model='qwen3', mode='preset', description='Calm delivery.',
                       parameters={'seed': 42, 'speaker': 'ryan'}, reference=None,
                       directory=str(root / 'jobs' / 'audition-12345678'))
            with patch.object(adapter, 'ROOT', root), patch.object(adapter, 'reference_wav', return_value=None), \
                    patch.object(adapter, 'write_audio') as writer, patch.object(adapter, 'emit'), \
                    patch.dict('sys.modules', {'torch': fake_torch}):
                runtime.generate(job)
        self.assertEqual(captured['speaker'], 'ryan')
        self.assertEqual(captured['instruct'], 'Calm delivery.')
        self.assertEqual(captured['temperature'], .9)
        writer.assert_called_once()

    def test_qwen_checkpoint_sampler_defaults_and_explicit_false_are_not_lost(self):
        options = adapter.qwen_generation_options({'seed': 42})
        self.assertTrue(options['do_sample'])
        self.assertEqual(options['temperature'], .9)
        self.assertEqual(options['repetition_penalty'], 1.05)
        self.assertEqual(options['subtalker_top_k'], 50)
        changed = adapter.qwen_generation_options({'do_sample': False, 'subtalker_dosample': False,
                                                   'temperature': 1.2, 'top_k': 75, 'subtalker_top_p': .8})
        self.assertFalse(changed['do_sample'])
        self.assertFalse(changed['subtalker_dosample'])
        self.assertEqual(changed['temperature'], 1.2)
        self.assertEqual(changed['top_k'], 75)
        self.assertEqual(changed['subtalker_top_p'], .8)
        self.assertEqual(changed['max_new_tokens'], 750)

    def vox_body(self, strategy='auto', transcript='The exact reference words.', description=''):
        return body(model='voxcpm2', mode='clone', description=description,
                    reference={'transcript': transcript},
                    parameters={'cfg_value': 2, 'inference_timesteps': 10, 'seed': 42, 'clone_strategy': strategy})

    def test_vox_auto_preserves_old_continuation_while_new_strategy_uses_both_paths(self):
        reference = Path('/private/reference.wav')
        old = adapter.voxcpm_generation_options(self.vox_body(), reference)
        self.assertEqual(old['prompt_wav_path'], str(reference))
        self.assertNotIn('reference_wav_path', old)
        both = adapter.voxcpm_generation_options(self.vox_body('prompt_reference'), reference)
        self.assertEqual(both['prompt_wav_path'], both['reference_wav_path'])
        self.assertEqual(both['prompt_text'], 'The exact reference words.')
        self.assertFalse(both['retry_badcase'])

    def test_vox_reference_only_applies_style_and_does_not_accidentally_use_transcript(self):
        options = adapter.voxcpm_generation_options(self.vox_body('reference', description='Warm (clear) voice（calm）'), Path('/private/ref.wav'))
        self.assertNotIn('prompt_wav_path', options)
        self.assertNotIn('prompt_text', options)
        self.assertEqual(options['text'], '(Warm clear voicecalm)A short audition.')
        for value in [self.vox_body('prompt_reference', transcript=''),
                      self.vox_body('prompt_reference', description='cheerful')]:
            with self.assertRaises(ValueError):
                adapter.voxcpm_generation_options(value, Path('/private/ref.wav'))

    def test_worker_validation_keeps_typed_controls_and_rejects_invalid_combinations(self):
        qwen = body(model='qwen3', mode='design', description='An adult warm narrator.',
                    parameters={'do_sample': False, 'subtalker_dosample': False, 'subtalker_top_k': 60})
        clean = service.validate(qwen)
        self.assertIs(clean['parameters']['do_sample'], False)
        self.assertIs(clean['parameters']['subtalker_dosample'], False)
        self.assertEqual(clean['parameters']['subtalker_top_k'], 60)
        for params in [{'do_sample': 'false'}, {'temperature': True}, {'subtalker_top_k': 2.5}]:
            with self.assertRaises(ValueError):
                service.validate(qwen | {'parameters': params})


if __name__ == '__main__':
    unittest.main()
