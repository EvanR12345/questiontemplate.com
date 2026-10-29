"""Chapter status no-ops retain a fresh read without publishing new snapshots."""
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

import studio_data
from studio_data import ProjectStore, new_project
from studio_service import StudioService


class StatusWriteTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = ProjectStore(self.temp.name)
        self.project = self.store.save(new_project('Status fixture'))
        self.pid = self.project['id']
        self.chid = self.project['chapters'][0]['id']
        self.folder = self.store.folder(self.pid)
        self.service = StudioService.__new__(StudioService)
        self.service.store = self.store

    def tearDown(self):
        self.temp.cleanup()

    def snapshots(self):
        return {p.name: p.read_bytes() for p in self.folder.iterdir() if p.is_file()}

    def test_same_status_keeps_revision_bytes_backup_and_archive_queue(self):
        self.service.status(self.pid, self.chid, 'QC')
        before = self.snapshots()
        archive = self.store.archive = Mock()
        with patch.object(studio_data, 'validate_project', wraps=studio_data.validate_project) as validate:
            for _ in range(4):
                self.assertIsNone(self.service.status(self.pid, self.chid, 'QC'))
            validate.assert_not_called()
        self.assertEqual(self.snapshots(), before)
        archive.enqueue.assert_not_called()

    def test_real_transition_validates_publishes_and_preserves_exact_previous(self):
        before = (self.folder / 'project.json').read_bytes()
        archive = self.store.archive = Mock()
        with patch.object(studio_data, 'validate_project', wraps=studio_data.validate_project) as validate:
            self.assertIsNone(self.service.status(self.pid, self.chid, 'QC'))
            self.assertEqual(validate.call_count, 1)
        saved = self.store.load(self.pid)
        self.assertEqual(saved['chapters'][0]['status'], 'QC')
        self.assertEqual(saved['revision'], self.project['revision'] + 1)
        self.assertEqual((self.folder / 'project.previous.json').read_bytes(), before)
        archive.enqueue.assert_called_once_with(self.pid)

    def test_noop_does_not_cache_status_across_external_edits(self):
        self.service.status(self.pid, self.chid, 'QC')
        path = self.folder / 'project.json'
        external = json.loads(path.read_bytes())
        external['chapters'][0]['status'] = 'GENERATING'
        external['revision'] += 10
        path.write_text(json.dumps(external), encoding='utf-8')
        before = path.read_bytes()
        self.service.status(self.pid, self.chid, 'QC')
        self.assertEqual(self.store.load(self.pid)['revision'], external['revision'] + 1)
        self.assertEqual((self.folder / 'project.previous.json').read_bytes(), before)

    def test_invalid_status_fails_without_changing_saved_bytes(self):
        before = self.snapshots()
        with self.assertRaisesRegex(ValueError, 'Invalid chapter status'):
            self.service.status(self.pid, self.chid, 'UNSUPPORTED')
        self.assertEqual(self.snapshots(), before)

    def test_real_transition_still_rejects_invalid_project(self):
        path = self.folder / 'project.json'
        invalid = json.loads(path.read_bytes())
        invalid['chapters'][0]['sourceText'] = 123
        path.write_text(json.dumps(invalid), encoding='utf-8')
        before = self.snapshots()
        with self.assertRaises((ValueError, TypeError)):
            self.service.status(self.pid, self.chid, 'QC')
        self.assertEqual(self.snapshots(), before)

    def test_simultaneous_same_transition_publishes_once(self):
        archive = self.store.archive = Mock()
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.service.status(self.pid, self.chid, 'QC'), range(16)))
        self.assertEqual(self.store.load(self.pid)['revision'], self.project['revision'] + 1)
        archive.enqueue.assert_called_once_with(self.pid)

    def test_no_chapter_keeps_existing_noop_contract(self):
        with patch.object(self.store, 'mutate') as mutate:
            self.assertIsNone(self.service.status(self.pid, None, 'QC'))
            mutate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
