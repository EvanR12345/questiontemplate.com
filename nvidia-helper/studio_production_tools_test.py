import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from studio_data import new_project, ProjectStore, digest
from studio_action_history import record, undo
from studio_workspace import workspace_change
from studio_workspace_report import reference_checks, completion_report
from studio_render import VideoRenderer


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.p = new_project('Test')
    def edit(self, fn):
        before = copy.deepcopy(self.p)
        fn(self.p)
        record(self.p, before, 'Test edit')
        return self.p['actionHistory'][-1]['id']
    def test_undo_keeps_unrelated_generated_work_and_handles_missing_fields(self):
        id = self.edit(lambda p: p['chapters'][0].update(sourceText='Changed', cleanNarrationText='Changed'))
        self.p['chapters'][0]['audio'] = {'path':'new.wav'}
        undo(self.p, id)
        self.assertEqual(self.p['chapters'][0]['sourceText'], '')
        self.assertEqual(self.p['chapters'][0]['audio']['path'], 'new.wav')
        self.assertTrue(self.p['renderStale'])
    def test_conflict_refuses_partial_undo_and_allows_newer_first(self):
        first = self.edit(lambda p: p.update(name='One'))
        second = self.edit(lambda p: p.update(name='Two'))
        before = copy.deepcopy(self.p)
        with self.assertRaisesRegex(ValueError,'Newer work'): undo(self.p, first)
        self.assertEqual(self.p, before)
        undo(self.p, second);undo(self.p, first)
        self.assertEqual(self.p['name'],'Test')
    def test_identity_guard_prevents_editing_a_replaced_chapter(self):
        identity = self.edit(lambda p:p['chapters'][0].update(name='New name'))
        self.p['chapters'][0]['id']='different'
        with self.assertRaisesRegex(ValueError,'moved or replaced'):undo(self.p,identity)
    def test_renaming_does_not_discard_visual_acceptances_or_trigger_new_checks(self):
        shot={'imagePath':'face.png','qc':{'pass':True},'qcOverride':{'decision':'accepted'}}
        self.p['chapters'][0]['scenes']=[{'shots':[shot]}]
        identity=self.edit(lambda p:p['chapters'][0].update(name='Another name'))
        undo(self.p,identity)
        self.assertTrue(shot['qc']['pass']);self.assertEqual(shot['qcOverride']['decision'],'accepted')
    def test_history_is_bounded_and_excludes_keys(self):
        for i in range(60): self.edit(lambda p:p.update(name=str(i)))
        self.assertEqual(len(self.p['actionHistory']),50)
        before=copy.deepcopy(self.p)
        self.p['settings']['apiKey']='secret';self.p['name']='Another'
        record(self.p,before,'Manual')
        self.assertNotIn('secret',str(self.p['actionHistory']))
    def test_atomic_sensitive_edits_are_not_retained_and_undo_invalidates_acceptance(self):
        identity=self.edit(lambda p:p['chapters'][0].update(sourceText='Changed story'))
        self.p['chapters'][0]['scenes']=[{'shots':[{'imagePath':'face.png','qcOverride':{'decision':'accepted'}}]}]
        undo(self.p,identity)
        self.assertNotIn('qcOverride',self.p['chapters'][0]['scenes'][0]['shots'][0])
        before=copy.deepcopy(self.p)
        self.p['characters'].append({'id':'person','name':'Person','credential':'private'})
        record(self.p,before,'Imported character')
        self.assertFalse(self.p['actionHistory'][-1]['undoable'])
        self.assertNotIn('private',str(self.p['actionHistory']))
    def test_workspace_undo_revision_guard_and_restart_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            store=ProjectStore(folder)
            id=self.edit(lambda p:p.update(name='Changed'))
            p=store.save(self.p)
            reloaded=ProjectStore(folder)
            with self.assertRaises(ValueError):workspace_change(reloaded,p['id'],{'operation':'undo','actionId':id,'revision':-1})
            result=workspace_change(reloaded,p['id'],{'operation':'undo','actionId':id,'revision':p['revision']})
            self.assertEqual(result['name'],'Test')


