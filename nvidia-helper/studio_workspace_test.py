import copy
import tempfile
import unittest
from unittest.mock import patch
from studio_data import ProjectStore, new_project, digest
from studio_workspace import workspace_change, setup_snapshot
from studio_file_locations import located_files


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = ProjectStore(self.tmp.name)
        self.p = new_project('Test')
        c = self.p['chapters'][0]
        c.update(cleanNarrationText='He returned.', sourceText='He returned.')
        c['audio'] = {'path':'a.wav','textDigest':digest('He returned.'),'duration':10}
        c['audioHistory'] = [{'path':'old.wav','textDigest':digest('He returned.'),'duration':20}]
        c['scenes'] = [{'id':'scene1','chapterId':c['id'],'start':0,'end':10,'characters':[],
            'shots':[{'id':'shot1','sceneId':'scene1','chapterId':c['id'],'start':0,'end':10,
                      'imagePath':'current.png','history':[{'plan':{}},{'imagePath':'old.png','metadata':{'seed':9}}],
                      'qcAcceptance':{'pass':True},'qcDecision':{'blocking':False},'characters':[],
                      'status':'COMPLETE','camera':{},'generationSettings':{}}]}]
        self.p = self.store.save(self.p)
        for name in ['a.wav','old.wav','old.png','current.png']:
            self.store.asset_local(self.p['id'],name).write_bytes(b'test')
    def apply(self, operation, **kwargs):
        p = self.store.load(self.p['id'])
        return workspace_change(self.store,p['id'],{'revision':p['revision'],'operation':operation,
                    'chapter':p['chapters'][0]['id'],'shot':'shot1',**kwargs})
    def test_stale_revision_cannot_overwrite_selection(self):
        with self.assertRaisesRegex(ValueError,'changed'):
            workspace_change(self.store,self.p['id'],{'revision':-1,'operation':'restore-image','index':0})
        self.assertEqual(self.store.load(self.p['id'])['revision'],self.p['revision'])
    def test_choose_old_picture_keeps_both_files_and_requires_fresh_qc(self):
        p=self.apply('restore-image',index=0);s=p['chapters'][0]['scenes'][0]['shots'][0]
        self.assertEqual(s['imagePath'],'old.png');self.assertEqual(s['imageMetadata']['seed'],9)
        self.assertEqual(s['qc']['status'],'UNREVIEWED');self.assertNotIn('qcAcceptance',s)
        self.assertTrue(s['manual']['imagePath']);self.assertFalse(s['generationStale'])
        self.assertTrue(p['renderStale']);self.assertEqual(s['history'][-1]['imagePath'],'current.png')
        self.assertTrue(self.store.asset_local(p['id'],'current.png').exists())
    def test_manual_upload_attaches_only_available_project_image_and_retains_previous(self):
        p=self.apply('attach-image',path='old.png');s=p['chapters'][0]['scenes'][0]['shots'][0]
        self.assertEqual(s['imageMetadata']['source'],'manual-upload')
        self.assertEqual(s['imagePath'],'old.png');self.assertEqual(s['history'][-1]['imagePath'],'current.png')
        with self.assertRaises(ValueError):self.apply('attach-image',path='../other.png')
        with self.assertRaises(ValueError):self.apply('attach-image',path='missing.png')
    def test_audio_restore_keeps_current_recording_and_scales_timing_honestly(self):
        p=self.apply('restore-audio',index=0);c=p['chapters'][0]
        self.assertEqual(c['audio']['path'],'old.wav');self.assertEqual(c['audioHistory'][-1]['path'],'a.wav')
        self.assertEqual(c['scenes'][0]['shots'][0]['end'],20);self.assertTrue(c['timingNeedsReview'])
        self.assertEqual(c['timingHistory'][-1]['scenes'][0]['shots'][0]['end'],10)
    def test_audio_with_different_script_is_not_silently_selected(self):
        self.store.mutate(self.p['id'],lambda p:p['chapters'][0].update(cleanNarrationText='Changed story.'))
        with self.assertRaisesRegex(ValueError,'different narration'):
            self.apply('restore-audio',index=0)
    def test_preset_roundtrip_preserves_budget_assets_and_never_copies_credentials(self):
        def edit(p):
            p['settings']['budget']={'openaiUSD':2}
            p['settings']['director']['apiKey']='private'
            p['settings']['video'].update(fps=30,motionMode='gentle')
            p['settings']['engagement']['outroAudioPath']='private/audio.wav'
        self.store.mutate(self.p['id'],edit)
        saved=self.apply('save-setup',name='Fantasy')
        self.assertNotIn('budget',saved['savedSetups'][0]['settings'])
        self.assertNotIn('apiKey',saved['savedSetups'][0]['settings']['director'])
        self.assertNotIn('outroAudioPath',saved['savedSetups'][0]['settings']['engagement'])
        target=new_project('Other');target['settings']['budget']={'openaiUSD':.25};target=self.store.save(target)
        result=workspace_change(self.store,target['id'],{'revision':target['revision'],'operation':'apply-setup',
                 'sourceProject':self.p['id'],'name':'Fantasy'})
        self.assertEqual(result['settings']['budget'],{'openaiUSD':.25})
        self.assertEqual(result['settings']['video']['fps'],30)
        self.assertNotIn('apiKey',result['settings']['director'])
        self.assertTrue(result['renderStale']);self.assertEqual(result['chapters'][0]['sourceText'],'')
    def test_missing_and_cloud_assets_do_not_restore_media_during_listing(self):
        self.store.mutate(self.p['id'],lambda p:p['chapters'][0]['audio'].update(path='missing.wav'))
        rows=located_files(self.store,self.p['id'],[{'path':'old.png','cloud':True,'bytes':4}])
        rows={r['path']:r for r in rows}
        self.assertEqual(rows['missing.wav']['storageStatus'],'MISSING')
        self.assertTrue(rows['old.png']['local']);self.assertEqual(rows['old.png']['storageStatus'],'CLOUD_SAVED')
        self.assertFalse(self.store.asset_local(self.p['id'],'missing.wav').exists())

    def test_repeated_history_references_are_resolved_once_and_rechecked_next_listing(self):
        self.store.mutate(self.p['id'],lambda p:p.update(styleReferences=[{'path':'old.png'} for _ in range(2000)]+
            [{'path':'missing.png'} for _ in range(1000)]))
        with patch.object(self.store,'asset_local',wraps=self.store.asset_local) as resolve:
            rows=located_files(self.store,self.p['id'],[{'path':'old.png','cloud':True,'bytes':4}])
            names=[call.args[1] for call in resolve.call_args_list]
            self.assertEqual(names.count('old.png'),1)
            self.assertEqual(names.count('missing.png'),2) # discovery + final evidence
            self.assertEqual(next(r for r in rows if r['path']=='missing.png')['storageStatus'],'MISSING')
        self.store.asset_local(self.p['id'],'old.png').unlink()
        rows=located_files(self.store,self.p['id'],[{'path':'old.png','cloud':True,'bytes':4}])
        self.assertFalse(next(r for r in rows if r['path']=='old.png')['local'])
        self.assertTrue(next(r for r in rows if r['path']=='old.png')['cloud'])

if __name__=='__main__':unittest.main()
