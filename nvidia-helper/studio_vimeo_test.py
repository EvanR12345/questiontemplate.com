import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError,HTTPError
from studio_data import ProjectStore,new_project
from studio_vimeo import VimeoPublisher,NoVimeoRedirect


class VimeoTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=ProjectStore(self.root);self.project=self.store.save(new_project('Cloud story'))
        self.secret=self.root/'private.json';self.secret.write_text(json.dumps({'vimeoApiToken':'private-token-for-test-only'}))
        self.record={'key':'private/cloud/video','bytes':1234,'sha256':'a'*64}
        self.archive=SimpleNamespace(provider='Google Cloud Storage',enabled=True,
            asset_record=lambda *_:self.record,objects=SimpleNamespace(bucket=SimpleNamespace(
                blob=lambda _:SimpleNamespace(generate_signed_url=lambda **_: 'https://storage.googleapis.com/private?signature=private-source'))))
        self.calls=[]
        def transport(request,timeout):
            self.calls.append(request)
            return io.BytesIO(json.dumps({'uri':'/videos/1234','link':'https://vimeo.com/1234/abcdef'}).encode())
        self.publisher=VimeoPublisher(self.store,self.archive,{'vimeoKeyFile':str(self.secret)},opener=transport)
        self.options={'id':'a'*32,'path':'story.mp4','title':'Cloud story','privacy':'unlisted'}
    def tearDown(self):self.temp.cleanup()

    def test_direct_cloud_pull_is_private_by_default_and_does_not_download_video(self):
        options={k:v for k,v in self.options.items() if k!='privacy'}
        result=self.publisher.start(self.project['id'],options)
        body=json.loads(self.calls[0].data)
        self.assertEqual(body['upload']['approach'],'pull')
        self.assertEqual(body['privacy']['view'],'nobody')
        self.assertEqual(result['status'],'PROCESSING')
        self.assertFalse(self.store.asset_local(self.project['id'],'story.mp4').exists())
        for raw in (json.dumps(result),self.publisher.path(self.project['id'],options['id']).read_text()):
            self.assertNotIn('private-source',raw);self.assertNotIn('private-token-for-test-only',raw)

    def test_same_identity_never_submits_twice_and_different_inputs_are_rejected(self):
        first=self.publisher.start(self.project['id'],self.options)
        self.assertEqual(first,self.publisher.start(self.project['id'],self.options))
        self.assertEqual(len(self.calls),1)
        with self.assertRaisesRegex(ValueError,'different video'):self.publisher.start(self.project['id'],self.options|{'title':'Other title'})

    def test_lost_reply_is_durable_and_cannot_create_a_duplicate_on_retry(self):
        def lost(*args,**kwargs):self.calls.append(1);raise URLError('lost connection')
        self.publisher.opener=lost
        with self.assertRaisesRegex(ValueError,'interrupted'):self.publisher.start(self.project['id'],self.options)
        result=self.publisher.start(self.project['id'],self.options)
        self.assertEqual(result['status'],'SUBMISSION_UNKNOWN');self.assertEqual(len(self.calls),1)
        self.assertEqual(self.publisher.jobs(self.project['id'])[0]['status'],'SUBMISSION_UNKNOWN')

    def test_confirmed_rejection_and_unsigned_source_are_not_uncertain_uploads(self):
        def rejected(request,**kwargs):raise HTTPError(request.full_url,403,'Forbidden',{},io.BytesIO())
        self.publisher.opener=rejected
        with self.assertRaisesRegex(ValueError,'403'):self.publisher.start(self.project['id'],self.options)
        self.assertEqual(self.publisher.status(self.project['id'],self.options['id'])['status'],'REJECTED')
        def unsigned(**kwargs):raise ValueError('Cannot sign source')
        self.archive.objects.bucket.blob=lambda _:SimpleNamespace(generate_signed_url=unsigned)
        options=self.options|{'id':'b'*32}
        with self.assertRaisesRegex(ValueError,'Cannot sign'):self.publisher.start(self.project['id'],options)
        self.assertEqual(self.publisher.status(self.project['id'],options['id'])['status'],'FAILED')

    def test_processing_status_retains_the_unlisted_viewing_hash(self):
        self.publisher.start(self.project['id'],self.options)
        self.publisher.opener=lambda *args,**kw:io.BytesIO(json.dumps({'uri':'/videos/1234',
            'link':'https://vimeo.com/1234/abcdef','status':'available','transcode':{'status':'complete'},
            'upload':{'status':'complete'},'privacy':{'view':'unlisted'}}).encode())
        result=self.publisher.status(self.project['id'],self.options['id'])
        self.assertEqual(result['status'],'COMPLETE')
        self.assertEqual(result['videoURL'],'https://vimeo.com/1234/abcdef')
        self.assertEqual(result['actualPrivacy'],'unlisted')

    def test_foreign_host_redirect_path_and_visibility_are_rejected(self):
        with self.assertRaises(ValueError):NoVimeoRedirect().redirect_request(None,None,None,None,None,None)
        with self.assertRaises(ValueError):self.publisher.request('/videos/1234?next=https://attacker.test')
        with self.assertRaises(ValueError):self.publisher.start(self.project['id'],self.options|{'path':'../secret.mp4'})
        with self.assertRaises(ValueError):self.publisher.start(self.project['id'],self.options|{'privacy':'public'})
        with self.assertRaises(ValueError):self.publisher.video_link({'link':'https://attacker.test/1234'},'/videos/1234')

    def test_unconfigured_health_never_claims_an_account_connection(self):
        self.publisher.config={}
        self.assertFalse(self.publisher.health()['configured'])
        with self.assertRaises(ValueError):self.publisher.start(self.project['id'],self.options)

    def test_cloud_receipt_recovers_on_another_helper_without_duplicate_upload(self):
        saved={};versions={}
        class Objects:
            def read(_,key):return (json.dumps(saved[key]).encode(),str(versions[key])) if key in saved else (None,None)
            def put_json(_,key,value,create=False,etag=None):
                if create and key in saved:raise ValueError('Already exists')
                if etag and etag!=str(versions.get(key)):raise ValueError('Revision changed')
                saved[key]=json.loads(json.dumps(value));versions[key]=versions.get(key,0)+1
            def keys(_,prefix):return [key for key in saved if key.startswith(prefix)]
        objects=Objects();objects.bucket=self.archive.objects.bucket;self.archive.objects=objects
        first=self.publisher.start(self.project['id'],self.options)
        self.publisher.path(self.project['id'],self.options['id']).unlink()
        recovered=VimeoPublisher(self.store,self.archive,self.publisher.config,
            opener=lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Duplicate Vimeo POST')))
        self.assertEqual(recovered.start(self.project['id'],self.options),first)
        self.assertEqual(recovered.jobs(self.project['id'])[0]['id'],first['id'])

if __name__=='__main__':unittest.main()
