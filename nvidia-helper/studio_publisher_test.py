import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from studio_publisher import CloudPublisher

class PublisherTests(unittest.TestCase):
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
