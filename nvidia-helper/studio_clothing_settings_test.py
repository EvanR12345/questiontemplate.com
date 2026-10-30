"""Project-scoped clothing preference changes preserve unrelated saved work."""
import copy
import tempfile
import unittest

from studio_data import ProjectStore, new_project
from studio_workspace import workspace_change, setup_snapshot
from studio_performance import profile_for


class ClothingSettingsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(self.tmp.name)
        self.project = self.store.save(new_project('Clothing preference'))

    def change(self, value, revision=None):
        current = self.store.load(self.project['id'])
        return workspace_change(self.store, current['id'], {
            'operation': 'set-clothing-continuity', 'value': value,
            'revision': current['revision'] if revision is None else revision})

    def test_exact_setting_change_preserves_source_voice_and_budgets_and_can_undo(self):
        before = copy.deepcopy(self.project)
        after = self.change('advisory')
        self.assertEqual(after['settings'], before['settings'] | {'qcClothingContinuity': 'advisory'})
        self.assertEqual(after['chapters'], before['chapters'])
        self.assertEqual(after['characters'], before['characters'])
        entry = after['actionHistory'][-1]
        self.assertTrue(entry['undoable'])
        undone = workspace_change(self.store, after['id'], {
            'operation': 'undo', 'revision': after['revision'], 'actionId': entry['id']})
        self.assertEqual(undone['settings'], before['settings'])

    def test_stale_revision_and_bad_values_cannot_change_project(self):
        for value, revision in [('advisory', -1), ('ignore-everything', None), (True, None)]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.change(value, revision)
            self.assertEqual(self.store.load(self.project['id']), self.project)

    def test_noop_does_not_create_history_or_revision_and_setups_keep_preference(self):
        same = self.change('required')
        self.assertEqual(same, self.project)
        after = self.change('advisory')
        self.assertEqual(setup_snapshot(after)['settings']['qcClothingContinuity'], 'advisory')

    def test_new_review_identity_does_not_mix_old_timings_and_voice_is_unchanged(self):
        before = copy.deepcopy(self.project)
        after = self.change('advisory')
        for stage in ('Visual QC', 'Image pipeline', 'Image + quality checks'):
            self.assertNotEqual(profile_for(stage, before, {}), profile_for(stage, after, {}))
        self.assertEqual(profile_for('Image generation', before, {}), profile_for('Image generation', after, {}))
        self.assertEqual(profile_for('Narration', before, {}), profile_for('Narration', after, {}))


if __name__ == '__main__':
    unittest.main()
