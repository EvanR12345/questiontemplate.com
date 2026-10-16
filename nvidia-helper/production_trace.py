"""Durable, payload-free stage intervals. Overlapping durations are not additive."""
import contextvars
import json
import math
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

FIELDS = {'chapter', 'shot', 'intro', 'provider', 'model', 'jobId', 'seed',
          'estimatedUSD', 'serviceTier', 'reused', 'timingSource', 'detail',
          'executionId', 'inputHash', 'parallelDirector', 'latency',
          'inputTokens', 'tokens', 'reasoningTokens', 'visibleOutputTokens',
          'usagePending', 'reservedUSD'}


def interval_union(intervals):
    end = None
    total = 0.0
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        total += max(0, stop - max(start, end if end is not None else start))
        end = max(stop, end if end is not None else stop)
    return total


def window_coverage(intervals, start, end):
    """Account for a recorded window without pretending gaps are GPU idle."""
    clipped = sorted((max(start, a), min(end, b)) for a, b in intervals
                     if b > start and a < end and b > a)
    cursor = start
    gaps = []
    for a, b in clipped:
        if a > cursor:
            gaps.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < end:
        gaps.append((cursor, end))
    covered = interval_union(clipped)
    return {'seconds': end-start, 'recordedSeconds': covered,
            'unrecordedSeconds': max(0, end-start-covered),
            'gapCount': len(gaps),
            'largestGapSeconds': max((b-a for a,b in gaps), default=0),
            'beforeFirstRecordedSeconds': max(0, clipped[0][0]-start) if clipped else end-start,
            'afterLastRecordedSeconds': max(0, end-max(b for _,b in clipped)) if clipped else 0,
            'note': 'Gaps have no recorded process. They may include startup, transfer, pauses, idle or missing telemetry; they are not measured GPU idle.'}


def summarize(rows, rental=None):
    completed = [r for r in rows if r['finished'] is not None]
    by_stage = {}
    for r in completed:
        item = by_stage.setdefault(r['stage'], {'attempts': 0, 'sumSeconds': 0, 'intervals': []})
        item['attempts'] += 1
        item['sumSeconds'] += r['seconds']
        item['intervals'].append((r['started'], r['finished']))
    for item in by_stage.values():
        item['coveredWallSeconds'] = interval_union(item.pop('intervals'))
    report = {'stages': by_stage, 'unfinishedOrInterrupted': len(rows)-len(completed),
              'coveredWallSeconds': interval_union([(r['started'], r['finished']) for r in completed]),
              'note': 'Nested/overlapping stage sums cannot be added to get elapsed production time. '
                      'Intervals are client wall time, not GPU utilization or an invoice.'}
    activity = [(r['started'], r['finished']) for r in completed if r['kind'] != 'job']
    if activity:
        report['activity'] = window_coverage(activity, min(a for a,_ in activity), max(b for _,b in activity))
    if rental:
        start, end = rental.get('cloudStartedAt'), rental.get('cloudStoppedAt')
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in (start, end)) and end >= start:
            intervals = [(max(start, r['started']), min(end, r['finished']))
                         for r in completed if r['kind'] != 'job']
            coverage = window_coverage(intervals, start, end)
            report['rental'] = coverage | {'attributedStageSeconds': coverage['recordedSeconds'],
                                'unattributedSeconds': coverage['unrecordedSeconds'],
                                'startupToReadySeconds': rental['cloudReadyAt']-start
                                    if type(rental.get('cloudReadyAt')) in (int,float)
                                    and math.isfinite(rental['cloudReadyAt']) and start <= rental['cloudReadyAt'] <= end else None,
                                'note': 'Unattributed time is unknown/startup/transfer/idle; not proof of idle GPU.'}
    return report


class ProductionTrace:
    def __init__(self, path):
        self.lock = threading.RLock()
        self.parent = contextvars.ContextVar('studio_trace_parent_' + uuid.uuid4().hex, default=None)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS spans '
                        '(id TEXT PRIMARY KEY, project TEXT, parent TEXT, stage TEXT, kind TEXT, '
                        'started REAL, finished REAL, seconds REAL, status TEXT, metadata TEXT)')
        self.db.execute('CREATE INDEX IF NOT EXISTS spans_project_started ON spans(project,started)')
        # A crash does not establish an end time. Never charge the whole downtime.
        self.db.execute("UPDATE spans SET status='INTERRUPTED' WHERE status='RUNNING'")
        self.db.commit()

    def begin(self, project, stage, details=None, kind='stage'):
        identity = uuid.uuid4().hex
        metadata = {k: v for k, v in (details or {}).items() if k in FIELDS}
        with self.lock:
            self.db.execute('INSERT INTO spans VALUES(?,?,?,?,?,?,?,?,?,?)',
                            (identity, project, self.parent.get(), stage, kind, time.time(), None,
                             None, 'RUNNING', json.dumps(metadata)))
            self.db.commit()
        return identity

    def finish(self, identity, seconds, status):
        with self.lock:
            self.db.execute('UPDATE spans SET finished=started+?,seconds=?,status=? WHERE id=?',
                            (seconds, seconds, status, identity))
            self.db.commit()

    @contextmanager
    def span(self, project, stage, details=None, kind='stage'):
        identity = self.begin(project, stage, details, kind)
        token = self.parent.set(identity)
        began = time.monotonic()
        status = 'COMPLETE'
        try:
            yield identity
        except BaseException as error:
            status = 'CANCELLED' if type(error).__name__ == 'JobCancelled' else 'FAILED'
            raise
        finally:
            self.finish(identity, time.monotonic()-began, status)
            self.parent.reset(token)

    def record(self, project, stage, seconds, details=None):
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            raise ValueError('Invalid recorded trace duration.')
        identity = self.begin(project, stage, details, 'detail')
        finished = time.time()
        with self.lock:
            self.db.execute('UPDATE spans SET started=?,finished=?,seconds=?,status=? WHERE id=?',
                            (finished-seconds, finished, seconds, (details or {}).get('status', 'COMPLETE'), identity))
            self.db.commit()

    def report(self, project, rental=None):
        with self.lock:
            rows = [dict(r) for r in self.db.execute('SELECT * FROM spans WHERE project=? ORDER BY started', (project,))]
        for row in rows:
            row['metadata'] = json.loads(row['metadata'])
        return {'project': project, 'spans': rows, 'summary': summarize(rows, rental)}

    def close(self):
        with self.lock:
            self.db.close()
