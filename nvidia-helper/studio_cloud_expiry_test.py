"""Offline launch/SSH proof tests. Every key, pod and remote process is fake."""
import base64
import contextlib
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import shlex
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parent
APP=ROOT
sys.path.insert(0,str(APP))
import studio_cache_owner as owner
import studio_runpod_cache as cache
from studio_native_cache_test import Fixture,fake_live


class CloudExpiryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.script=self.root/'guard.py';self.script.write_text("# Fake offline source\nprint('never executed')\n")
        self.key=self.root/'fake-key';self.key.write_text('OFFLINE-ONLY-SECRET')
        self.config=self.root/'config.json';owner.write(self.config,{'runpodActionKeyFile':str(self.key)})
        self.profile={'cloudExpiryEnabled':True,'cloudExpiryScriptPath':str(self.script),
            'cloudExpiryScriptSHA256':owner.sha(self.script)}
        self.now=time.time();self.state={'name':'studio-two-copy-5090-012345abcdef','startedEpoch':self.now,
            'podId':'offlinepod123','setupDeadlineEpoch':self.now+110}
        self.plan={'jobId':'native-image-012345abcdef','deadlineEpoch':self.now+400,'image':owner.IMAGE,
            'dataCenter':'EU-RO-1','volumeId':'offlinevolume123','configSourcePath':str(self.config),
            'configSourceSHA256':owner.sha(self.config)}
        self.job=object.__new__(owner.Job);self.job.root=self.root;self.job.folder=self.root/'job';self.job.folder.mkdir()
        self.job.plan=self.plan;self.job.verify_profile=Mock(return_value=self.profile)
        self.expected={'podId':self.state['podId'],'name':self.state['name'],'nonce':self.plan['jobId'],
            'created':self.now,'deadline':self.plan['deadlineEpoch'],'scriptSHA256':owner.sha(self.script)}
        self.proof={'version':1,'status':'ARMED','podId':self.state['podId'],'name':self.state['name'],
            'deadline':self.plan['deadlineEpoch'],'scriptSHA256':owner.sha(self.script),'guardPID':123,
            'atEpoch':self.now,'checkedAtEpoch':self.now,'guardProcessAlive':True,'cloudResident':True,'nonceMatched':True}
        self.pod={'id':self.state['podId'],'name':self.state['name'],'env':{'QT_CLOUD_NONCE':self.expected['nonce'],
            'QT_CLOUD_CONFIG':json.dumps({k:self.expected[k] for k in ('name','nonce','created','deadline')})}}
    def tearDown(self):self.temp.cleanup()

    def test_disabled_body_equals_legacy_and_never_reads_key_or_script(self):
        self.job.verify_profile.return_value={'cloudExpiryEnabled':False,'cloudExpiryScriptPath':'does-not-exist'}
        with patch.object(Path,'read_text',side_effect=AssertionError('key must not be read')):
            actual=self.job.create_body(self.state,'ssh-ed25519 OFFLINEPUBLIC')
        expected={'name':self.state['name'],'cloud':'SECURE','image':self.plan['image'],
            'gpu':{'id':owner.GPU,'count':1,'minCudaVersion':'12.8'},'dataCenterIds':['EU-RO-1'],
            'disk':80,'mounts':{'network':[{'volumeId':'offlinevolume123','path':'/runpod-volume'}]},
            'ports':['22/tcp'],'startSsh':True,'startJupyter':False,'env':{'PUBLIC_KEY':'ssh-ed25519 OFFLINEPUBLIC'}}
        self.assertEqual(actual,expected)
        self.job.verify_profile.return_value={}
        self.assertEqual(self.job.create_body(self.state,'ssh-ed25519 OFFLINEPUBLIC'),expected)

    def test_optin_body_has_original_deadline_self_only_and_credential_only_in_env(self):
        body=self.job.create_body(self.state,'ssh-ed25519 OFFLINEPUBLIC')
        config=json.loads(body['env']['QT_CLOUD_CONFIG'])
        self.assertEqual(config,{k:self.expected[k] for k in ('name','nonce','created','deadline')})
        self.assertNotIn('volume',config)
        self.assertEqual(body['entrypoint'],['/bin/bash','-lc'])
        self.assertEqual(body['env']['QT_CLOUD_API_KEY'],'OFFLINE-ONLY-SECRET')
        self.assertEqual(body['env']['QT_CLOUD_NONCE'],self.plan['jobId'])
        self.assertNotIn('OFFLINE-ONLY-SECRET',body['cmd'][0])
        self.assertTrue(body['cmd'][0].endswith('; exec /start.sh'))
        self.assertIn('os.kill(p,0)',body['cmd'][0]);self.assertIn('time.monotonic()+60',body['cmd'][0])
        payload=shlex.split(body['cmd'][0])[6]
        self.assertEqual(base64.b64decode(payload),self.script.read_bytes())
        self.assertEqual(body['gpu']['id'],'NVIDIA GeForce RTX 5090')

    def test_script_change_rejected_before_body_or_credential_read(self):
        self.script.write_text('raise RuntimeError()\n')
        with patch.object(Path,'read_text',side_effect=AssertionError('key read')):
            with self.assertRaises(ValueError):self.job.create_body(self.state,'ssh-ed25519 OFFLINEPUBLIC')

    def test_optin_rejects_malformed_boolean_path_hash_and_window(self):
        for change in ({'cloudExpiryEnabled':'true'},{'cloudExpiryScriptPath':'relative.py'},
                       {'cloudExpiryScriptSHA256':'bad'}):
            with self.subTest(change=change),self.assertRaises(ValueError):owner.cloud_expiry_source(self.profile|change)
        self.plan['deadlineEpoch']=self.now+4*3600+1
        with self.assertRaises(ValueError):self.job.create_body(self.state,'ssh-ed25519 OFFLINEPUBLIC')

    def test_heartbeat_accepts_exact_and_omits_unknown_environment_fields(self):
        proof=owner.validate_cloud_heartbeat(self.proof|{'credential':'must-not-propagate'},self.expected,self.now)
        self.assertEqual(proof,self.proof);self.assertNotIn('credential',proof)

    def test_heartbeat_rejects_stale_future_wrong_identity_nonce_and_deadline(self):
        changes=({'atEpoch':self.now-10.01},{'atEpoch':self.now+.01},{'checkedAtEpoch':self.now-11},
            {'podId':'foreignpod123'},{'name':'studio-foreign'},{'deadline':self.plan['deadlineEpoch']+1},
            {'scriptSHA256':'0'*64},{'nonceMatched':False},{'guardProcessAlive':False},{'status':'EXPIRING'},
            {'guardPID':True},{'atEpoch':float('nan')})
        for change in changes:
            with self.subTest(change=change),self.assertRaises(ValueError):
                owner.validate_cloud_heartbeat(self.proof|change,self.expected,self.now)

    def remote_fixture(self,change=None):
        """Execute real probe code against virtual /proc files; no OS process/network."""
        remote=self.root/'remote';remote.mkdir(exist_ok=True)
        mapping={}
        def put(name,value):
            target=remote/str(len(mapping));target.write_bytes(value if isinstance(value,bytes) else value.encode())
            mapping[name]=target
        put('/tmp/questiontemplate-cloud-guard.pid','123')
        put('/tmp/questiontemplate-cloud-guard.py',self.script.read_bytes())
        launch_tokens=shlex.split(owner.cloud_expiry_command(self.script.read_bytes()))
        start=launch_tokens.index('python3');launch_argv=launch_tokens[start:start+3]
        self.assertEqual(launch_argv,['python3','-u','/tmp/questiontemplate-cloud-guard.py'])
        put('/proc/123/cmdline',b'\0'.join(v.encode() for v in launch_argv)+b'\0')
        config={k:self.expected[k] for k in ('name','nonce','created','deadline')}
        env={'QT_CLOUD_CONFIG':json.dumps(config),'QT_CLOUD_NONCE':self.expected['nonce'],
            'RUNPOD_POD_ID':self.expected['podId'],'QT_CLOUD_API_KEY':'OFFLINE-ONLY-SECRET'}
        heartbeat={k:self.proof[k] for k in ('status','podId','deadline','atEpoch','cloudResident')}
        heartbeat.update(name=self.expected['name'],nonce=self.expected['nonce'],pid=123)
        if change:change(mapping,env,heartbeat)
        put('/proc/123/environ',b'\0'.join(k.encode()+b'='+v.encode() for k,v in env.items())+b'\0')
        put('/tmp/questiontemplate-cloud-guard.json',json.dumps(heartbeat))
        command=owner.cloud_heartbeat_probe(self.expected);code=shlex.split(command)[2]
        output=io.StringIO()
        with (patch('pathlib.Path',side_effect=lambda p:mapping[p]),patch('os.kill') as kill,
             patch('time.time',return_value=self.now),contextlib.redirect_stdout(output)):
            exec(compile(code,'remote-probe','exec'),{})
        return json.loads(output.getvalue()),kill

    def test_remote_probe_checks_process_env_source_and_outputs_no_key_nonce(self):
        proof,kill=self.remote_fixture()
        self.assertEqual(owner.validate_cloud_heartbeat(proof,self.expected,self.now),proof)
        self.assertEqual(kill.call_count,2)
        self.assertNotIn('OFFLINE-ONLY-SECRET',json.dumps(proof));self.assertNotIn(self.expected['nonce'],json.dumps(proof))

    def test_remote_probe_rejects_nonce_wrong_process_hash_or_stale_beat(self):
        def wrong_nonce(m,e,h):e['QT_CLOUD_NONCE']='foreign'
        def stale(m,e,h):h['atEpoch']=self.now-11
        def wrong_process(m,e,h):m['/proc/123/cmdline'].write_bytes(b'python3\0/tmp/foreign.py\0')
        def wrong_source(m,e,h):m['/tmp/questiontemplate-cloud-guard.py'].write_text('wrong source')
        for callback in (wrong_nonce,stale,wrong_process,wrong_source):
            with self.subTest(callback=callback.__name__),self.assertRaises(AssertionError):self.remote_fixture(callback)

    def test_actual_guard_launch_argv_accepts_reviewed_absolute_python3_paths(self):
        for executable in (b'/usr/bin/python3',b'/usr/local/bin/python3'):
            def absolute(m,e,h):m['/proc/123/cmdline'].write_bytes(executable+b'\0-u\0/tmp/questiontemplate-cloud-guard.py\0')
            with self.subTest(executable=executable):
                proof,_=self.remote_fixture(absolute)
                self.assertEqual(owner.validate_cloud_heartbeat(proof,self.expected,self.now),proof)

    def test_guard_argv_rejects_extra_flags_missing_u_or_unreviewed_interpreter(self):
        for command in (b'python3\0/tmp/questiontemplate-cloud-guard.py\0',
            b'python3\0-u\0-c\0/tmp/questiontemplate-cloud-guard.py\0',
            b'/tmp/python3\0-u\0/tmp/questiontemplate-cloud-guard.py\0',
            b'python3\0-u\0/tmp/foreign.py\0'):
            def wrong(m,e,h):m['/proc/123/cmdline'].write_bytes(command)
            with self.subTest(command=command),self.assertRaises(AssertionError):self.remote_fixture(wrong)

    def test_handoff_requires_ssh_success_and_fresh_proof_then_saves_sanitized_receipt(self):
        self.job.guard=Mock();self.job.ssh_args=Mock(return_value=['EXACT-OWNED-SSH'])
        result=SimpleNamespace(returncode=0,stdout=json.dumps(self.proof),stderr='NOT RECORDED')
        with patch.object(owner.subprocess,'run',return_value=result) as run:
            self.assertEqual(self.job.verify_cloud_handoff(self.state,self.pod),self.proof)
        args=run.call_args.args[0];self.assertEqual(args[0],'EXACT-OWNED-SSH');self.assertNotIn('OFFLINE-ONLY-SECRET',str(args))
        self.assertEqual(owner.read(self.job.folder/'cloud-expiry-proof.json'),self.proof)
        self.job.guard.assert_called_once_with(self.state)

    def test_failed_remote_check_has_no_receipt_and_legacy_has_no_ssh(self):
        self.job.guard=Mock();self.job.ssh_args=Mock(return_value=['EXACT-OWNED-SSH'])
        bad=SimpleNamespace(returncode=1,stdout='private invalid output',stderr='private error')
        with patch.object(owner.subprocess,'run',return_value=bad):
            with self.assertRaisesRegex(ValueError,'Cloud expiry heartbeat probe failed'):
                self.job.verify_cloud_handoff(self.state,self.pod)
        self.assertFalse((self.job.folder/'cloud-expiry-proof.json').exists())
        self.job.verify_profile.return_value={}
        with patch.object(owner.subprocess,'run',side_effect=AssertionError('No SSH in disabled branch')):
            self.assertIsNone(self.job.verify_cloud_handoff(self.state,{}))

    def test_provider_config_wrong_nonce_identity_or_volume_never_reaches_ssh(self):
        changes=({'id':'foreign1234'},{'name':'studio-foreign'},
            {'env':self.pod['env']|{'QT_CLOUD_NONCE':'foreign'}},
            {'env':self.pod['env']|{'QT_CLOUD_CONFIG':json.dumps({k:self.expected[k] for k in ('name','nonce','created','deadline')}|{'volume':{}})}})
        for change in changes:
            with self.subTest(change=change),patch.object(owner.subprocess,'run',side_effect=AssertionError('SSH forbidden')):
                with self.assertRaises(ValueError):self.job.verify_cloud_handoff(self.state,self.pod|change)

    def test_capacity_probe_is_false_by_default_and_null_stock_creates_no_intent(self):
        f=Fixture(self.root/'probe-disabled')
        with patch.object(cache,'workstation_owner_root',return_value=f.state.resolve()):job=f.prepare()
        release=owner.read(job.folder/'native-admission.json')
        self.assertIs(release['capacityProbeAllowed'],False)
        counter=self.root/'count-disabled';fake_live(job,counter);original=job.api
        job.api=lambda path,*a,**kw:({'id':owner.GPU,'secure':True,'price':{'secure':1.19},'dataCenters':None}
            if path.startswith('/catalog/gpus/') else original(path,*a,**kw))
        with self.assertRaisesRegex(ValueError,'No cache-region capacity'):job.launch(job.folder/'native-admission.json')
        self.assertFalse((job.folder/'create-intent.json').exists());self.assertFalse(counter.exists())

    def test_capacity_probe_explicit_private_profile_permits_only_one_mocked_post(self):
        f=Fixture(self.root/'probe-enabled');profile_path=Path(f.config['runpodImageCacheProfile'])
        profile=owner.read(profile_path);profile['allowOneCapacityProbe']=True;owner.write(profile_path,profile)
        with patch.object(cache,'workstation_owner_root',return_value=f.state.resolve()):job=f.prepare()
        release=owner.read(job.folder/'native-admission.json');self.assertIs(release['capacityProbeAllowed'],True)
        counter=self.root/'count-enabled';fake_live(job,counter);original=job.api
        job.api=lambda path,*a,**kw:({'id':owner.GPU,'secure':True,'price':{'secure':1.19},'dataCenters':None}
            if path.startswith('/catalog/gpus/') else original(path,*a,**kw))
        job.launch(job.folder/'native-admission.json')
        self.assertEqual(counter.read_text(),'ONE_MOCK_POST\n')
        with self.assertRaisesRegex(ValueError,'no replay'):job.launch(job.folder/'native-admission.json')
        self.assertEqual(counter.read_text(),'ONE_MOCK_POST\n')

    def test_release_cannot_enable_probe_without_profile_or_after_profile_changed(self):
        f=Fixture(self.root/'probe-release')
        with patch.object(cache,'workstation_owner_root',return_value=f.state.resolve()):job=f.prepare()
        release=owner.read(job.folder/'native-admission.json');release['capacityProbeAllowed']=True
        with self.assertRaisesRegex(ValueError,'bound private-profile opt-in'):job.verify_release(release)
        profile_path=Path(f.config['runpodImageCacheProfile']);p=owner.read(profile_path)
        p['allowOneCapacityProbe']=True;owner.write(profile_path,p)
        with self.assertRaisesRegex(ValueError,'Job cache profile changed'):job.verify_release(release)


if __name__=='__main__':unittest.main()
