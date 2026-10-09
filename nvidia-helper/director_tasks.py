"""Bounded independent director work; chronological story state stays serial."""
import contextvars
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait


def validate_director_result(method, context, result):
    if method=='writeImagePrompt':
        entries=result.get('prompts',[])
        indices=[entry.get('shotIndex') for entry in entries]
        if (any(type(index) is not int for index in indices)
                or sorted(indices)!=list(range(len(context['shots'])))
                or any(not isinstance(entry.get('prompt'),str) or not entry['prompt'].strip() for entry in entries)):
            raise ValueError('Director prompt batch has missing, duplicate or empty shot prompts. '
                'Completed responses are cached; existing shots and manual edits were retained.')
    if method=='selectImageWorkflow':
        matches=[provider for provider in context['available']
            if provider['provider']==result['provider']
            and result['model'] in provider['models']
            and result['workflow'] in provider['workflows']]
        if not matches:
            raise ValueError('Director selected an unavailable image model/workflow. Review settings and retry.')
    return result


def bounded_director_map(items, execute, gate, limit=3):
    """Return input order with a bounded admission window, no automatic retries.

    Each execute callback must own its adapter, budget reservation and journal
    context. After a failure, already submitted work drains for its receipts;
    no new item is admitted. Failed or cancelled work is never silently replayed.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 3:
        raise ValueError('Choose one, two or three parallel director calls.')
    iterator = iter(enumerate(items))
    results = {}
    stopped = threading.Event()
    pending = {}
    failure = None
    exhausted = False

    def invoke(index, item):
        try:
            # Copy the trace parent, never share a mutable provider instance.
            gate('Preparing independent director pass')
            if stopped.is_set():
                return None
            return execute(item, stopped)
        except BaseException:
            stopped.set()
            raise

    with ThreadPoolExecutor(max_workers=limit, thread_name_prefix='studio-director') as pool:
        try:
            while pending or not exhausted:
                # Drain and surface all completed errors before admitting more.
                done = [future for future in pending if future.done()]
                for future in done:
                    index = pending.pop(future)
                    try:
                        results[index] = future.result()
                    except BaseException as error:
                        if failure is None:
                            failure = error
                if failure is not None or stopped.is_set():
                    exhausted = True
                while not exhausted and len(pending) < limit:
                    gate('Preparing independent director pass')
                    if stopped.is_set():
                        exhausted = True
                        break
                    try:
                        index, item = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    future = pool.submit(contextvars.copy_context().run, invoke, index, item)
                    pending[future] = index
                if pending:
                    wait(pending, timeout=.1, return_when=FIRST_COMPLETED)
                    # The ordinary gate checks cancellation and pause. Already
                    # paid streams have their separate non-pausing receive gate.
                    gate('Waiting for independent director passes')
        except BaseException as error:
            stopped.set()
            if failure is None:
                failure = error
        finally:
            # Cancel only callbacks that never started. Active callbacks finish
            # or preserve their own UNKNOWN liability on true cancellation.
            for future in pending:
                future.cancel()
            for future, index in pending.items():
                if future.cancelled():
                    continue
                try:
                    results[index] = future.result()
                except BaseException as error:
                    if failure is None:
                        failure = error
    if failure is not None:
        raise failure
    return [results[index] for index in sorted(results)]
