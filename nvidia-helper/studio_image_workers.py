"""Two private Comfy processes on one owned GPU; bounded request admission."""
import copy
import threading
from urllib.parse import urlparse, urlunparse
from image_provider import ComfyImageProvider,RemoteGenerationUncertain,ImageAdmissionBlocked


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
        self.workers = [factory({**config, 'comfyEndpoint': endpoint}, request_slots=2)
                        for endpoint in worker_endpoints(config)]
        self.cv = threading.Condition()
        self.active = [0] * self.model_copies
        self.sequence = 0
        self.confirmation = None
        self.quarantined = False
        self.lifetime_verified = False
        self._lifetime_id = None

    def prepare_worker_lifetime(self):
        """Retire readiness metadata without forgiving unresolved remote work."""
        with self.cv:
            if any(self.active):raise RuntimeError('Image calls must drain before worker preparation.')
            self.confirmation=None
            self.lifetime_verified=False
            self.invalidate_backend_cache()

    def begin_worker_lifetime(self, proof=None):
        if proof is None:
            # Older preparation callers may invalidate readiness, never clear a
            # quarantine merely by reconnecting or polling health.
            return self.prepare_worker_lifetime()
        with self.cv:
            endpoint=getattr(self.workers[0], 'url', worker_endpoints(self.config)[0])
            if (not isinstance(proof,dict) or proof.get('owned') is not True
                    or proof.get('ready') is not True or proof.get('endpoint')!=endpoint
                    or not isinstance(proof.get('podId'),str) or not proof['podId'].strip()
                    or not isinstance(proof.get('lifetimeId'),str) or not proof['lifetimeId'].strip()):
                raise RuntimeError('Verified owned worker lifetime proof is required.')
            if not self.confirmation or any(self.active):
                raise RuntimeError('Verified worker startup and drained image calls are required.')
            token=proof['lifetimeId']
            if self.quarantined and token==self._lifetime_id:
                raise RuntimeError('The same worker lifetime still has unresolved remote work.')
            self.quarantined=True
            self.lifetime_verified=False
            for index,worker in enumerate(self.workers):
                begin=getattr(worker,'begin_worker_lifetime',None)
                if callable(begin):
                    begin({**proof,'endpoint':getattr(worker,'url',worker_endpoints(self.config)[index]),
                           'lifetimeId':token+':worker:'+str(index)})
            self._lifetime_id=token
            self.lifetime_verified=True
            self.quarantined=False
            self.cv.notify_all()

    def __getattr__(self, name):
        return getattr(self.workers[0], name)

    def confirm_workers(self, report):
        rows = report.get('workers', [])
        if (len(rows) != self.model_copies or len({r.get('pid') for r in rows}) != self.model_copies
                or any(type(r.get('pid')) is not int or r['pid'] <= 0 or r.get('port') != 8188+i
                       for i, r in enumerate(rows))
                or report.get('gpu') != 'NVIDIA GeForce RTX 5090'):
            raise RuntimeError('Separate same-GPU Comfy processes were not verified; no image submitted.')
        with self.cv:
            if any(self.active):raise RuntimeError('Image calls must drain before process confirmation.')
            self.confirmation = copy.deepcopy(report)
            self.lifetime_verified = False

    def invalidate_backend_cache(self):
        for worker in self.workers:
            worker.invalidate_backend_cache()

    def healthCheck(self, **selection):
        rows = [w.healthCheck(**selection) for w in self.workers]
        result = copy.deepcopy(rows[0])
        result.update(installed=all(row.get('installed') for row in rows), modelCopies=self.model_copies,
                      requestSlots=self.client_capacity, multipleModelCopiesVerified=bool(self.confirmation),
                      workerLifetimeVerified=self.lifetime_verified,
                      workerHealth=[{'index': i, 'installed': bool(row.get('installed'))} for i, row in enumerate(rows)])
        return result

    def unload(self):
        for worker in self.workers:
            worker.unload()

    def generateImage(self, request, checkpoint):
        if not self.confirmation or not self.lifetime_verified:
            raise ImageAdmissionBlocked('Two-copy worker startup has not been verified; no image submitted.')
        index = None
        while index is None:
            checkpoint(0, request.get('settings', {}).get('steps', 1), 'Waiting for a cloud image worker')
            with self.cv:
                if not self.confirmation or not self.lifetime_verified:
                    raise ImageAdmissionBlocked('Two-copy worker startup has not been verified; no image submitted.')
                if self.quarantined:
                    raise ImageAdmissionBlocked('An image worker has unresolved remote work; reconcile and restart the owned rental before new dispatch.')
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
        except (RemoteGenerationUncertain,ImageAdmissionBlocked):
            with self.cv:
                # Publish the group fence only after every endpoint's pending
                # dispatches are blocked. These hooks only set Events: no child
                # admission mutex is taken while holding the pool condition.
                for worker in self.workers:
                    block=getattr(worker,'block_new_dispatch',None)
                    if callable(block):block()
                self.quarantined=True
            raise
        finally:
            with self.cv:
                self.active[index] -= 1
                self.cv.notify_all()


def make_comfy_provider(config):
    if model_copies(config) == 2:return ComfyWorkerPool(config)
    managed=bool(config.get('runpodManagedPodId') or config.get('runpodPreparedPodIds'))
    return ComfyImageProvider(config,request_slots=2 if managed else 1)
