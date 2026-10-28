import copy
import math
import sys
import unittest
from unittest.mock import patch
import studio_snapshot_budget as budget


class SnapshotBudgetTest(unittest.TestCase):
    def test_small_json_and_exact_available_threshold(self):
        project={'id':'synthetic','settings':{'enabled':True},'chapters':[{'source':'unchanged','number':1,'nullable':None,'value':.5}]}
        before=copy.deepcopy(project);details=budget.snapshot_budget_details(project,2**30)
        self.assertTrue(details['allowed']);self.assertEqual(project,before)
        self.assertEqual(details['extraEstimatedBytes'],details['singleSnapshotEstimatedBytes']*3)
        self.assertTrue(budget.adaptive_snapshot_room(project,details['requiredAvailableBytes']))
        self.assertFalse(budget.adaptive_snapshot_room(project,details['requiredAvailableBytes']-1))

    def test_unknown_bool_nonfinite_and_negative_memory_fail_closed(self):
        for memory in (None,True,False,'10000000000',float('nan'),float('inf'),-1,0,10**10000):
            self.assertFalse(budget.adaptive_snapshot_room({},memory))

    def test_extra_three_cannot_exceed_128mib(self):
        project={'oversized':'x'*(budget.MAX_EXTRA_BYTES//3)}
        details=budget.snapshot_budget_details(project,64*2**30)
        self.assertFalse(details['allowed']);self.assertEqual(details['reason'],'snapshot_size_limit')

    def test_aliases_count_once_and_cycles_fail_closed(self):
        shared={'array':[1,2,'unchanged']};project={'first':shared,'second':shared}
        details=budget.snapshot_budget_details(project,2**30)
        expected=sum(sys.getsizeof(v) for v in (project,'first','second',shared,'array',shared['array'],1,2,'unchanged'))
        self.assertEqual(details['singleSnapshotEstimatedBytes'],expected)
        project['cycle']=project
        self.assertFalse(budget.adaptive_snapshot_room(project,2**30))

    def test_unsupported_objects_keys_and_project_nan_fail_closed(self):
        for project in ({'value':object()},{'bytes':b'not JSON'},{1:'non-string key'},{'value':math.nan},[]):
            self.assertFalse(budget.adaptive_snapshot_room(project,2**30))

    def test_large_alias_list_traversal_is_bounded(self):
        with patch.object(budget,'MAX_VISITS',100):
            details=budget.snapshot_budget_details({'aliases':[None]*1000},2**30)
        self.assertFalse(details['allowed']);self.assertEqual(details['reason'],'traversal_visit_limit')
        self.assertEqual(details['visits'],101)

    def test_depth_and_unique_object_traversals_are_bounded(self):
        project={};cursor=project
        for _ in range(20):cursor['child']={};cursor=cursor['child']
        with patch.object(budget,'MAX_DEPTH',8):
            self.assertEqual(budget.snapshot_budget_details(project,2**30)['reason'],'traversal_depth_limit')
        with patch.object(budget,'MAX_UNIQUE_OBJECTS',5):
            self.assertEqual(budget.snapshot_budget_details({'values':[str(i) for i in range(20)]},2**30)['reason'],'traversal_object_limit')

    def test_no_copy_serialize_or_memory_provider_call(self):
        with patch('copy.deepcopy',side_effect=AssertionError('copy forbidden')),patch('json.dumps',side_effect=AssertionError('serialize forbidden')):
            self.assertTrue(budget.adaptive_snapshot_room({'nested':['immutable',4]},2**30))
        self.assertFalse(budget.adaptive_snapshot_room({'small':True},budget.SPARE_BYTES))


if __name__=='__main__':unittest.main()
