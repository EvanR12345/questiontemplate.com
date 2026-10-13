import ast,json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from studio_worker_bootstrap import MODELS,remote_script
from studio_runpod import ProductionGPU,ManagedGPUError,bootstrap_diagnostic

class BootstrapTest(unittest.TestCase):
    def test_cuda_child_uses_assigned_image_mapping_without_copying_private_keys(self):
        from studio_worker_bootstrap import cuda_smoke_script
        with tempfile.TemporaryDirectory() as folder:
            environ=Path(folder)/'environ'
            environ.write_bytes(b'CUDA_VISIBLE_DEVICES=0\0LD_LIBRARY_PATH=/native/driver\0OPENAI_API_KEY=do-not-copy\0')
            namespace={'pathlib':SimpleNamespace(Path=lambda *_:environ),'os':SimpleNamespace(environ={'CUDA_VISIBLE_DEVICES':'wrong','OTHER':'keep'})}
            function=next(item for item in ast.parse(cuda_smoke_script(2)).body if isinstance(item,ast.FunctionDef) and item.name=='image_gpu_environment')
            exec(compile(ast.Module(body=[function],type_ignores=[]),'cuda-environment-test','exec'),namespace)
            result=namespace['image_gpu_environment']()
            self.assertEqual(result['CUDA_VISIBLE_DEVICES'],'0');self.assertEqual(result['LD_LIBRARY_PATH'],'/native/driver')
            self.assertEqual(result['OTHER'],'keep');self.assertNotIn('OPENAI_API_KEY',result)
    def test_owned_launcher_recovery_refuses_occupied_port_and_custom_arguments(self):
        import os
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'comfy';root.mkdir();proc=Path(folder)/'proc';proc.mkdir()
            class Paths:
                @staticmethod
                def Path(value):return proc if str(value)=='/proc' else Path(value)
            function=next(item for item in ast.parse(remote_script(2)).body if isinstance(item,ast.FunctionDef) and item.name=='recover_owned_base')
            sock=Mock();sock.bind.side_effect=OSError('Occupied')
            popen=Mock();namespace={'pathlib':Paths,'os':os,'root':root,'socket':SimpleNamespace(socket=lambda:sock),
                                   'subprocess':SimpleNamespace(Popen=popen),'json':json}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'owned-recovery-test','exec'),namespace)
            self.assertFalse(namespace['recover_owned_base']());popen.assert_not_called();sock.close.assert_called_once()
            sock.bind.side_effect=None;(root.parent/'comfyui_args.txt').write_text('--force-fp16\n')
            with self.assertRaisesRegex(RuntimeError,'custom arguments'):namespace['recover_owned_base']()
            popen.assert_not_called()
    def test_socket_owner_discovery_distinguishes_supervisor_and_python_model_process(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'comfy';root.mkdir();proc=Path(folder)/'proc';(proc/'net').mkdir(parents=True)
            (proc/'net'/'tcp').write_text('header\n0: 00000000:1FFC 00000000:0000 0A 0:0 0:0 0 0 0 42\n')
            (proc/'net'/'tcp6').write_text('header\n');links={}
            for pid,executable,socket,args in [(101,'uv','42',['uv','run','python','main.py']),
                    (202,'python3.12','42',[str(root/'.venv/bin/python'),'main.py']),
                    (303,'python3.12','43',['python','main.py'])]:
                process=proc/str(pid);(process/'fd').mkdir(parents=True)
                (process/'cmdline').write_bytes(('\0'.join(args)+'\0').encode());(process/'fd'/'4').touch()
                links[str(process/'exe')]='/usr/bin/'+executable;links[str(process/'cwd')]=str(root)
                links[str(process/'fd'/'4')]='socket:['+socket+']'
            class Paths:
                @staticmethod
                def Path(value):
                    return Path(folder)/str(value).lstrip('/') if str(value).startswith('/proc') else Path(value)
            def readlink(value):
                try:return links[str(value)]
                except KeyError:raise OSError('Unknown link')
            function=next(item for item in ast.parse(remote_script(2)).body if isinstance(item,ast.FunctionDef) and item.name=='base_process')
            namespace={'pathlib':Paths,'os':SimpleNamespace(readlink=readlink),'root':root.resolve()}
            exec(compile(ast.Module(body=[function],type_ignores=[]),'socket-owner-test','exec'),namespace)
            pid,args=namespace['base_process']()
            self.assertEqual(pid,202);self.assertEqual(args[0],str(root/'.venv/bin/python'))
    def test_bootstrap_errors_keep_cause_but_redact_urls_and_credentials_before_save(self):
        text='HTTP https://example.test/model?token=secret hf_syntheticToken sk-privateExample rpa_testKey\nRuntimeError: Unknown fast runtime flag'
        result=bootstrap_diagnostic(text)
        self.assertNotIn('token=secret',result);self.assertNotIn('syntheticToken',result)
        self.assertNotIn('privateExample',result);self.assertNotIn('testKey',result)
        self.assertIn('Unknown fast runtime flag',result)
    def test_pinned_manifest_matches_existing_verified_downloads(self):
        manifest=Path(__file__).resolve().parents[2]/'studio-runtime/cloud-setup/flux-model-manifest.json'
        actual=[{k:m[k] for k in ('url','destination','bytes','sha256')} for m in json.loads(manifest.read_text())]
        self.assertEqual(MODELS,actual)
        ast.parse(remote_script())
        # Multi-GB model hashing/downloading stays streamed; only tiny /proc
        # environment records may be read at once for runtime preservation.
        functions=[item for item in ast.parse(remote_script()).body if isinstance(item,ast.FunctionDef) and item.name in ('digest','one')]
        self.assertEqual(len(functions),2)
        self.assertFalse(any(isinstance(node,ast.Attribute) and node.attr=='read_bytes' for function in functions for node in ast.walk(function)))

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
