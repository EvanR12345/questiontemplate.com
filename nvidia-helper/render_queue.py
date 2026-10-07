"""Bounded clip dispatch. State callbacks stay in the owning queue thread."""
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import threading


class ClipPoolStopped(Exception):
    pass


def render_unique_clips(jobs, render, gate, workers=1, poll_seconds=.15, capacity=None):
    """Preserve order, deduplicate output identity, and bound in-flight work.

    jobs: sequence of (cache_identity, immutable_input).
    render(input, worker_gate): save atomically before returning the result.
    gate(message): owner callback; may block for pause or raise for cancellation.
    Worker exceptions cancel peers; successfully saved results remain available.
    """
    if isinstance(workers, bool) or not isinstance(workers, int) or not 1 <= workers <= 3:
        raise ValueError('Use one to three render workers.')
    unique = {}
    order = []
    for key, data in jobs:
        order.append(key)
        if key in unique and unique[key] != data:
            raise ValueError('Conflicting inputs for one cached clip.')
        unique.setdefault(key, data)
    results = {}
    items = iter(unique.items())
    stop = threading.Event()
    def worker_gate(*_):
        if stop.is_set():
            raise ClipPoolStopped('Clip pool stopped; completed cache files are retained.')
    def work(item):
        key, data = item
        worker_gate()
        return key, render(data, worker_gate)
    executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix='studio-clip')
    pending = set()
    exhausted = False
    def fill():
        nonlocal exhausted
        limit = capacity(len(pending)) if capacity else workers
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= workers:
            raise ValueError('Render capacity must be between one and the configured worker limit.')
        while not exhausted and len(pending) < limit:
            item = next(items, None)
            if item is None:
                exhausted = True
                break
            gate(f'Rendering clips {len(results)} / {len(unique)}')
            pending.add(executor.submit(work, item))
    try:
        gate(f'Rendering clips 0 / {len(unique)}')
        fill()
        while pending:
            gate(f'Rendering clips {len(results)} / {len(unique)}')
            done, pending = wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
            # Inspect the entire finished set before dispatching replacements.
            completed = [future.result() for future in done]
            results.update(completed)
            fill()
        gate(f'Rendered clips {len(results)} / {len(unique)}')
        return [results[key] for key in order]
    except BaseException:
        stop.set()
        for future in pending:
            future.cancel()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
