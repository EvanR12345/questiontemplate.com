"""No-network regression fixtures for pre-submission readiness GET timeouts."""
import unittest
import urllib.error
import tempfile
from pathlib import Path
from unittest.mock import Mock

from image_provider import ComfyImageProvider


class ReadinessRetryTests(unittest.TestCase):
    def provider(self):
        return ComfyImageProvider({'comfyEndpoint':'http://127.0.0.1:18188'})

    def test_memory_timeouts_retry_same_mandatory_probe_before_validation(self):
        provider=self.provider()
        provider._request_json=Mock(side_effect=[TimeoutError('one'),TimeoutError('two'),
            {'devices':[{'vram_total':32*2**30}]}])
        self.assertEqual(provider.available_vram_gb(),32)
        self.assertEqual(provider._request_json.call_count,3)
        for call in provider._request_json.call_args_list:
            self.assertEqual(call.args,('http://127.0.0.1:18188/system_stats',))
            self.assertEqual(call.kwargs,{'timeout':3})

    def test_wrapped_socket_timeout_retries_object_info_only(self):
        provider=self.provider();expected={'Node':{'input':{}}}
        provider._request_json=Mock(side_effect=[urllib.error.URLError(TimeoutError('socket')),expected])
        self.assertEqual(provider._readiness_json('/object_info',10),expected)
        self.assertEqual(provider._request_json.call_count,2)
        self.assertEqual(provider._request_json.call_args.kwargs,{'timeout':10})

    def test_actual_workflow_health_still_validates_catalog_after_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'workflow.json';path.write_text('{}')
            provider=self.provider();provider.config['comfyWorkflow']=str(path)
            provider.template=Mock(return_value={'model':'fixture','name':'fixture',
                'prompt':{'1':{'class_type':'Node','inputs':{}}}})
            provider._request_json=Mock(side_effect=[TimeoutError(),
                {'Node':{'input':{'required':{}}}}])
            self.assertTrue(provider.healthCheck(model='fixture',workflow='fixture')['installed'])
            self.assertEqual(provider._request_json.call_count,2)
            provider._health=(0,None);provider._node_catalog=(0,None)
            provider._request_json=Mock(return_value={})
            result=provider.healthCheck(model='fixture',workflow='fixture')
            self.assertFalse(result['installed']);self.assertEqual(result['missingNodes'],['Node'])
            self.assertEqual(provider._request_json.call_count,1)

    def test_exhaustion_is_three_attempts_and_does_not_cache_success(self):
        provider=self.provider();provider._request_json=Mock(side_effect=TimeoutError('still busy'))
        with self.assertRaisesRegex(RuntimeError,'Cannot verify'):
            provider.available_vram_gb()
        self.assertEqual(provider._request_json.call_count,3)
        self.assertEqual(provider._server_memory,(0,None))

    def test_invalid_memory_after_timeout_is_not_retried_or_accepted(self):
        for memory in (-1,float('nan')):
            provider=self.provider();provider._request_json=Mock(side_effect=[TimeoutError(),
                {'devices':[{'vram_total':memory}]}])
            with self.assertRaisesRegex(RuntimeError,'Invalid GPU memory'):
                provider.available_vram_gb()
            self.assertEqual(provider._request_json.call_count,2)
            self.assertEqual(provider._server_memory,(0,None))

    def test_auth_bad_response_and_non_timeout_network_errors_are_not_retried(self):
        errors=[urllib.error.HTTPError('http://127.0.0.1',403,TimeoutError(),{},None),
            urllib.error.URLError(ConnectionRefusedError('refused')),ValueError('malformed JSON'),
            urllib.error.URLError('timed out')]
        for error in errors:
            provider=self.provider();provider._request_json=Mock(side_effect=error)
            with self.assertRaises(type(error)):provider._readiness_json('/object_info',10)
            self.assertEqual(provider._request_json.call_count,1)

    def test_only_two_allowlisted_paths_can_use_retry_helper(self):
        provider=self.provider();provider._request_json=Mock()
        for path in ('/prompt','/history/id','/queue','/upload/image','/system_stats?other=1'):
            with self.assertRaises(ValueError):provider._readiness_json(path,3)
        provider._request_json.assert_not_called()

    def test_post_timeout_still_has_one_dispatch(self):
        provider=self.provider();provider._transport.json=Mock(side_effect=TimeoutError('lost acknowledgement'))
        with self.assertRaises(TimeoutError):
            provider._request_json(provider.url+'/prompt',{'prompt':{'exact':'unchanged'}})
        provider._transport.json.assert_called_once_with(provider.url+'/prompt',
            {'prompt':{'exact':'unchanged'}},180)


if __name__=='__main__':unittest.main()
