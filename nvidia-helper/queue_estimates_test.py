import unittest
from queue_estimates import remaining_time, timing_profile


class QueueEstimatesTest(unittest.TestCase):
    def job(self, status='QUEUED', **changes):
        return {'kind': 'image', 'timingProfile': 'cloud-reference', 'status': status, 'started': 80} | changes

    def test_overdue_running_image_does_not_count_as_zero_remaining_work(self):
        result = remaining_time([self.job('RUNNING'), self.job()], {'cloud-reference': [5, 6, 7]}, 100)
        self.assertIsNone(result['etaSeconds'])
        self.assertIsNone(result['etaRangeSeconds'])
        self.assertEqual(result['etaStatus'], 'LONGER_THAN_HISTORY')

    def test_running_tail_uses_matching_longer_observations(self):
        result = remaining_time([self.job('RUNNING'), self.job()], {'cloud-reference': [10, 30, 40]}, 100)
        self.assertEqual(result['etaSeconds'], 42)
        self.assertEqual(result['etaRangeSeconds'], [20, 60])

    def test_unknown_workflow_and_composite_production_are_not_zero_cost_work(self):
        for job in (self.job(timingProfile='new-model'), self.job(kind='produce-story')):
            result = remaining_time([job], {'cloud-reference': [5]}, 100)
            self.assertIsNone(result['etaSeconds'])
            self.assertEqual(result['etaStatus'], 'INSUFFICIENT_HISTORY')

    def test_no_pending_work_is_zero_and_subsecond_pending_work_is_not_idle(self):
        self.assertEqual(remaining_time([], {}, 100)['etaStatus'], 'IDLE')
        result = remaining_time([self.job()], {'cloud-reference': [.2]}, 100)
        self.assertEqual(result['etaSeconds'], 1)
        self.assertEqual(result['etaStatus'], 'ESTIMATED')

    def test_failed_or_invalid_duration_is_not_an_observation(self):
        result = remaining_time([self.job()], {'cloud-reference': [True, float('nan'), float('inf'), -3, 8]}, 100)
        self.assertEqual(result['etaRangeSeconds'], [8, 8])
        self.assertEqual(result['etaSeconds'], 8)

    def test_deferred_review_is_not_estimated_from_image_plus_review_durations(self):
        payload = {'shotSnapshot': {'imageProvider': 'comfyui', 'imageModel': 'klein',
                   'workflow': 'reference', 'generationSettings': {'width': 1344, 'height': 768, 'steps': 4}},
                   'qcTimingProfile': [True, 'BALANCED']}
        inline = timing_profile('image', payload)
        deferred = timing_profile('image', payload | {'deferQC': True})
        self.assertNotEqual(inline, deferred)
        result = remaining_time([self.job(timingProfile=deferred)], {inline: [18], deferred: [5]}, 100)
        self.assertEqual(result['etaSeconds'], 5)
        self.assertEqual(timing_profile('image', payload | {'deferQC': False}), inline)


if __name__ == '__main__': unittest.main()
