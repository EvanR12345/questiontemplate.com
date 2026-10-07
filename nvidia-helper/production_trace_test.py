import json
import tempfile
import time
import unittest
from pathlib import Path
from production_trace import ProductionTrace, summarize, interval_union


class TraceTest(unittest.TestCase):
    def test_overlap_is_not_double_counted_and_unknown_rental_is_not_idle_proof(self):
        rows = [{'stage': 'Image', 'kind': 'stage', 'started': 10, 'finished': 20, 'seconds': 10},
                {'stage': 'Render', 'kind': 'stage', 'started': 15, 'finished': 30, 'seconds': 15}]
        report = summarize(rows, {'cloudStartedAt': 0, 'cloudStoppedAt': 40})
        self.assertEqual(report['coveredWallSeconds'], 20)
        self.assertEqual(report['rental']['unattributedSeconds'], 20)
        self.assertEqual(sum(s['sumSeconds'] for s in report['stages'].values()), 25)
        self.assertIn('not proof', report['rental']['note'])

    def test_nested_spans_failures_privacy_and_persistence(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'trace.sqlite3'
            trace = ProductionTrace(path)
            with trace.span('project', 'Chapter', {'chapter': 1, 'prompt': 'SECRET STORY', 'apiKey': 'SECRET KEY'}) as parent:
                with self.assertRaises(ValueError):
                    with trace.span('project', 'Images'):
                        raise ValueError('payload must not enter trace')
            report = trace.report('project')
            self.assertEqual(report['spans'][1]['parent'], parent)
            self.assertEqual(report['spans'][1]['status'], 'FAILED')
            self.assertNotIn('SECRET', json.dumps(report))
            trace.close()
            again = ProductionTrace(path)
            self.assertEqual(len(again.report('project')['spans']), 2)
            again.close()

    def test_crash_keeps_unknown_end_instead_of_counting_downtime(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'trace.sqlite3'
            trace = ProductionTrace(path)
            trace.begin('project', 'Running when helper crashed')
            trace.close()
            again = ProductionTrace(path)
            report = again.report('project')
            self.assertEqual(report['spans'][0]['status'], 'INTERRUPTED')
            self.assertIsNone(report['spans'][0]['finished'])
            self.assertEqual(report['summary']['coveredWallSeconds'], 0)
            self.assertEqual(report['summary']['unfinishedOrInterrupted'], 1)
            again.close()

    def test_job_parent_is_not_attributed_to_a_useful_rental_stage(self):
        report = summarize([{'stage': 'Queue', 'kind': 'job', 'started': 0, 'finished': 100, 'seconds': 100}],
                           {'cloudStartedAt': 0, 'cloudStoppedAt': 100})
        self.assertEqual(report['rental']['unattributedSeconds'], 100)
        self.assertEqual(interval_union([(0, 2), (1, 4), (8, 9), (9, 9)]), 5)


if __name__ == '__main__': unittest.main()