class ReportTest(unittest.TestCase):
    def test_reference_checks_are_metadata_only_and_report_missing_paths(self):
        p=new_project('Test');p['characters']=[{'id':'hero','name':'Hero','references':[{'path':'face.png'}]}]
        p['chapters'][0]['scenes']=[{'shots':[{'characters':[{'id':'hero'}]}]}]
        calls=[];store=SimpleNamespace(has_asset=lambda pid,path:calls.append(path) or False)
        rows=reference_checks(store,p)
        self.assertFalse(rows[-1]['ready']);self.assertTrue(rows[-1]['blocking']);self.assertEqual(calls,['face.png'])
    def test_cost_uses_detail_receipts_once_and_stale_export_is_never_complete(self):
        p=new_project('Test');p['render']={'path':'old.mp4','duration':20};p['renderStale']=True
        p['production']={'status':'COMPLETE','timings':[
            {'provider':'openai-luna','detail':True,'estimatedUSD':.01},
            {'provider':'openai-luna','estimatedUSD':.01,'costRollup':True},
            {'provider':'openai-luna','detail':True,'usagePending':True}],
            'costs':{'gpuWindowEstimatedUSD':.02}}
        service=SimpleNamespace(store=SimpleNamespace(load=lambda pid:copy.deepcopy(p)),trace_report=lambda pid:{'spans':[], 'summary':{}})
        r=completion_report(service,p['id'])
        self.assertEqual(r['status'],'STALE_EXPORT');self.assertEqual(r['cost']['apiKnownEstimatedUSD'],.01)
        self.assertEqual(r['cost']['apiUnknownReceipts'],1)
        self.assertIsNone(r['time']['observedCalendarSeconds'])
        self.assertTrue(r['videoPath']);self.assertFalse(r['videoCurrent'])
        p['renderStale']=False;p['production']['status']='RUNNING'
        self.assertEqual(completion_report(service,p['id'])['status'],'RUNNING')


class PreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=ProjectStore(self.tmp.name);self.p=new_project('Preview')
        self.p['settings']['qcCheckLevel']='off';c=self.p['chapters'][0]
        self.store.folder(self.p['id']).mkdir(parents=True, exist_ok=True)
        c.update(cleanNarrationText='Narration',audio={'path':'voice.wav','duration':80,'textDigest':digest('Narration')},render={'path':'original.mp4'})
        c['scenes']=[{'id':'s','shots':[{'id':'a','start':0,'end':40,'imagePath':'a.png'},
            {'id':'b','start':40,'end':80,'imagePath':'b.png'}]}]
        self.renderer=VideoRenderer(self.store,{})
    def test_bounded_preview_uses_copy_and_never_replaces_saved_export(self):
        before=copy.deepcopy(self.p);captured=[]
        self.renderer.asset_identity=lambda *args:'hash'
        self.renderer.cached_asset=lambda *args:True
        def chapter(p,c,gate):
            captured.append((p,c));return {'path':'preview.mp4','duration':c['audio']['duration']}
        self.renderer.chapter=chapter;self.renderer.watermark_export=lambda p,r,g:r
        result=self.renderer.preview(self.p,self.p['chapters'][0],'b',lambda *_:None)
        self.assertLessEqual(result['duration'],30);self.assertEqual(result['shotId'],'b')
        self.assertEqual(self.p,before);self.assertTrue(result['preview'])
        self.assertEqual(captured[0][1]['scenes'][0]['shots'][0]['start'],0)
        self.assertEqual(captured[0][1]['render'],{})
    def test_preview_rejects_changed_audio_or_missing_shot_before_rendering(self):
        c=self.p['chapters'][0]
        with self.assertRaisesRegex(ValueError,'existing shot'):self.renderer.preview(self.p,c,'missing',lambda *_:None)
        c['cleanNarrationText']='Changed'
        with self.assertRaisesRegex(ValueError,'Narration changed'):self.renderer.preview(self.p,c,'a',lambda *_:None)
    def test_actual_tiny_ffmpeg_preview_decodes_and_keeps_originals(self):
        import os, json, subprocess, wave
        from pathlib import Path
        from PIL import Image
        ffmpeg=os.environ.get('STUDIO_TEST_FFMPEG')
        if not ffmpeg or not Path(ffmpeg).is_file():self.skipTest('Existing FFmpeg path not supplied')
        p=self.p;c=p['chapters'][0]
        p['settings']['video'].update(width=1280,height=720,fps=30,motionMode='static')
        p['settings']['watermark']={'enabled':False}
        c['audio']['duration']=2
        c['scenes'][0]['shots']=[{'id':'a','start':0,'end':1,'imagePath':'a.png'},
                                {'id':'b','start':1,'end':2,'imagePath':'b.png'}]
        for name,color in [('a.png','navy'),('b.png','maroon')]:
            Image.new('RGB',(320,180),color).save(self.store.asset_local(p['id'],name))
        with wave.open(str(self.store.asset_local(p['id'],'voice.wav')),'wb') as audio:
            audio.setparams((1,2,24000,0,'NONE','not compressed'));audio.writeframes(b'\0\0'*48000)
        before=copy.deepcopy(p)
        self.renderer.config['ffmpeg']=ffmpeg
        result=self.renderer.preview(p,c,'a',lambda *_:None)
        target=self.store.asset_local(p['id'],result['path'])
        self.assertTrue(target.is_file());self.assertEqual(p,before)
        decoded=subprocess.run([ffmpeg,'-hide_banner','-i',str(target),'-f','null','-'],capture_output=True,text=True)
        self.assertEqual(decoded.returncode,0,decoded.stderr)
        self.assertIn('1280x720',decoded.stderr);self.assertIn('30 fps',decoded.stderr)
        self.assertIn('Audio: aac',decoded.stderr);self.assertIn('Duration: 00:00:02.',decoded.stderr)
        self.assertTrue(self.store.asset_local(p['id'],'a.png').is_file())

    def test_signature_reads_only_media_in_the_preview_interval(self):
        seen=[]
        self.renderer.asset_identity=lambda pid,path:seen.append(path) or path
        with patch('studio_branding.identity',return_value='branding'):
            self.renderer.preview_signature(self.p,self.p['chapters'][0],'b')
        self.assertEqual(seen,['voice.wav','b.png'])

    def test_preview_queue_states_never_promote_or_fail_the_whole_chapter(self):
        import sqlite3,threading
        from studio_service import StudioService
        c=self.p['chapters'][0]
        c['scenes'][0]['chapterId']=c['id']
        for shot in c['scenes'][0]['shots']:shot.update(chapterId=c['id'],sceneId='s',status='COMPLETE',characters=[],camera={},generationSettings={})
        p=self.store.save(self.p);db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        self.addCleanup(db.close)
        db.execute('CREATE TABLE jobs(project,chapter,shot,status,kind,created)')
        service=SimpleNamespace(cv=threading.Condition(),db=db,store=self.store,paused=False)
        for status in ('QUEUED','RUNNING','COMPLETE','FAILED','CANCELLED'):
            db.execute('DELETE FROM jobs')
            db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?)',(p['id'],p['chapters'][0]['id'],None,status,'render-preview',1))
            StudioService.sync_queue_states(service,p['id'])
            self.assertEqual(self.store.load(p['id'])['chapters'][0]['status'],p['chapters'][0]['status'])

    def test_original_review_policy_still_blocks_preview(self):
        self.p['settings']['qcCheckLevel']='full'
        with patch('studio_render.qc_decision',return_value={'blocking':True}):
            with self.assertRaisesRegex(ValueError,'Review or repair'):self.renderer.preview(self.p,self.p['chapters'][0],'a',lambda *_:None)


if __name__=='__main__':unittest.main()
