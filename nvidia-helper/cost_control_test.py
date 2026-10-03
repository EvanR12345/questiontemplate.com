import tempfile
import unittest
from pathlib import Path
from cost_control import SpendLedger, luna_cost

class CostGuardTest(unittest.TestCase):
    def test_usage_cache_and_restart_budget(self):
        self.assertAlmostEqual(luna_cost({'input_tokens':1000,'output_tokens':200,'input_tokens_details':{'cached_tokens':500}}), .000155)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'ledger.json'
            ledger = SpendLedger(path, .002)
            body = {'model':'gpt-6-luna','max_output_tokens':1000,'input':[{'role':'user','content':'hello'}]}
            ledger.reserve(body)
            with self.assertRaisesRegex(RuntimeError, 'unresolved'):
                SpendLedger(path,.002).reserve(body)
            ledger.settle({'input_tokens':1000,'output_tokens':2000})
            with self.assertRaisesRegex(RuntimeError, 'exceed'):
                ledger.reserve(body | {'max_output_tokens':4000})
            self.assertEqual(len(ledger.load()['receipts']),1)
    def test_failure_reserves_cost_without_false_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger = SpendLedger(Path(folder)/'cost.json',.01)
            bound = ledger.reserve({'model':'gpt-6-luna','max_output_tokens':100,'input':[]})
            ledger.settle()
            self.assertEqual(ledger.load()['spentUSD'], bound)

if __name__ == '__main__':
    unittest.main()
