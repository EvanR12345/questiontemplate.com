"""Offline request-loss tests. No real backend, GPU or image billing."""
import io, itertools, unittest, urllib.error
from unittest.mock import patch
from PIL import Image
from image_provider import ComfyImageProvider, RemoteGenerationUncertain

class RecoveryTest(unittest.TestCase):
    def test_gateway_request_timeout_is_uncertain_rather_than_a_free_retry(self):
        import urllib.error
        p=self.provider()
        error=urllib.error.HTTPError('http://127.0.0.1:8188/prompt',408,'gateway timeout',{},None)
        with patch('image_provider.request_json',side_effect=error):
            with self.assertRaises(RemoteGenerationUncertain):
                p.generateImage(self.request(),lambda *args:None)

    def provider(self):
        p=ComfyImageProvider({})
        p.validateSettings=lambda s:s
        p.healthCheck=lambda:{'installed':True}
        p.template=lambda:{'model':'flux','name':'test','prompt':{'1':{'class_type':'Test','inputs':{}}},'bindings':{}}
        return p
    def request(self):
        return {'prompt':'A landscape','settings':{'model':'flux','seed':17,'steps':4}}
    def history(self):
        return {'accepted':{'outputs':{'1':{'images':[{'filename':'saved.png'}]}},'status':{'status_str':'success'}}}
    def png(self):
        data=io.BytesIO();Image.new('RGB',(8,8),(120,160,200)).save(data,'PNG')
        return io.BytesIO(data.getvalue())

    def test_lost_submission_ack_is_not_assumed_rejected(self):
        for error in (OSError('lost reply'),{'wrong':'reply'},
                      urllib.error.HTTPError('http://test',500,'server failure',{},None)):
            with self.subTest(error=type(error).__name__):
                with patch('image_provider.request_json',side_effect=error if isinstance(error,Exception) else [error]) as network:
                    with self.assertRaises(RemoteGenerationUncertain) as result:
                        self.provider().generateImage(self.request(),lambda *_:None)
                    self.assertIsNone(result.exception.prompt_id)
                    self.assertEqual(network.call_count,1)

    def test_lost_history_preserves_accepted_id_and_does_not_kill_another_job(self):
        with patch('image_provider.request_json',side_effect=[{'prompt_id':'accepted'},OSError('temporary disconnect')]) as network:
            with self.assertRaises(RemoteGenerationUncertain) as result:
                self.provider().generateImage(self.request(),lambda *_:None)
            self.assertEqual(result.exception.prompt_id,'accepted')
            self.assertEqual(network.call_count,2)
            self.assertNotIn('/interrupt',str(network.call_args_list))

    def test_timeout_removes_only_own_job_and_does_not_interrupt_other_owner(self):
        p=self.provider();p.config['comfyGenerationTimeoutSeconds']=30
        calls=[]
        def network(url,body=None,**kwargs):
            calls.append((url,body))
            if url.endswith('/prompt'):return {'prompt_id':'accepted'}
            if '/history/' in url:return {}
            if url.endswith('/queue'):return {'queue_running':[[0,'another-job']]}
            raise AssertionError('Do not interrupt the other running job')
        with patch('image_provider.request_json',network),patch('image_provider.time.sleep'), \
             patch('image_provider.time.monotonic',side_effect=itertools.count()):
            with self.assertRaises(RemoteGenerationUncertain) as result:
                p.generateImage(self.request(),lambda *_:None)
        self.assertTrue(result.exception.timed_out)
        self.assertEqual([body for url,body in calls if body], [{'prompt':{'1':{'class_type':'Test','inputs':{}}},'client_id':calls[0][1]['client_id']}, {'delete':['accepted']}])
        self.assertFalse(any(url.endswith('/interrupt') for url,_ in calls))

    def test_cancel_interrupts_only_the_owned_running_prompt(self):
        def network(url,body=None,**kwargs):
            if url.endswith('/prompt'):return {'prompt_id':'accepted'}
            if url.endswith('/queue'):return {'queue_running':[[0,'accepted']]}
            if url.endswith('/interrupt'):return {}
            raise AssertionError(url)
        def cancel(*args):
            if args[-1]=='ComfyUI generation':raise InterruptedError('cancelled')
        with patch('image_provider.request_json',side_effect=network) as calls:
            with self.assertRaises(InterruptedError):
                self.provider().generateImage(self.request(),cancel)
            self.assertTrue(any(c.args[0].endswith('/interrupt') for c in calls.call_args_list))

    def test_download_failure_never_repeats_generation(self):
        with patch('image_provider.request_json',side_effect=[{'prompt_id':'accepted'},self.history()]) as calls, \
             patch('image_provider.urllib.request.urlopen',side_effect=OSError('image transfer failed')):
            with self.assertRaises(RemoteGenerationUncertain) as result:
                self.provider().generateImage(self.request(),lambda *_:None)
            self.assertEqual(result.exception.prompt_id,'accepted')
            self.assertEqual(calls.call_count,2)

    def test_success_records_client_stages_and_remote_identity_without_changing_pixels(self):
        with patch('image_provider.request_json',side_effect=[{'prompt_id':'accepted'},self.history()]), \
             patch('image_provider.urllib.request.urlopen',return_value=self.png()):
            result=self.provider().generateImage(self.request(),lambda *_:None)
        self.assertEqual(result['remotePromptId'],'accepted')
        self.assertEqual(result['pil'].getpixel((0,0)),(120,160,200))
        self.assertEqual(result['providerTimings']['historyRequests'],1)
        self.assertEqual(result['seed'],17)
        self.assertGreaterEqual(result['providerTimings']['totalClientSeconds'],0)

if __name__=='__main__':unittest.main()
