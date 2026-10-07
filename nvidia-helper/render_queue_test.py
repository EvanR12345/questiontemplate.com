import threading
import time
import unittest
from render_queue import render_unique_clips, ClipPoolStopped


class PoolTest(unittest.TestCase):
    def test_capacity_can_shrink_without_discarding_current_outputs(self):
        owner = threading.get_ident()
        limits, completed = [], []
        def capacity(active):
            self.assertEqual(threading.get_ident(), owner)
            limit = 3 if not limits else 1
            limits.append((active,limit))
            return limit
        def render(value, gate):
            time.sleep(.005)
            gate()
            completed.append(value)
            return value
        values = render_unique_clips([(str(i),i) for i in range(12)],render,lambda *_:None,
                                     workers=3,poll_seconds=.001,capacity=capacity)
        self.assertEqual(values,list(range(12)))
        self.assertEqual(sorted(completed),values)
        self.assertEqual(limits[0],(0,3))
        self.assertTrue(all(limit==1 for _,limit in limits[1:]))

    def test_invalid_dynamic_capacity_cannot_dispatch_jobs(self):
        called = []
        with self.assertRaisesRegex(ValueError,'capacity'):
            render_unique_clips([('one',1)],lambda *args:called.append(args),lambda *_:None,
                                workers=3,capacity=lambda active:0)
        self.assertFalse(called)

    def test_bounded_parallel_work_preserves_timeline_order_and_deduplicates(self):
        lock = threading.Lock()
        active = peak = 0
        calls = []
        def render(data, gate):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
                calls.append(data)
            gate()
            time.sleep((8-data)*.002)
            with lock:
                active -= 1
            return data*10
        jobs = [(str(i), i) for i in range(7)] + [('2', 2)]
        result = render_unique_clips(jobs, render, lambda *_:None, workers=3, poll_seconds=.001)
        self.assertEqual(result, [0,10,20,30,40,50,60,20])
        self.assertEqual(sorted(calls), list(range(7)))
        self.assertEqual(peak, 3)

    def test_state_callback_is_only_called_in_owner_thread(self):
        owner = threading.get_ident()
        seen = []
        def gate(_):
            seen.append(threading.get_ident())
        render_unique_clips([(str(i), i) for i in range(10)], lambda data, g:data, gate, workers=3)
        self.assertEqual(set(seen), {owner})

    def test_cancel_stops_peers_and_never_starts_the_whole_queue(self):
        entered = threading.Event()
        stopped = []
        calls = []
        def render(data, gate):
            calls.append(data)
            entered.set()
            try:
                for _ in range(1000):
                    gate()
                    time.sleep(.001)
            except ClipPoolStopped:
                stopped.append(data)
                raise
        def owner_gate(_):
            if entered.is_set():
                raise RuntimeError('User cancelled')
        with self.assertRaisesRegex(RuntimeError, 'User cancelled'):
            render_unique_clips([(str(i), i) for i in range(1000)], render, owner_gate, workers=3)
        self.assertLessEqual(len(calls), 3)
        self.assertEqual(sorted(stopped), sorted(calls))

    def test_worker_failure_stops_dispatch_and_does_not_discard_saved_results(self):
        saved = []
        ready = threading.Event()
        def render(data, gate):
            if data == 1:
                ready.wait(1)
                raise ValueError('Encoder failed')
            if data == 0:
                saved.append('complete-0.mp4')
                ready.set()
                return saved[-1]
            while True:
                gate()
                time.sleep(.001)
        with self.assertRaisesRegex(ValueError, 'Encoder failed'):
            render_unique_clips([(str(i), i) for i in range(30)], render, lambda *_:None, workers=3)
        self.assertEqual(saved, ['complete-0.mp4'])

    def test_pause_lets_current_clips_save_but_stops_new_claims(self):
        started, saved = [], []
        state = {'pause': False, 'seenPause': False}
        def render(data, gate):
            started.append(data)
            time.sleep(.005)
            gate()
            saved.append(data)
            return data
        def gate(_):
            if started and not state['seenPause']:
                state['seenPause'] = True
                state['pause'] = True
                before = len(started)
                time.sleep(.025)
                self.assertEqual(len(started), before)
                self.assertGreater(len(saved), 0)
                state['pause'] = False
        result = render_unique_clips([(str(i), i) for i in range(9)], render, gate, workers=3, poll_seconds=.001)
        self.assertEqual(result, list(range(9)))
        self.assertTrue(state['seenPause'])

    def test_invalid_workers_or_conflicting_cache_identity_fail_before_work(self):
        def render(*_):
            self.fail('No work should be dispatched')
        for workers in (0,4,True,1.5):
            with self.assertRaises(ValueError):
                render_unique_clips([('a', 1)], render, lambda *_:None, workers=workers)
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            render_unique_clips([('a', 1), ('a', 2)], render, lambda *_:None)


if __name__ == '__main__':
    unittest.main()
