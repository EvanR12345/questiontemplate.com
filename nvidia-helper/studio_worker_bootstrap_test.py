import ast,json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio_worker_bootstrap import MODELS,remote_script
from studio_runpod import ProductionGPU,ManagedGPUError

class BootstrapTest(unittest.TestCase):
    def test_pinned_manifest_matches_existing_verified_downloads(self):
        manifest=Path(__file__).resolve().parents[2]/'studio-runtime/cloud-setup/flux-model-manifest.json'
        actual=[{k:m[k] for k in ('url','destination','bytes','sha256')} for m in json.loads(manifest.read_text())]
        self.assertEqual(MODELS,actual)
        ast.parse(remote_script())
        self.assertNotIn('read_bytes',remote_script())

    def rental(self,folder,gate=lambda *_:None):
        r=ProductionGPU({'runpodManagedPodId':'worker-test-123','runpodSSHKeyFile':'private-key'},
            Path(folder)/'lease.json','p',{'gpuBudgetUSD':1},SimpleNamespace(),gate,clock=lambda:100)
        r.owned=True;r.started=90;r.deadline=200
        return r

    def test_disabled_by_default_and_pool_mismatch_rejected(self):
        with tempfile.TemporaryDirectory() as d,patch('studio_runpod.subprocess.Popen') as popen,patch('studio_runpod.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            r=self.rental(d);r.bootstrap({});popen.assert_not_called()
            r.config.update(runpodBootstrapKleinPodIds=[r.pod],runpodPreparedPodIds=['another-worker-123'])
            with self.assertRaisesRegex(ManagedGPUError,'outside'):r.bootstrap({})
            popen.assert_not_called()

    def test_verified_remote_download_once_no_keys_in_stdin(self):
        with tempfile.TemporaryDirectory() as d,patch('studio_runpod.subprocess.Popen') as popen,patch('studio_runpod.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            r=self.rental(d);r.config['runpodBootstrapKleinPodIds']=[r.pod]
            process=Mock(returncode=0);process.communicate.return_value=('MODELS_VERIFIED','');process.poll.return_value=0
            popen.return_value=process
            pod={'ssh':{'direct':{'host':'192.0.2.1','port':22,'username':'root'}}}
            r.bootstrap(pod);r.bootstrap(pod)
            self.assertEqual(popen.call_count,1)
            self.assertEqual(process.communicate.call_args.kwargs['input'],remote_script())
            self.assertNotIn('private-key',remote_script())

    def test_failure_does_not_mark_ready(self):
        with tempfile.TemporaryDirectory() as d,patch('studio_runpod.subprocess.Popen') as popen,patch('studio_runpod.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            r=self.rental(d);r.config['runpodBootstrapKleinPodIds']=[r.pod]
            process=Mock(returncode=1);process.communicate.return_value=('','');process.poll.return_value=1
            popen.return_value=process
            with self.assertRaisesRegex(ManagedGPUError,'preparation failed'):
                r.bootstrap({'ssh':{'direct':{'host':'192.0.2.1','port':22,'username':'root'}}})
            self.assertFalse(getattr(r,'bootstrapped',False))

    def test_cancel_during_download_kills_transport_without_marking_ready(self):
        with tempfile.TemporaryDirectory() as d,patch('studio_runpod.subprocess.Popen') as popen,patch('studio_runpod.subprocess.run',return_value=SimpleNamespace(returncode=0)):
            calls=[]
            def gate(*_):
                calls.append(True)
                if len(calls)==4:raise RuntimeError('User cancelled')
            r=self.rental(d,gate);r.config['runpodBootstrapKleinPodIds']=[r.pod]
            process=Mock(returncode=None);process.poll.return_value=None
            process.communicate.side_effect=[subprocess.TimeoutExpired('ssh',1),('','')]
            popen.return_value=process
            with self.assertRaisesRegex(RuntimeError,'User cancelled'):
                r.bootstrap({'ssh':{'direct':{'host':'192.0.2.1','port':22,'username':'root'}}})
            process.kill.assert_called_once()
            self.assertFalse(getattr(r,'bootstrapped',False))

if __name__=='__main__':unittest.main()
