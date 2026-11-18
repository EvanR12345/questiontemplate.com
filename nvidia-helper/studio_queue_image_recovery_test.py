"""Direct image recovery must identify one job's output before deciding to dispatch."""
import copy
import hashlib
import json
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from studio_data import ProjectStore, get_shot, new_project
from studio_queue_image_recovery import QueuedImageReceipt, ImageRecoveryRequired, run_queued_image
from studio_qc import review_signature
from studio_render_inputs import render_submission_signature, visual_spec_signature
from studio_workspace import workspace_change


class DirectImageRecoveryTest(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ProjectStore(folder.name)
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE queued_image_receipts(job TEXT PRIMARY KEY,receipt TEXT NOT NULL)')
        self.service = SimpleNamespace(store=self.store, db=self.db, cv=threading.RLock(),
            visual_spec_signature=lambda p,s: visual_spec_signature(p,s,self.store),
            saved_image_ready=lambda p,s: s.get('status') in ('COMPLETE','PASSED'),
            begin_queued_image_rental=Mock(return_value=None), generate=Mock())
        p = new_project('Recovery fixture')
        p['settings']['qcCheckLevel'] = 'off'
        c = p['chapters'][0]
        self.shot = {'id':'shot-one', 'sceneId':'scene-one', 'chapterId':c['id'], 'prompt':'A quiet archive.',
            'characters':[], 'camera':{}, 'generationSettings':{'seed':8}, 'start':0, 'end':5,
            'imageProvider':'existing', 'imageModel':'sd15', 'status':'READY_FOR_IMAGES'}
        c['scenes'] = [{'id':'scene-one', 'shots':[self.shot]}]
        self.store.save(p)
        self.job = {'id':'job-one', 'project':p['id'], 'chapter':c['id'], 'shot':self.shot['id'],
                    'seed':8, 'attempt':1, 'started':10, 'message':'Helper restarted.'}
        self.receipt = QueuedImageReceipt(self.service,self.job)
        self.options = {'shotSnapshot':copy.deepcopy(self.shot)}

    def project(self):
        return self.store.load(self.job['project'])

    def selected(self, p):
        return get_shot(p,self.job['chapter'],self.job['shot'])

    def dispatch(self, provider='existing', qc=None):
        p = self.project()
        shot = self.selected(p)
        selected = shot.get('imagePath','')
        selected_sha = self.store.asset_digest(p['id'],selected) if selected else None
        rel = self.receipt.dispatch(provider,0,self.service.visual_spec_signature(p,shot),selected,selected_sha,
                                    shot.get('qcOverride'),qc or {'status':'UNCHECKED','pass':None},
                                    shot.get('imageSelectionRevision'))
        self.path = self.store.asset_local(p['id'],rel)
        self.path.parent.mkdir(exist_ok=True)
        self.rel = rel

    def publish_files(self):
        self.path.write_bytes(b'deterministic test image bytes')
        self.sha = hashlib.sha256(self.path.read_bytes()).hexdigest()
        self.metadata = {'imageSHA256':self.sha, 'queueJobId':self.job['id'], 'seed':8,
                         'attempt':0, 'requestedModel':'sd15', 'referenceImages':[]}
        self.path.with_suffix('.json').write_text(json.dumps(self.metadata))

    def run_job(self):
        return run_queued_image(self.service,self.project(),self.job,self.options)

    def test_durable_sidecar_before_selection_and_existing_final_selection_are_idempotent(self):
        self.dispatch()
        self.publish_files()
        self.assertTrue(self.run_job())
        self.assertTrue(self.run_job())
        shot = self.selected(self.project())
        self.assertEqual((shot['imagePath'],shot['status']),(self.rel,'COMPLETE'))
        self.assertEqual(shot.get('history',[]),[])
        self.assertEqual(shot['retryHistory'],[self.metadata])
        self.service.begin_queued_image_rental.assert_not_called()
        self.service.generate.assert_not_called()
        # A new explicit regenerate has a different durable job ID, even for the same seed.
        other = self.job | {'id':'job-two','message':'Waiting','attempt':0,'started':None}
        self.assertFalse(run_queued_image(self.service,self.project(),other,{'shotSnapshot':copy.deepcopy(shot)}))
        self.service.generate.assert_called_once()

    def test_unknown_remote_dispatch_and_corrupt_result_hold_without_another_rental(self):
        self.dispatch('comfyui')
        with self.assertRaises(ImageRecoveryRequired) as unknown:
            self.run_job()
        self.assertTrue(unknown.exception.pause_queue)
        self.publish_files()
        self.path.write_bytes(b'different image')
        with self.assertRaisesRegex(ImageRecoveryRequired,'do not match'):
            self.run_job()
        self.service.begin_queued_image_rental.assert_not_called()
        self.service.generate.assert_not_called()

    def test_local_interruption_before_output_resumes_with_original_seed(self):
        self.dispatch()
        self.assertFalse(self.run_job())
        self.assertEqual(self.service.generate.call_args.args[3],8)

    def test_manual_selection_after_crash_is_retained_with_the_recovered_alternative(self):
        self.dispatch()
        self.publish_files()
        p = self.project()
        other = 'manual.png'
        self.store.asset_local(p['id'],other).write_bytes(b'editor selection')
        self.selected(p).update(imagePath=other,imageMetadata={'source':'manual-upload'})
        self.store.save(p)
        with self.assertRaisesRegex(ImageRecoveryRequired,'current selection'):
            self.run_job()
        s = self.selected(self.project())
        self.assertEqual(s['imagePath'],other)
        self.assertEqual(s['history'][0]['imagePath'],self.rel)
        self.assertFalse(s['history'][0]['selected'])
        self.service.generate.assert_not_called()

    def choose(self, path):
        p = self.project()
        self.store.asset_local(p['id'], path).write_bytes(path.encode())
        workspace_change(self.store, p['id'], {'operation':'attach-image', 'chapter':self.job['chapter'],
            'shot':self.job['shot'], 'path':path, 'revision':p['revision']})
        return self.selected(self.project())['imageSelectionRevision']

    def test_deliberate_reselection_of_original_bytes_retains_choice_and_render_cache(self):
        first = self.choose('original.png')
        self.options['shotSnapshot'] = copy.deepcopy(self.selected(self.project()))
        signature = render_submission_signature(self.project(),self.store)
        self.dispatch('comfyui')
        self.publish_files()
        self.choose('replacement.png')
        last = self.choose('original.png')
        self.assertNotEqual(first,last)
        self.assertEqual(render_submission_signature(self.project(),self.store),signature)
        with self.assertRaisesRegex(ImageRecoveryRequired,'current selection'):
            self.run_job()
        shot = self.selected(self.project())
        self.assertEqual((shot['imagePath'],shot['imageSelectionRevision']),('original.png',last))
        self.assertEqual([(h['imagePath'],h['selected']) for h in shot['history'] if h['imagePath']==self.rel],
                         [(self.rel,False)])
        self.service.generate.assert_not_called()
        self.service.begin_queued_image_rental.assert_not_called()

    def test_receipt_from_older_helper_observes_versionless_manual_history(self):
        self.choose('original.png')
        p = self.project()
        self.selected(p).pop('imageSelectionRevision')
        self.store.save(p)
        self.options['shotSnapshot'] = copy.deepcopy(self.selected(p))
        self.dispatch('comfyui')
        self.publish_files()
        self.choose('other.png')
        self.choose('original.png')
        p = self.project()
        self.selected(p).pop('imageSelectionRevision')
        self.store.save(p)
        with self.assertRaisesRegex(ImageRecoveryRequired,'current selection'):
            self.run_job()
        shot = self.selected(self.project())
        self.assertEqual(shot['imagePath'],'original.png')
        self.assertTrue(any(row.get('imagePath')==self.rel and row.get('selected') is False for row in shot['history']))

    def test_undo_is_a_new_selection_but_unrelated_shot_retains_its_version(self):
        p = self.project()
        other = copy.deepcopy(self.selected(p))
        other.update(id='other-shot',imageSelectionRevision='older-version')
        p['chapters'][0]['scenes'][0]['shots'].append(other)
        self.store.save(p)
        first = self.choose('original.png')
        second = self.choose('replacement.png')
        p = self.project()
        workspace_change(self.store,p['id'],{'operation':'undo','actionId':p['actionHistory'][-1]['id'],
                                             'revision':p['revision']})
        p = self.project()
        selected = self.selected(p)
        self.assertEqual(selected['imagePath'],'original.png')
        self.assertNotIn(selected['imageSelectionRevision'],(first,second))
        self.assertEqual(get_shot(p,self.job['chapter'],'other-shot')['imageSelectionRevision'],'older-version')

    def test_confirmed_remote_failure_is_retryable_after_reopen_with_selection_intact(self):
        self.choose('original.png')
        self.options['shotSnapshot'] = copy.deepcopy(self.selected(self.project()))
        self.dispatch('comfyui')
        self.receipt.confirm_retry_safe()
        self.assertFalse(self.run_job())
        self.service.generate.assert_called_once()
        self.choose('replacement.png')
        self.choose('original.png')
        with self.assertRaisesRegex(ImageRecoveryRequired,'current selection'):
            self.run_job()
        self.service.generate.assert_called_once()

    def test_current_acceptance_survives_and_pending_qc_is_never_certified(self):
        self.dispatch(qc={'status':'PENDING','pass':None})
        self.publish_files()
        p = self.project()
        p['settings']['qcCheckLevel'] = 'practical'
        self.store.save(p)
        with self.assertRaisesRegex(ImageRecoveryRequired,'Visual QC'):
            self.run_job()
        p = self.project()
        s = self.selected(p)
        self.assertEqual((s['status'],s['qc']['status']),('QC','PENDING'))
        # A user accepts after a completed visual check whose original finding remains available.
        s['qc'] = {'status':'FAILED','pass':False,'issues':['Wrong identity.']}
        override = {'decision':'accepted','imagePath':self.rel,'imageSHA256':self.sha,
                    'reviewSpecSignature':review_signature(p,s,self.store),'reason':'Reviewed the actual output.'}
        s['qcOverride'] = override
        self.store.save(p)
        self.assertTrue(self.run_job())
        s = self.selected(self.project())
        self.assertEqual(s['qcOverride'],override)
        self.assertEqual(s['qc']['pass'],False)
        self.assertEqual(s['qcDecision']['disposition'],'accepted')
        self.service.generate.assert_not_called()

    def test_preupgrade_remote_job_has_no_proof_and_local_user_change_is_retained(self):
        self.options['shotSnapshot']['imageProvider']='comfyui'
        with self.assertRaisesRegex(ImageRecoveryRequired,'no verified output receipt'):
            self.run_job()
        self.options['shotSnapshot']['imageProvider']='existing'
        p=self.project()
        self.selected(p)['imagePath']='chosen-later.png'
        self.store.save(p)
        with self.assertRaisesRegex(ImageRecoveryRequired,'no verified output receipt'):
            self.run_job()
        self.service.generate.assert_not_called()


class LegacyQueueUpgradeTest(unittest.TestCase):
    def run_old_job(self, status, action, provider='comfyui', traces=(), error='', attempts=1):
        from studio_service import StudioService

        class IdleService(StudioService):
            def worker(self):
                pass

        with tempfile.TemporaryDirectory() as root, patch('studio_service.__file__',str(Path(root)/'studio_service.py')):
            def open_service():
                return IdleService(root,SimpleNamespace(gpu='Test'),lambda:None,threading.Lock(),
                                   lambda:None,lambda:None,SimpleNamespace(control=lambda *_:None))
            old = open_service()
            p = new_project('Old queue')
            c = p['chapters'][0]
            shot = {'id':'old-shot','chapterId':c['id'],'sceneId':'scene','prompt':'The archive.',
                    'imageProvider':provider,'imageModel':'sd15','characters':[],'camera':{},'manual':{},
                    'status':'FAILED','generationSettings':{'seed':8},'start':0,'end':4}
            c['scenes'] = [{'id':'scene','shots':[shot]}]
            if error:
                c['errors'].append({'job':'old-job','message':error})
            old.store.save(p)
            old.db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                ('old-job',p['id'],c['id'],shot['id'],'image',json.dumps({'shotSnapshot':shot}),status,
                 error or 'Older worker status',0,1,2 if attempts else None,1,attempts,8))
            old.db.commit()
            for prior in traces:
                with old.trace.span(p['id'],'Queue: image',{'jobId':'old-job'},kind='job'):
                    for stage,backend in prior:
                        span = old.trace.begin(p['id'],stage,{'shot':shot['id'],'seed':8,'provider':backend})
                        old.trace.finish(span,.1,'FAILED')
            old.close()
            helper = open_service()
            helper.begin_queued_image_rental = Mock(return_value=None)
            helper.generate = Mock()
            try:
                if action == 'cancel-retry':
                    helper.control('cancel-job','old-job',p['id'])
                    helper.control('retry','old-job')
                elif action == 'retry':
                    helper.control('retry','old-job')
                elif action == 'bulk':
                    helper.control('retry')
                else:
                    helper.control(action,project=p['id'])
                helper.thread = threading.Thread(target=StudioService.worker,args=(helper,),daemon=True)
                helper.thread.start()
                deadline = time.monotonic()+5
                while time.monotonic()<deadline:
                    queue = helper.snapshot()
                    job = queue['jobs'][0]
                    if job['status'] in ('COMPLETE','INTERRUPTED','FAILED'):
                        return (job['status'],queue['paused'],helper.generate.call_count,
                                helper.begin_queued_image_rental.call_count)
                    time.sleep(.02)
                self.fail('The migrated worker did not settle the job.')
            finally:
                helper.close()

    def test_normal_controls_preserve_receiptless_paid_liability(self):
        for action in ('retry','bulk','retry-missing','cancel-retry','resume'):
            with self.subTest(action=action):
                status = 'RUNNING' if action in ('cancel-retry','resume') else 'FAILED'
                self.assertEqual(self.run_old_job(status,action,error='RemoteGenerationUncertain: disconnected'),
                                 ('INTERRUPTED',True,0,0))
        # An old local snapshot never froze the configured fallback provider.
        # No execution history, or a local call followed by Comfy, cannot prove it stayed local.
        for traces in ((), ((('Image backend','existing'),('Image backend','comfyui')),)):
            with self.subTest(traces=traces):
                self.assertEqual(self.run_old_job('FAILED','retry',provider='existing',traces=traces),
                                 ('INTERRUPTED',True,0,0))

    def test_legacy_retries_require_complete_local_or_definitive_unsubmitted_proof(self):
        safe = ('COMPLETE',False,1,1)
        self.assertEqual(self.run_old_job('CANCELLED','retry',attempts=0),safe)
        local = (('Image backend','existing'),)
        remote = (('Image backend','comfyui'),)
        self.assertEqual(self.run_old_job('FAILED','retry',provider='existing',traces=(local,)),safe)
        self.assertEqual(self.run_old_job('FAILED','retry',traces=(remote,),
                         error='ImageAdmissionBlocked: no POST'),safe)
        ambiguous = ('INTERRUPTED',True,0,0)
        for traces,attempts in (((local,),2), ((remote,remote),2),
                                (((('Image backend','existing'),('Save image',None)),),1)):
            with self.subTest(traces=traces, attempts=attempts):
                self.assertEqual(self.run_old_job('FAILED','retry',traces=traces,
                                 attempts=attempts,error='ImageAdmissionBlocked: final admission refused'),ambiguous)


if __name__=='__main__':
    unittest.main()
