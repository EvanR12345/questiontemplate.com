"""Faster transport/assembly cannot learn from incompatible older timings."""
import tempfile,unittest
from pathlib import Path
from studio_data import new_project
from studio_performance import PerformanceStore,profile_for
from studio_planner_profile import planner_profile

class SpeedProfileTest(unittest.TestCase):
    def project(self):
        p=new_project();p['settings']['image']['provider']='comfyui';return p

    def test_image_transport_isolated_without_changing_qc_voice_or_director(self):
        p=self.project();legacy={'comfyPersistentHTTP':False}
        for stage in ('Image pipeline','Image + quality checks','Image generation','Character reference','Intro images','Automatic image repair'):
            with self.subTest(stage=stage):
                current=profile_for(stage,p,{});old=profile_for(stage,p,legacy)
                self.assertEqual(current['imageTransport'],'private-http-keepalive-v1')
                self.assertNotIn('imageTransport',old)
                self.assertEqual({k:v for k,v in current.items() if k!='imageTransport'},old)
        for stage in ('Visual QC','Narration','Render chapter','AI directing'):
            self.assertEqual(profile_for(stage,p,{}),profile_for(stage,p,legacy))
        with tempfile.TemporaryDirectory() as root:
            store=PerformanceStore(Path(root)/'timings.sqlite3')
            try:
                store.record('old','one',profile_for('Image generation',p,legacy),{'kind':'images','value':1},10,{'status':'COMPLETE'})
                self.assertIsNone(store.estimate(profile_for('Image generation',p,{}),{'kind':'images','value':1})['seconds'])
            finally:store.close()

    def test_direct_full_mux_has_separate_timing_policy(self):
        p=self.project();current=profile_for('Assemble full video',p,{}, {'fullAssemblyPolicy':'direct-concat-original-audio-v1'})
        self.assertEqual(current['fullAssemblyPolicy'],'direct-concat-original-audio-v1')
        self.assertNotIn('fullAssemblyPolicy',profile_for('Render chapter',p,{}))
        self.assertNotIn('fullAssemblyPolicy',profile_for('Narration',p,{}))
        self.assertEqual(profile_for('Assemble full video',p,{})['fullAssemblyPolicy'],'format-guarded-original-audio-v1')
        self.assertNotEqual(current,profile_for('Assemble full video',p,{}, {'fullAssemblyPolicy':'legacy-two-pass-v1'}))
        with tempfile.TemporaryDirectory() as root:
            store=PerformanceStore(Path(root)/'timings.sqlite3')
            try:
                old=dict(current);old.pop('fullAssemblyPolicy')
                store.record('old','one',old,{'kind':'videoSeconds','value':120},2,{'status':'COMPLETE'})
                self.assertIsNone(store.estimate(current,{'kind':'videoSeconds','value':120})['seconds'])
            finally:store.close()

    def test_saved_profile_reports_transport_and_keeps_review_compatibility(self):
        p=self.project();before=planner_profile(p,config={'comfyPersistentHTTP':False});after=planner_profile(p)
        self.assertEqual(before['imageExecution']['transport'],'urllib-close-v1')
        self.assertEqual(after['imageExecution']['transport'],'private-http-keepalive-v1')
        self.assertEqual(before['qcCompatibilityHash'],after['qcCompatibilityHash'])
        self.assertEqual(before['settings'],after['settings'])

if __name__=='__main__':unittest.main()
