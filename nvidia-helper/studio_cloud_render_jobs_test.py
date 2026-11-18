import copy,json,sqlite3,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from studio_data import new_project
from studio_cloud_render_jobs import CloudRenderJobs,render_options

class CloudJobTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.project=new_project();self.project['revision']=3;self.project['chapters'][0].update(sourceText='A saved story.',audio={'duration':82.05,'path':'voice.wav'})
        self.database=sqlite3.connect(':memory:');self.database.row_factory=sqlite3.Row
        self.database.execute('CREATE TABLE jobs(id TEXT PRIMARY KEY,project TEXT,chapter TEXT,shot TEXT,kind TEXT,payload TEXT,status TEXT,message TEXT,priority INTEGER,created REAL,started REAL,seconds REAL,attempt INTEGER,seed INTEGER)')
        self.publications=[];self.calls=[]
        self.archive=SimpleNamespace(enabled=True,provider='Google Cloud Storage',cv=threading.Condition(),active_project=None,pending=set(),
            manifests={},catalogue_lock=threading.RLock(),catalogue_at=42,
            manifest=lambda *_:(dict(project=copy.deepcopy(self.project),files={}), 'etag'),
            objects=SimpleNamespace(put_json=lambda *args,**kw:self.publications.append((args,kw))))
        self.studio=SimpleNamespace(store=SimpleNamespace(root=self.root,folder=lambda _:self.root,load=lambda _:copy.deepcopy(self.project)),
            config={},db=self.database,cv=threading.Condition(threading.RLock()),storage=self.archive,
            current=None,cancel=False,paused=False,yield_requested=False,gate=lambda *args,**kw:None)
        self.options={'id':'a'*32,'revision':3,'destination':'youtube','capUSD':.50}
        self.jobs=CloudRenderJobs(self.studio)
        self.jobs.capabilities=lambda :{'ready':True,'reason':'Ready','providers':['lium']}
        self.jobs.validate=lambda *_:None
    def tearDown(self):self.database.close();self.temp.cleanup()

    def test_lost_reply_retry_preserves_one_queued_request_and_cap(self):
        first=self.jobs.start(self.project['id'],self.options)
        second=self.jobs.start(self.project['id'],self.options)
        self.assertEqual(first,second);self.assertEqual(self.database.execute('SELECT count(*) FROM jobs').fetchone()[0],1)
        self.assertEqual(first['capUSD'],.50)
        with self.assertRaisesRegex(ValueError,'another request'):
            self.jobs.start(self.project['id'],self.options|{'capUSD':1})

    def test_another_active_queue_prevents_a_new_cloud_job(self):
        self.jobs.start(self.project['id'],self.options)
        with self.assertRaisesRegex(ValueError,'active queue'):
            self.jobs.start(self.project['id'],self.options|{'id':'b'*32})

    def test_queued_cancel_never_calls_renderer_and_is_discovered_after_reopen(self):
        self.jobs.start(self.project['id'],self.options)
        state=self.jobs.cancel(self.project['id'],'a'*32)
        self.assertEqual(state['status'],'CANCELLED')
        reopened=CloudRenderJobs(self.studio)
        self.assertEqual(reopened.status(self.project['id'])['id'],'a'*32)
        self.assertEqual(reopened.status(self.project['id'])['status'],'CANCELLED')
        with self.assertRaisesRegex(ValueError,'selected project'):self.jobs.cancel('pr-'+'b'*16,'a'*32)

    def fake_renderer(self,failure=False):
        test=self
        class Renderer:
            def __init__(self,*args,**kw):
                self.report={};self.gate=args[3]
                test.assertEqual(args[2]['gpuProviderOrder'],['lium'])
            def capabilities(self,_):return [],'etag'
            def run(self,project,identity,cap,**kw):
                test.calls.append((identity,cap));self.gate('Rendering')
                if failure:raise RuntimeError('A worker failed at https://private.example/token')
                return {'files':{'final':{'path':'cloud-renders/'+identity+'/full.mp4','key':'object','sha256':'b'*64,'bytes':44}},
                    'result':{'path':'cloud-renders/'+identity+'/full.mp4','duration':82.05},'rental':{'estimatedUSD':.01}}
        return Renderer

    def test_success_adds_cloud_files_and_preserves_selected_source_and_manual_edits(self):
        before=copy.deepcopy(self.project);self.jobs.renderer_factory=self.fake_renderer()
        self.jobs.run(self.project,self.options)
        self.assertEqual(self.project,before);self.assertEqual(self.calls,[('a'*32,.45)])
        self.assertEqual(len(self.publications),1)
        self.assertEqual(self.publications[0][1]['etag'],'etag')
        self.assertEqual(self.jobs.load('a'*32)['status'],'COMPLETE')
        with self.assertRaisesRegex(ValueError,'never replayed'):self.jobs.run(self.project,self.options)
        self.assertEqual(len(self.calls),1)

    def test_failed_attempt_is_retained_and_cannot_repeat_paid_allocation(self):
        self.jobs.renderer_factory=self.fake_renderer(failure=True)
        with self.assertRaises(RuntimeError):self.jobs.run(self.project,self.options)
        record=self.jobs.load('a'*32)
        self.assertNotIn('private.example',record['error']);self.assertEqual(record['status'],'FAILED')
        with self.assertRaisesRegex(ValueError,'never replayed'):self.jobs.run(self.project,self.options)
        self.assertEqual(len(self.calls),1);self.assertEqual(self.publications,[])

    def test_pause_stops_cloud_work_instead_of_waiting_on_rented_gpu(self):
        from studio_service import JobCancelled
        self.studio.paused=True;self.jobs.renderer_factory=self.fake_renderer()
        with self.assertRaises(JobCancelled):self.jobs.run(self.project,self.options)
        self.assertEqual(self.calls,[])

    def test_selected_outro_controls_assets_and_verified_duration_for_both_platforms(self):
        from studio_cloud_pod_render import render_assets
        from studio_engagement import audio_signature
        import wave
        project = self.project
        project['chapters'][0]['audio']['duration'] = 115
        project['chapters'][0]['scenes'] = [{'shots': [{'imagePath': 'frame.png'}]}]
        project['settings']['engagement'].update(exportDestination='youtube',
            outroEnabled=False, patreonOutroEnabled=True, outroDuration=15, outroAudioPath='outro.wav')
        project['settings']['engagement']['outroAudioSignature'] = audio_signature(project)
        with wave.open(str(self.root / 'outro.wav'), 'wb') as audio:
            audio.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x00\x00' * 8000 * 17)
        self.studio.store.asset_local = self.studio.store.asset = lambda _, path: self.root / path
        self.archive.asset_record = lambda pid, name: {'bytes': 1}
        self.jobs.capabilities = lambda: {'ready': True, 'maximumDurationSeconds': 120}
        original = copy.deepcopy(project)
        youtube = self.jobs.plan(project, 'youtube')
        self.assertEqual(youtube['durationSeconds'], 115)
        patreon = self.jobs.plan(project, 'patreon')
        self.assertEqual(patreon['durationSeconds'], 132.5)
        self.assertFalse(patreon['withinVerifiedDuration'])
        self.assertGreater(patreon['estimatedOutputBytes'], youtube['estimatedOutputBytes'])
        version = self.jobs.render_version(project, 'patreon', verify_outro=True)
        self.assertIn('outro.wav', render_assets(version))
        self.assertNotIn('outro.wav', render_assets(project))
        self.assertEqual(project, original)
        del self.jobs.validate
        with patch('studio_qc.decision', return_value={'blocking': False}):
            with self.assertRaisesRegex(ValueError, 'verified up to 120'):
                self.jobs.start(project['id'], self.options | {'destination': 'patreon'})
            self.assertEqual(self.database.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
            project['settings']['engagement'].update(outroEnabled=True, patreonOutroEnabled=False)
            state = self.jobs.start(project['id'], self.options | {'destination': 'patreon'})
        self.assertEqual(state['status'], 'QUEUED')
        projected = self.jobs.render_version(project, 'patreon', verify_outro=True)
        self.assertNotIn('outro.wav', render_assets(projected))
        self.assertEqual(self.jobs.plan(project, 'patreon')['durationSeconds'], 115)

    def test_nonfinite_cap_and_path_traversal_are_rejected_before_work(self):
        for value in (None,True,float('inf'),float('nan'),0,-1,101):
            with self.assertRaises(ValueError):render_options(self.options|{'capUSD':value})
        with self.assertRaises(ValueError):self.jobs.path('../secret')

    def test_background_progress_cannot_erase_a_saved_cancel_request(self):
        earlier={'id':'a'*32,'stage':'Rendering'};self.jobs.save(earlier)
        cancelled=dict(earlier,cancelRequested=True);self.jobs.save(cancelled)
        self.jobs.save({'id':'a'*32,'stage':'Uploading'})
        self.assertTrue(self.jobs.load('a'*32)['cancelRequested'])

    def test_readiness_requires_real_verification_on_selected_provider_and_google(self):
        with patch('studio_gpu_providers.provider_status',return_value={'providerOrder':['lium'],'providers':[
                {'provider':'lium','credentialSaved':True,'renderVerified':False}]}):
            state=CloudRenderJobs(self.studio).capabilities();self.assertFalse(state['ready']);self.assertEqual(state['paidRequestsSubmitted'],0)
        with patch('studio_gpu_providers.provider_status',return_value={'providerOrder':['lium'],'providers':[
                {'provider':'lium','credentialSaved':True,'renderVerified':True}]}):
            self.assertTrue(CloudRenderJobs(self.studio).capabilities()['ready'])
            self.archive.provider='Cloudflare R2';self.assertFalse(CloudRenderJobs(self.studio).capabilities()['ready'])

if __name__=='__main__':unittest.main()
