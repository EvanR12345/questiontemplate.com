import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from studio_publisher import CloudPublisher,save_upload_snapshot
from studio_data import ProjectStore,new_project

class PublisherTests(unittest.TestCase):
    def test_cloud_render_control_keeps_project_and_nonce_without_mutating_assets(self):
        provider=CloudPublisher('unused');pid='pr-0123456789abcdef';nonce='a'*32
        with patch.object(provider,'request',return_value={'project':pid,'id':nonce,'status':'QUEUED'}) as transport:
            provider.render('start',pid,{'id':nonce,'revision':4,'destination':'patreon','ignored':'never transmit'})
            transport.assert_called_once_with('/renders/start',{'project':pid,'id':nonce,'revision':4,'destination':'patreon'})
        with patch.object(provider,'request',return_value={'project':'pr-1111111111111111'}) as transport:
            with self.assertRaisesRegex(ValueError,'does not match'):provider.render('status',pid)
        with patch.object(provider,'request') as transport:
            with self.assertRaises(ValueError):provider.render('start',pid,{'id':nonce,'revision':True,'destination':'patreon'})
            with self.assertRaises(ValueError):provider.render('cancel',pid)
            transport.assert_not_called()
    def test_google_upload_requires_google_publisher_and_never_uses_r2_fallback(self):
        provider=CloudPublisher('unused')
        google=SimpleNamespace(provider='Google Cloud Storage')
        with patch.object(provider,'status',return_value={'storageProvider':'r2'}):
            with self.assertRaisesRegex(ValueError,'does not match'):provider.validate_storage(google)
        with patch.object(provider,'status',return_value={'storageProvider':'gcs'}):
            provider.validate_storage(google)
            with self.assertRaisesRegex(ValueError,'does not match'):
                provider.validate_storage(SimpleNamespace(provider='Cloudflare R2'))
    def test_chunk_progress_does_not_archive_whole_project_but_completion_is_saved(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProjectStore(tmp);project=store.save(new_project('Upload story'));pid=project['id']
            job={'id':'upload-1','status':'UPLOADING','uploaded':0,'total':100}
            save_upload_snapshot(store,pid,job,initial=True)
            initial=store.load(pid)['revision']
            for progress in range(1,100):save_upload_snapshot(store,pid,dict(job,uploaded=progress))
            self.assertEqual(store.load(pid)['revision'],initial)
            completed=dict(job,status='COMPLETE',uploaded=100,videoId='video-1')
            save_upload_snapshot(store,pid,completed)
            self.assertEqual(store.load(pid)['publishing'][0]['videoId'],'video-1')
            self.assertEqual(store.load(pid)['revision'],initial+1)
            save_upload_snapshot(store,pid,completed)
            self.assertEqual(store.load(pid)['revision'],initial+1)
    def test_cancellation_snapshot_keeps_original_video_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=ProjectStore(tmp);project=new_project('Keep source');project['render']={'path':'story.mp4'}
            project=store.save(project);pid=project['id']
            job={'id':'upload-2','status':'UPLOADING','uploaded':0,'total':100}
            save_upload_snapshot(store,pid,job,initial=True)
            save_upload_snapshot(store,pid,dict(job,status='CANCELLED',uploaded=25))
            self.assertEqual(store.load(pid)['render']['path'],'story.mp4')
            self.assertEqual(store.load(pid)['publishing'][0]['status'],'CANCELLED')
    def test_control_requests_identify_studio_and_transfer_only_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private.json'
            path.write_text(json.dumps({'url':'https://studio-test.example.workers.dev','token':'x'*64}))
            def transport(request,timeout):
                self.assertEqual(request.get_header('User-agent'),'QuestionTemplateStudio/1.0')
                self.assertEqual(request.get_header('Accept'),'application/json')
                self.assertEqual(json.loads(request.data),{'id':'test-job'})
                self.assertLess(len(request.data),100)
                return io.BytesIO(b'{"status":"UPLOADING"}')
            with patch('studio_publisher.urlopen',transport):
                self.assertEqual(CloudPublisher(path).call('next',{'id':'test-job'})['status'],'UPLOADING')
    def test_missing_setup_stays_disconnected_and_embedded_url_credentials_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'private.json';provider=CloudPublisher(path)
            self.assertFalse(provider.status()['configured'])
            path.write_text(json.dumps({'url':'https://user:password@studio.example.workers.dev','token':'x'*64}))
            with self.assertRaisesRegex(ValueError,'verified HTTPS'):provider.config()

if __name__=='__main__':unittest.main()
