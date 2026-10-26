import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from voice_lab import MODELS, VoiceLab, WorkerClient, WorkerNotFound, WorkerUnavailable
from studio_audio import OpusWriter


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

    def completed_design(self, seconds=27.25):
        if not self.ffmpeg:
            self.skipTest('FFmpeg required')
        job = self.lab.enqueue(self.body(model='qwen3', mode='design', language='English',
            text='Mara paused.\n“Not this time,” she said.', description='A calm narrator.'))
        path = self.lab.root / (job['id'] + '.opus')
        with OpusWriter(path, self.ffmpeg) as audio:
            audio.setparams((1, 2, 24000))
            audio.writeframes(b'\0\0' * round(seconds * 24000))
        # Deliberately wrong metadata proves promotion reads actual audio length.
        self.lab._update(job['id'], 'COMPLETED', dict(audioSeconds=999))
        return job, path

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

    def test_qwen_sampler_controls_preserve_booleans_and_reject_numeric_type_confusion(self):
        job = self.lab.enqueue(self.body(model='qwen3', mode='design', language='English', description='A calm voice.',
            parameters=dict(do_sample=False, temperature=.9, top_p=1, top_k=50, repetition_penalty=1.05,
                            subtalker_dosample=True, subtalker_temperature=.9, subtalker_top_p=1, subtalker_top_k=50)))
        self.lab.tick()
        sent = self.worker.submits[-1]['parameters']
        self.assertIs(sent['do_sample'], False)
        self.assertIs(sent['subtalker_dosample'], True)
        self.assertEqual(sent['repetition_penalty'], 1.05)
        for params in ({'do_sample': 1}, {'subtalker_dosample': 'true'}, {'top_k': True},
                       {'temperature': float('inf')}, {'repetition_penalty': 0}, {'subtalker_top_k': 1.5}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                self.lab.enqueue(self.body(model='qwen3', mode='design', language='English',
                                          description='A calm voice.', parameters=params))
        self.assertEqual(self.lab.job(job['id'])['status'], 'RUNNING')

    def test_qwen_nine_builtin_speakers_are_selectable_without_replacing_base_or_design(self):
        from voice_lab_worker.service import validate
        model = next(m for m in MODELS if m['id'] == 'qwen3')
        modes = {m['id']: m for m in model['modes']}
        self.assertEqual(set(modes), {'design', 'clone', 'preset'})
        self.assertFalse(modes['preset']['requiresReference'])
        self.assertTrue(modes['preset']['allowsDescription'])
        control = next(p for p in model['parameters'] if p['id'] == 'speaker')
        speakers = ['vivian', 'serena', 'uncle_fu', 'dylan', 'eric', 'ryan', 'aiden', 'ono_anna', 'sohee']
        self.assertEqual([o['value'] for o in control['options']], speakers)
        self.assertEqual(control['modes'], ['preset'])
        for i, speaker in enumerate(speakers):
            description = '' if i % 2 == 0 else 'Warm and clear.'
            job = self.lab.enqueue(self.body(model='qwen3', mode='preset', language='English',
                description=description, parameters={'speaker': speaker}))
            self.assertEqual(job['parameters']['speaker'], speaker)
            worker_input = validate(dict(job, parameters=job['parameters']))
            self.assertEqual(worker_input['parameters']['speaker'], speaker)
            self.assertEqual(worker_input['description'], description)
            self.assertIsNone(worker_input['reference'])
            self.lab.cancel(job['id'])
        design = self.lab.enqueue(self.body(model='qwen3', mode='design', language='English',
                                           description='A calm voice.', parameters={'speaker': 'ryan'}))
        self.assertEqual(design['mode'], 'design')
        self.assertEqual(design['parameters']['speaker'], 'ryan')
        for speaker in ('unknown', 'Vivian', True, 3):
            with self.subTest(speaker=speaker), self.assertRaises(ValueError):
                self.lab.enqueue(self.body(model='qwen3', mode='preset', language='English', parameters={'speaker': speaker}))

    def test_qwen_metrics_select_the_actual_variant_checkpoint_instead_of_always_base(self):
        from voice_lab_worker import service
        items = [dict(folder='qwen3-base', repo='Base', revision='base-pin'),
                 dict(folder='qwen3-design', repo='VoiceDesign', revision='design-pin'),
                 dict(folder='qwen3-preset', repo='CustomVoice', revision='preset-pin')]
        with patch.dict(service.MANIFEST, {'models': {'qwen3': items}}):
            self.assertEqual(service.checkpoint_for('qwen3', 'clone'), items[0])
            self.assertEqual(service.checkpoint_for('qwen3', 'design'), items[1])
            self.assertEqual(service.checkpoint_for('qwen3', 'preset'), items[2])
            with self.assertRaises(ValueError):
                service.checkpoint_for('qwen3', 'unsupported')

    def test_drama_design_character_defaults_and_author_names_survive_helper_worker_contract(self):
        from voice_lab_worker.service import validate
        job = self.lab.enqueue(self.body(model='drama3', mode='design', description='A warm storyteller.'))
        self.assertEqual(job['character'], {'name': 'Narrator', 'scene': None})
        self.lab.tick()
        sent = self.worker.submits[-1]
        self.assertEqual(validate(sent)['character'], job['character'])
        self.complete(job['id'])
        self.lab.tick()
        metadata = json.loads((Path(__file__).resolve().parent.parent / 'voice-lab-author-presets.json').read_text('utf-8'))
        self.assertEqual(len(metadata['drama3']), 8)
        for preset in metadata['drama3']:
            character = dict(name=preset['characterName'], scene=preset.get('scene') or None)
            author = self.lab.enqueue(self.body(model='drama3', mode='design', description=preset['description'], character=character))
            self.assertEqual(author['character'], character)
            self.lab.tick()
            self.assertEqual(validate(self.worker.submits[-1])['character'], character)
            self.complete(author['id'])
            self.lab.tick()

    def test_drama_top_k_default_includes_all_candidates_and_preserves_explicit_historical_values(self):
        from voice_lab_worker.service import validate
        for value in (None, 30, 4097):
            job = self.lab.enqueue(self.body(model='drama3', parameters={} if value is None else {'top_k': value}))
            self.assertEqual(job['parameters']['top_k'], 0 if value is None else value)
            self.lab.tick()
            self.assertEqual(validate(self.worker.submits[-1])['parameters']['top_k'], 0 if value is None else value)
            self.complete(job['id'])
            self.lab.tick()

    def test_drama_design_rejects_structural_character_fields_and_ignored_fields_on_other_modes(self):
        bad = [{'name': 'A:B'}, {'name': 'A\nB'}, {'name': '<|speaker|>'}, {'name': '@@drama3-space@@'},
               {'name': 'x'*81}, {'name': 'Narrator', 'scene': 'x'*501},
               {'name': 'Narrator', 'scene': 'A\nB'}, {'name': 'Narrator', 'scene': '<|speaker|>'},
               {'name': 'Narrator', 'unknown': 'extra'}, []]
        for character in bad:
            with self.subTest(character=character), self.assertRaises(ValueError):
                self.lab.enqueue(self.body(model='drama3', mode='design', description='A warm voice.', character=character))
        with self.assertRaises(ValueError):
            self.lab.enqueue(self.body(model='qwen3', mode='preset', language='English', character={'name': 'Narrator'}))

    def test_vox_clone_strategy_validates_transcript_and_style_without_silently_ignoring_it(self):
        ref = self.reference()
        no_transcript = self.reference(transcript='')
        for params, reference, description in [({'clone_strategy': False}, ref, ''),
                ({'clone_strategy': 'unsupported'}, ref, ''),
                ({'clone_strategy': 'prompt_reference'}, no_transcript, ''),
                ({'clone_strategy': 'prompt_reference'}, ref, 'A dramatic whisper.')]:
            with self.subTest(params=params, description=description), self.assertRaises(ValueError):
                self.lab.enqueue(self.body(mode='clone', referenceId=reference['id'], parameters=params, description=description))
        strict = self.lab.enqueue(self.body(mode='clone', referenceId=ref['id'],
                                           parameters={'clone_strategy': 'prompt_reference'}))
        styled = self.lab.enqueue(self.body(mode='clone', referenceId=ref['id'], description='A dramatic whisper.',
                                           parameters={'clone_strategy': 'reference'}))
        self.assertEqual(strict['parameters']['clone_strategy'], 'prompt_reference')
        self.assertEqual(styled['description'], 'A dramatic whisper.')

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

    def test_promoted_voice_preserves_exact_audio_script_provenance_and_saved_data(self):
        job, path = self.completed_design()
        original = path.read_bytes()
        ref = self.lab.reference_from_job(dict(jobId=job['id'], name='Mara narrator', rightsConfirmed=True))
        self.assertEqual(ref['transcript'], job['text'])
        self.assertEqual(ref['source'], dict(kind='audition', jobId=job['id'], model='qwen3', mode='design',
                                           sha256=hashlib.sha256(original).hexdigest()))
        self.assertAlmostEqual(ref['durationSeconds'], 27.25, places=5)
        self.assertTrue(ref['preferredDuration'])
        self.assertTrue(ref['audioReady'])
        self.assertEqual(self.lab.reference_audio(ref['id']).read_bytes(), original)
        self.assertEqual(path.read_bytes(), original)
        self.lab.close()
        self.lab = VoiceLab(self.temp.name, self.config, self.worker)
        self.assertEqual(self.lab.snapshot()['references'][0], ref)
        again = self.lab.reference_from_job(dict(jobId=job['id'], name='A repeated click', rightsConfirmed=True))
        self.assertEqual(again, ref)
        self.assertEqual(self.lab.db.execute('SELECT count(*) FROM refs').fetchone()[0], 1)
        self.assertEqual(list(self.lab.root.glob('*.writing')), [])

    def test_saved_designed_voice_can_condition_all_three_models_including_its_creator(self):
        source, _ = self.completed_design()
        ref = self.lab.reference_from_job(dict(jobId=source['id'], name='Reusable design', rightsConfirmed=True))
        for model, language in [('drama3', 'Auto'), ('voxcpm2', 'Auto'), ('qwen3', 'English')]:
            job = self.lab.enqueue(self.body(model=model, mode='clone', language=language, referenceId=ref['id']))
            self.lab.tick()
            submission = self.worker.submits[-1]
            self.assertEqual(submission['id'], job['id'])
            self.assertEqual(submission['model'], model)
            self.assertEqual(submission['reference']['transcript'], source['text'])
            self.assertEqual(base64.b64decode(submission['reference']['audioBase64']), self.lab.reference_audio(ref['id']).read_bytes())
            self.complete(job['id'])
            self.lab.tick()
            self.assertEqual(self.lab.job(job['id'])['status'], 'COMPLETED')

    def test_reference_promotion_rejects_missing_permission_incomplete_or_long_audio_without_trimming(self):
        job = self.lab.enqueue(self.body())
        for body in [dict(jobId=job['id'], rightsConfirmed=False), dict(jobId=job['id'], rightsConfirmed=True),
                     dict(jobId='../private', rightsConfirmed=True)]:
            with self.assertRaises(ValueError):
                self.lab.reference_from_job(body)
        long_job, path = self.completed_design(61)
        original = path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'will not be trimmed'):
            self.lab.reference_from_job(dict(jobId=long_job['id'], name='Too long', rightsConfirmed=True))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.lab.snapshot()['references'], [])
        proper_job, _ = self.completed_design(26)
        with self.assertRaisesRegex(ValueError, 'original script'):
            self.lab.reference_from_job(dict(jobId=proper_job['id'], rightsConfirmed=True, transcript='Replacement text'))

    def test_reference_audio_requires_a_library_identity_not_an_arbitrary_job_or_path(self):
        job, _ = self.completed_design()
        for id in (job['id'], '../private', 'b' * 32):
            with self.subTest(id=id), self.assertRaises(ValueError):
                self.lab.reference_audio(id)

    def test_legacy_reference_schema_is_migrated_without_replacing_assets_or_rows(self):
        legacy = Path(self.temp.name) / 'legacy'
        root = legacy / '.voice-lab'
        root.mkdir(parents=True)
        db = sqlite3.connect(root / 'auditions.sqlite')
        db.execute('CREATE TABLE refs(id TEXT PRIMARY KEY,name TEXT NOT NULL,transcript TEXT NOT NULL,duration REAL NOT NULL,created REAL NOT NULL)')
        id = 'a' * 32
        db.execute('INSERT INTO refs VALUES(?,?,?,?,?)', (id, 'Existing voice', 'Existing transcript.', 28, 123))
        db.commit()
        db.close()
        original = b'existing audio bytes preserved by metadata-only migration'
        (root / (id + '.opus')).write_bytes(original)
        migrated = VoiceLab(legacy, self.config, self.worker)
        try:
            ref = migrated.snapshot()['references'][0]
            self.assertEqual((ref['id'], ref['name'], ref['transcript'], ref['durationSeconds'], ref['createdAt']),
                             (id, 'Existing voice', 'Existing transcript.', 28, 123))
            self.assertEqual(ref['source'], {'kind': 'upload'})
            self.assertEqual(migrated.reference_audio(id).read_bytes(), original)
        finally:
            migrated.close()

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
                           ('/studio/voice-lab/reference-audio?id=' + 'a' * 32, None),
                           ('/studio/voice-lab/reference-from-job', {}),
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
        code, _, raw = request('/studio/voice-lab/reference-from-job',
                               dict(jobId=results[2]['id'], name='Reusable audition', rightsConfirmed=True))
        self.assertEqual(code, 200, raw)
        saved = json.loads(raw)
        self.assertEqual(saved['source']['model'], 'qwen3')
        self.assertEqual(saved['transcript'], 'A quiet test.')
        self.assertAlmostEqual(saved['durationSeconds'], 2, places=2)
        self.assertFalse(saved['preferredDuration'])
        code, headers, raw = request('/studio/voice-lab/reference-audio?id=' + saved['id'], extra_headers={'Range': 'bytes=0-3'})
        self.assertEqual((code, raw), (206, b'OggS'))
        self.assertTrue(headers['Content-Type'].startswith('audio/'))
        self.assertEqual(request('/studio/voice-lab/reference-audio?id=' + saved['id'], key='wrong')[0], 401)
        config = self.bridge.server.RequestHandlerClass.studio_service.config
        self.assertNotIn('voiceEngine', config)
        snapshot = json.loads(request('/studio/voice-lab')[2])
        self.assertNotIn('integration-test-only-private-token', json.dumps(snapshot))
        self.assertNotIn('voiceLabWorkerURL', snapshot)

    def test_all_nine_builtin_speakers_cross_the_private_http_boundary_with_optional_instructions(self):
        request = self.bridge.request
        speakers = ['vivian', 'serena', 'uncle_fu', 'dylan', 'eric', 'ryan', 'aiden', 'ono_anna', 'sohee']
        for i, speaker in enumerate(speakers):
            description = '' if i % 2 == 0 else 'Speak warmly and clearly.'
            code, _, raw = request('/studio/voice-lab/jobs', dict(model='qwen3', mode='preset', language='English',
                text='A quiet test.', description=description, parameters={'speaker': speaker}))
            self.assertEqual(code, 200, raw)
            job = json.loads(raw)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                result = json.loads(request('/studio/voice-lab/job?id=' + job['id'])[2])
                if result['status'] == 'COMPLETED':
                    break
                time.sleep(.025)
            self.assertEqual(result['status'], 'COMPLETED', result)
            self.assertEqual(self.received[-1]['parameters']['speaker'], speaker)
            self.assertEqual(self.received[-1]['description'], description)
            self.assertIsNone(self.received[-1]['reference'])
        self.assertEqual(len(self.received), 9)


if __name__ == '__main__':
    unittest.main()
