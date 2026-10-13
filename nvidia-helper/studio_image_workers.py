"""Two private Comfy processes on one owned GPU; bounded request admission."""
import copy
import threading
from urllib.parse import urlparse, urlunparse
from image_provider import ComfyImageProvider,RemoteGenerationUncertain


def model_copies(config):
    managed = bool(config.get('runpodManagedPodId') or config.get('runpodPreparedPodIds'))
    default = 2 if managed and config.get('runpodRequiredGPUType') == 'NVIDIA GeForce RTX 5090' else 1
    value = config.get('comfyModelCopies', default)
    if type(value) is not int or value not in (1, 2):
        raise ValueError('Choose one or two Comfy model copies on the same GPU.')
    return value


def worker_endpoints(config):
    count = model_copies(config)
    base = config.get('comfyEndpoint', 'http://127.0.0.1:8188')
    if count == 1:
        return [base]
    parsed = urlparse(base)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or not parsed.port or parsed.port >= 65535 or parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError('Two copies require adjacent private localhost SSH tunnel ports.')
    return [urlunparse(parsed._replace(netloc=f'127.0.0.1:{parsed.port+i}')) for i in range(count)]


class ComfyWorkerPool:
    id = 'comfyui'

    def __init__(self, config, factory=ComfyImageProvider):
        self.config = config
        self.model_copies = model_copies(config)
        self.client_capacity = self.model_copies * 2
        self.workers = [factory({**config, 'comfyEndpoint': endpoint}) for endpoint in worker_endpoints(config)]
        self.cv = threading.Condition()
        self.active = [0] * self.model_copies
        self.sequence = 0
        self.confirmation = None
        self.quarantined = False

    def begin_worker_lifetime(self):
        self.confirmation=None
        self.quarantined=False
        self.invalidate_backend_cache()

    def __getattr__(self, name):
        return getattr(self.workers[0], name)

    def confirm_workers(self, report):
        rows = report.get('workers', [])
        if (len(rows) != self.model_copies or len({r.get('pid') for r in rows}) != self.model_copies
                or any(type(r.get('pid')) is not int or r['pid'] <= 0 or r.get('port') != 8188+i
                       for i, r in enumerate(rows))
                or report.get('gpu') != 'NVIDIA GeForce RTX 5090'):
            raise RuntimeError('Separate same-GPU Comfy processes were not verified; no image submitted.')
        self.confirmation = copy.deepcopy(report)

    def invalidate_backend_cache(self):
        for worker in self.workers:
            worker.invalidate_backend_cache()

    def healthCheck(self, **selection):
        rows = [w.healthCheck(**selection) for w in self.workers]
        result = copy.deepcopy(rows[0])
        result.update(installed=all(row.get('installed') for row in rows), modelCopies=self.model_copies,
                      requestSlots=self.client_capacity, multipleModelCopiesVerified=bool(self.confirmation),
                      workerHealth=[{'index': i, 'installed': bool(row.get('installed'))} for i, row in enumerate(rows)])
        return result

    def unload(self):
        for worker in self.workers:
            worker.unload()

    def generateImage(self, request, checkpoint):
        if not self.confirmation:
            raise RuntimeError('Two-copy worker startup has not been verified; no image submitted.')
        index = None
        while index is None:
            checkpoint(0, request.get('settings', {}).get('steps', 1), 'Waiting for a cloud image worker')
            with self.cv:
                if self.quarantined:
                    raise RuntimeError('An image worker has unresolved remote work; reconcile and restart the owned rental before new dispatch.')
                eligible = [i for i, active in enumerate(self.active) if active < 2]
                if eligible:
                    index = min(eligible, key=lambda i: (self.active[i], (i-self.sequence) % self.model_copies))
                    self.active[index] += 1
                    self.sequence = (index+1) % self.model_copies
                else:
                    self.cv.wait(.05)
        try:
            owned = dict(request)
            observer = owned.get('_executionObserver')
            if observer:
                owned['_executionObserver'] = lambda event, details: observer(event, {**details, 'workerIndex': index})
            result = self.workers[index].generateImage(owned, checkpoint)
            result.update(workerIndex=index, modelCopies=self.model_copies, requestSlots=self.client_capacity)
            return result
        except RemoteGenerationUncertain:
            with self.cv:self.quarantined=True
            raise
        finally:
            with self.cv:
                self.active[index] -= 1
                self.cv.notify_all()


def make_comfy_provider(config):
    return ComfyWorkerPool(config) if model_copies(config) == 2 else ComfyImageProvider(config)
