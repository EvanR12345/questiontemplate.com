import unittest
from unittest.mock import Mock
from studio_power import ProductionPowerRequest, CONTINUOUS, SYSTEM_REQUIRED


class ProductionPowerTest(unittest.TestCase):
    def test_request_prevents_idle_sleep_without_forcing_the_display_and_restores_prior_state(self):
        setter=Mock(side_effect=[CONTINUOUS|2,CONTINUOUS])
        with ProductionPowerRequest(setter=setter,platform='nt') as lease:
            self.assertTrue(lease.active)
            setter.assert_called_once_with(CONTINUOUS|SYSTEM_REQUIRED)
        self.assertFalse(lease.active)
        self.assertEqual(setter.call_args_list[-1].args,(CONTINUOUS|2,))

    def test_failure_cancel_and_exception_release_the_request(self):
        setter=Mock(return_value=CONTINUOUS)
        with self.assertRaises(RuntimeError):
            with ProductionPowerRequest(setter=setter,platform='nt'):
                raise RuntimeError('Production failed')
        self.assertEqual(setter.call_count,2)
        self.assertEqual(setter.call_args.args,(CONTINUOUS,))

    def test_unavailable_or_disabled_request_does_not_change_another_threads_state(self):
        for platform,enabled in [('posix',True),('nt',False)]:
            setter=Mock()
            with ProductionPowerRequest(enabled=enabled,setter=setter,platform=platform) as lease:
                self.assertFalse(lease.active)
            setter.assert_not_called()
        setter=Mock(return_value=0)
        with ProductionPowerRequest(setter=setter,platform='nt') as lease:self.assertFalse(lease.active)
        self.assertEqual(setter.call_count,1)

    def test_release_is_idempotent(self):
        setter=Mock(return_value=CONTINUOUS)
        lease=ProductionPowerRequest(setter=setter,platform='nt')
        lease.acquire();lease.acquire();lease.release();lease.release()
        self.assertEqual(setter.call_count,2)

    def test_cleanup_failure_does_not_mask_a_production_error(self):
        setter=Mock(side_effect=[CONTINUOUS,OSError('OS unavailable')])
        with self.assertRaisesRegex(RuntimeError,'original production error'):
            with ProductionPowerRequest(setter=setter,platform='nt'):
                raise RuntimeError('original production error')
