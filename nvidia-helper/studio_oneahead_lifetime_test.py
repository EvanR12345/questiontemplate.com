"""Free startup/lifetime checks; all provider operations stay in process."""
import datetime
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_runpod import ManagedGPUError, ProductionGPU
from studio_runpod_test import Client


class Provider:
    url = 'http://127.0.0.1:8188'
    client_capacity = 2

    def __init__(self, installed=(True,)):
        self.installed = list(installed)
        self.events = []
        self.proofs = []
        self.fenced = True
        self.confirmation = None
        self.on_health = None
        self.on_begin = None

    def prepare_worker_lifetime(self):
        self.events.append('prepare')
        self.confirmation = None
        # Preparing metadata must not retire an uncertain generation.
        assert self.fenced

    def invalidate_backend_cache(self):
        self.events.append('invalidate')

    def healthCheck(self, **selection):
        self.events.append(('health', self.fenced, self.confirmation))
        if self.on_health:
            self.on_health()
        installed = self.installed.pop(0) if len(self.installed) > 1 else self.installed[0]
        return {'installed': installed}

    def begin_worker_lifetime(self, proof):
        self.events.append('begin')
        if self.on_begin:
            self.on_begin(proof)
        self.proofs.append(dict(proof))
        self.fenced = False


class OneAheadLifetimeTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name) / 'lease.json'
        self.config = {'runpodManagedPodId': 'pod-test-123'}
        self.client = Client()
        self.clock = [100]
        self.provider = Provider()
        self.rentals = []
        self.threads = patch('studio_runpod.threading.Thread')
        self.threads.start()

    def tearDown(self):
        for rental in self.rentals:
            rental.stop()
        self.threads.stop()
        self.folder.cleanup()

    def rental(self, **options):
        rental = ProductionGPU(
            self.config, self.path, 'project', {'gpuBudgetUSD': 1},
            self.provider, lambda *_: None, client=self.client,
            connector=lambda *_: self.provider.events.append('connect'),
            clock=lambda: self.clock[0], **options)
        # No timing or external service is needed to advance fake health reads.
        rental.finished.wait = lambda *_: False
        def bootstrap(_):
            self.provider.events.append('bootstrap')
            self.provider.confirmation = 'new-process-proof'
        rental.bootstrap = bootstrap
        self.rentals.append(rental)
        return rental

    def test_verified_owned_readiness_precedes_reset_and_proof_is_persisted(self):
        rental = self.rental()
        def beginning(proof):
            saved = json.loads(self.path.read_text())
            self.assertTrue(rental.owned)
            self.assertEqual(saved['status'], 'STARTING')
            self.assertEqual(saved['workerLifetimeId'], proof['lifetimeId'])
            self.assertEqual(saved['workerLifetimeOrigin'], 'verified-stopped-start')
        self.provider.on_begin = beginning
        rental.ensure()
        self.assertEqual(self.provider.events,
                         ['connect', 'prepare', 'bootstrap', 'invalidate',
                          ('health', True, 'new-process-proof'), 'begin'])
        proof = self.provider.proofs[0]
        self.assertEqual(set(proof), {'owned', 'ready', 'podId', 'lifetimeId', 'endpoint'})
        self.assertTrue(proof['owned'] and proof['ready'])
        self.assertEqual(proof['podId'], 'pod-test-123')
        self.assertEqual(proof['endpoint'], self.provider.url)
        self.assertEqual(rental.state['status'], 'RUNNING')
        self.assertEqual(rental.state['requestSlots'], 2)
        self.assertTrue(rental.state['workerLifetimeVerified'])
        self.assertEqual(self.provider.confirmation, 'new-process-proof')
        rental.ensure()
        self.assertEqual(len(self.provider.proofs), 1)
        self.assertEqual(self.client.calls.count('start'), 1)

    def test_health_retries_keep_fence_and_prepare_only_once(self):
        self.provider = Provider((False, True))
        rental = self.rental()
        rental.ensure()
        health = [e for e in self.provider.events if isinstance(e, tuple)]
        self.assertEqual(len(health), 2)
        self.assertTrue(all(row[1] for row in health))
        self.assertEqual(self.provider.events.count('prepare'), 1)
        self.assertEqual(len(self.provider.proofs), 1)

    def test_failed_health_never_retires_unknown_lifetime(self):
        rental = self.rental()
        self.provider.on_health = lambda: (_ for _ in ()).throw(RuntimeError('not ready'))
        with self.assertRaisesRegex(RuntimeError, 'not ready'):
            rental.ensure()
        self.assertEqual(self.provider.proofs, [])
        self.assertTrue(self.provider.fenced)
        self.assertFalse(rental.owned)
        self.assertEqual(rental.state['status'], 'STOPPED')

    def test_shutdown_during_health_wins_over_ready_response(self):
        rental = self.rental()
        self.provider.on_health = lambda: rental.stop('stopped during health')
        with self.assertRaisesRegex(ManagedGPUError, 'closed during worker startup'):
            rental.ensure()
        self.assertEqual(self.provider.proofs, [])
        self.assertTrue(self.provider.fenced)
        self.assertFalse(rental.owned)
        self.assertEqual(self.client.calls.count('stop'), 1)

    def test_wrong_running_identity_stops_without_connect_or_reset(self):
        request = self.client.request
        wrong_seen = []
        def wrong_once(pod, action=None):
            row = request(pod, action)
            if action is None and row['status'] == 'RUNNING' and not wrong_seen:
                wrong_seen.append(True)
                return row | {'id': 'different-owned-pod'}
            return row
        self.client.request = wrong_once
        rental = self.rental()
        with self.assertRaisesRegex(ManagedGPUError, 'identity differs'):
            rental.ensure()
        self.assertEqual(self.provider.events, [])
        self.assertTrue(self.provider.fenced)
        self.assertEqual(self.client.calls.count('stop'), 1)

    def test_active_call_reset_refusal_stops_without_clearing_fence(self):
        rental = self.rental()
        self.provider.on_begin = lambda _: (_ for _ in ()).throw(RuntimeError('active calls'))
        with self.assertRaisesRegex(RuntimeError, 'active calls'):
            rental.ensure()
        self.assertEqual(self.provider.proofs, [])
        self.assertTrue(self.provider.fenced)
        self.assertFalse(rental.owned)

    def test_missing_endpoint_never_produces_ready_lifetime(self):
        self.provider.url = None
        rental = self.rental()
        with self.assertRaisesRegex(ManagedGPUError, 'endpoint is unavailable'):
            rental.ensure()
        self.assertEqual(self.provider.proofs, [])
        self.assertTrue(self.provider.fenced)

    def test_new_stopped_start_uses_new_token_even_at_same_clock_value(self):
        first = self.rental()
        first.ensure()
        first.stop()
        first_id = self.provider.proofs[0]['lifetimeId']
        self.provider.fenced = True
        second = self.rental()
        second.ensure()
        second_id = self.provider.proofs[1]['lifetimeId']
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.client.calls.count('start'), 2)
        self.assertEqual(second.state['workerLifetimeId'], second_id)

    def test_fresh_owned_startup_handoff_is_distinct_from_external_running_pod(self):
        name = 'studio-two-copy-5090-0123456789ab'
        receipt = {'project': 'project', 'podId': 'pod-test-123', 'name': name,
                   'started': 95, 'capUSD': 1}
        self.client.status = 'RUNNING'
        original = self.client.request
        def request(pod, action=None):
            row = original(pod, action)
            return row | {'name': name, 'createdAt': datetime.datetime.fromtimestamp(
                95, datetime.timezone.utc).isoformat(),
                'gpu': {'id': 'NVIDIA GeForce RTX 5090', 'count': 1}}
        self.client.request = request
        rental = self.rental(owned_startup=receipt)
        rental.ensure()
        self.assertNotIn('start', self.client.calls)
        self.assertEqual(rental.state['workerLifetimeOrigin'], 'fresh-owned-startup')
        self.assertEqual(len(self.provider.proofs), 1)
        self.assertTrue(rental.state['startupHandoff'])


if __name__ == '__main__':
    unittest.main()
