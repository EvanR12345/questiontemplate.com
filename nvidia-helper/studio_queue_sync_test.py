"""Queue synchronization keeps exact state/timing changes and skips true no-ops."""
import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import studio_data
from studio_data import ProjectStore, new_project
from studio_service import StudioService


class QueueSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(self.temp.name)
        project = new_project('Queue state fixture')
        chapter = project['chapters'][0]
        self.pid, self.chid, self.sid = project['id'], chapter['id'], 'shot-queue'
        chapter['status'] = 'GENERATING'
        chapter['scenes'] = [{'id': 'scene-queue', 'shots': [{
            'id': self.sid, 'chapterId': self.chid, 'sceneId': 'scene-queue',
            'characters': [], 'camera': {}, 'generationSettings': {},
            'prompt': 'A courtyard.', 'start': 0, 'end': 6,
            'status': 'GENERATING', 'imagePath': 'image.png',
            'qc': {'pass': True}, 'retryHistory': [{'seed': 42}],
        }]}]
        self.project = self.store.save(project)
        self.folder = self.store.folder(self.pid)
        self.store.asset(self.pid, 'image.png').write_bytes(b'preserved image')
        self.service = StudioService.__new__(StudioService)
        self.service.store = self.store
        self.service.cv = threading.Condition(threading.RLock())
        self.service.paused = False
        self.service.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.service.db.row_factory = sqlite3.Row
        self.service.db.execute('CREATE TABLE jobs(id TEXT,project TEXT,chapter TEXT,shot TEXT,kind TEXT,status TEXT,created REAL)')

    def tearDown(self):
        self.service.db.close()
        self.temp.cleanup()

    def job(self, identity='running', status='RUNNING', shot=True, kind='image', created=0):
        self.service.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?)',
            (identity, self.pid, self.chid, self.sid if shot else None, kind, status, created))
        self.service.db.commit()

    def attempt(self, **changes):
        return dict(id='qc-job', project=self.pid, chapter=self.chid, shot=self.sid,
                    kind='qc', status='COMPLETE', started=100, seconds=2.1256) | changes

    def files(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}

    def test_unchanged_running_items_skip_full_copy_and_every_file_write(self):
        self.job()
        before = self.files()
        self.store.archive = Mock()
        with patch.object(studio_data.copy, 'deepcopy', side_effect=AssertionError('Full snapshot copy')):
            self.service.sync_queue_states(self.pid)
        self.assertEqual(self.files(), before)
        self.store.archive.enqueue.assert_not_called()

    def test_intermediate_chapter_state_restored_by_active_shot_is_true_noop(self):
        self.job('failed-chapter', 'FAILED', shot=False)
        self.job('running-shot', created=1)
        before = self.files()
        self.service.sync_queue_states(self.pid)
        self.assertEqual(self.files(), before)

    def test_paused_queue_changes_chapter_and_shot_and_preserves_assets(self):
        self.job(status='QUEUED')
        self.service.paused = True
        before = (self.folder / 'project.json').read_bytes()
        with patch.object(studio_data, 'validate_project', wraps=studio_data.validate_project) as validate:
            self.service.sync_queue_states(self.pid)
            self.assertEqual(validate.call_count, 1)
        saved = self.store.load(self.pid)
        chapter = saved['chapters'][0]
        self.assertEqual(chapter['status'], 'PAUSED')
        self.assertEqual(chapter['scenes'][0]['shots'][0]['status'], 'PAUSED')
        self.assertEqual(saved['revision'], self.project['revision'] + 1)
        self.assertEqual((self.folder / 'project.previous.json').read_bytes(), before)
        self.assertEqual((self.folder / 'image.png').read_bytes(), b'preserved image')
        self.assertEqual(chapter['scenes'][0]['shots'][0]['retryHistory'], [{'seed': 42}])

    def test_complete_queue_row_does_not_certify_unfinished_shot(self):
        self.job(status='COMPLETE')
        before = self.files()
        self.service.sync_queue_states(self.pid)
        self.assertEqual(self.files(), before)

    def test_chapter_completion_from_already_saved_ready_shots_is_persisted(self):
        self.job(status='COMPLETE')
        self.store.mutate(self.pid, lambda p: p['chapters'][0]['scenes'][0]['shots'][0].update(status='PASSED'))
        self.service.sync_queue_states(self.pid)
        self.assertEqual(self.store.load(self.pid)['chapters'][0]['status'], 'COMPLETE')

    def test_new_timing_persists_duplicate_is_noop_and_next_attempt_is_distinct(self):
        self.job()
        self.service.sync_queue_states(self.pid, self.attempt())
        first = self.files()
        saved = self.store.load(self.pid)
        timing = saved['production']['timings'][0]
        self.assertEqual((timing['jobId'], timing['attemptStarted'], timing['seconds']), ('qc-job', 100, 2.126))
        self.assertEqual(timing['timingSource'], 'queue-attempt')
        self.service.sync_queue_states(self.pid, self.attempt(seconds=99))
        self.assertEqual(self.files(), first)
        self.service.sync_queue_states(self.pid, self.attempt(started=200, seconds=4))
        rows = self.store.load(self.pid)['production']['timings']
        self.assertEqual([(r['attemptStarted'], r['seconds']) for r in rows], [(100, 2.126), (200, 4)])

    def test_parent_job_and_missing_duration_do_not_create_timings(self):
        before = self.files()
        self.service.sync_queue_states(self.pid, self.attempt(kind='produce-story'))
        self.service.sync_queue_states(self.pid, self.attempt(seconds=None))
        self.assertEqual(self.files(), before)

    def test_concurrent_completion_notifications_retain_both_attempts_once(self):
        attempts = [self.attempt(started=100 + i % 2) for i in range(16)]
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda job: self.service.sync_queue_states(self.pid, job), attempts))
        saved = self.store.load(self.pid)
        self.assertEqual(sorted(t['attemptStarted'] for t in saved['production']['timings']), [100, 101])
        self.assertEqual(saved['revision'], self.project['revision'] + 2)

    def test_fresh_read_keeps_unrelated_external_edit(self):
        self.job()
        self.service.sync_queue_states(self.pid)
        path = self.folder / 'project.json'
        external = json.loads(path.read_bytes())
        external['name'] = 'External current name'
        external['chapters'][0]['scenes'][0]['shots'][0]['status'] = 'QUEUED'
        external['revision'] += 10
        path.write_text(json.dumps(external), encoding='utf-8')
        self.service.sync_queue_states(self.pid)
        saved = self.store.load(self.pid)
        self.assertEqual(saved['name'], 'External current name')
        self.assertEqual(saved['chapters'][0]['scenes'][0]['shots'][0]['status'], 'GENERATING')
        self.assertEqual(saved['revision'], external['revision'] + 1)

    def test_real_transition_keeps_full_project_validation(self):
        self.job(status='QUEUED')
        path = self.folder / 'project.json'
        invalid = json.loads(path.read_bytes())
        invalid['chapters'][0]['sourceText'] = 123
        path.write_text(json.dumps(invalid), encoding='utf-8')
        before = self.files()
        with self.assertRaises((ValueError, TypeError)):
            self.service.sync_queue_states(self.pid)
        self.assertEqual(self.files(), before)

    def test_legacy_null_history_without_timing_event_keeps_noop_and_real_transition(self):
        self.job()
        for legacy in (None, {'timings': None}):
            with self.subTest(production=legacy):
                self.store.mutate(self.pid, lambda p: p.update(production=legacy))
                before = self.files()
                self.service.sync_queue_states(self.pid)
                self.assertEqual(self.files(), before)
                self.service.paused = True
                self.service.sync_queue_states(self.pid)
                saved = self.store.load(self.pid)
                self.assertEqual(saved['chapters'][0]['status'], 'PAUSED')
                self.assertEqual(saved['production'], legacy)
                self.service.paused = False
                self.service.sync_queue_states(self.pid)

    def test_non_timing_completed_jobs_preserve_legacy_null_history(self):
        self.job()
        for legacy in (None, {'timings': None}):
            with self.subTest(production=legacy):
                self.store.mutate(self.pid, lambda p: p.update(production=legacy))
                before = self.files()
                self.service.sync_queue_states(self.pid, self.attempt(kind='produce-story'))
                self.service.sync_queue_states(self.pid, self.attempt(seconds=None))
                self.assertEqual(self.files(), before)

    def test_actual_timing_event_does_not_silently_repair_invalid_legacy_history(self):
        for legacy in (None, {'timings': None}):
            with self.subTest(production=legacy):
                self.store.mutate(self.pid, lambda p: p.update(production=legacy))
                before = self.files()
                with self.assertRaises((TypeError, AttributeError)):
                    self.service.sync_queue_states(self.pid, self.attempt())
                self.assertEqual(self.files(), before)


if __name__ == '__main__':
    unittest.main()
