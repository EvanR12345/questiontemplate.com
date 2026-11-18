"""Recovery of chapter audio and voice anchors across routing and storage changes."""
import base64
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from studio_data import new_project, new_chapter, ProjectStore
from studio_service import StudioService
from studio_voice_provider import KaggleVoice
from studio_voice_legacy import original_request, overlapping_group


class ChapterAdmissionTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        service=StudioService.__new__(StudioService)
        service.store=ProjectStore(self.temp.name)
        service.config={'kaggleUsername':'testowner'}
        service.cv=threading.Condition()
        service.current='job'
        service.db=sqlite3.connect(':memory:')
        self.addCleanup(service.db.close)
        service.db.row_factory=sqlite3.Row
        service.db.executescript("CREATE TABLE jobs(id,kind);INSERT INTO jobs VALUES('job','produce-story');")
        service.status=Mock()
        service.gate=Mock()
        self.service=service
        self.project=new_project()
        self.project['settings'].update(voiceEngine='qwen3-tts-1.7b',voiceChapterBatchSize=2)
        self.project['chapters'][0]['sourceText']='The first gate opened.'
        self.project['chapters'] += [new_chapter(n)|{'sourceText':text} for n,text in
                                     [(2,'The second gate opened.'),(3,'The third gate opened.')]]
        self.service.store.save(self.project)
        self.pushes=[]
        def lost(args,**kwargs):
            self.assertEqual(args[:2],['kernels','push'])
            self.pushes.append(json.loads((Path(args[3])/'kernel-metadata.json').read_text())['id'])
            raise TimeoutError('Lost push reply')
        self.enterContext(patch('studio_voice_provider.readiness',return_value=None))
        self.enterContext(patch.object(KaggleVoice,'_run',side_effect=lost))

    def narrate(self):
        self.service.store.save(self.project)
        self.service.narration(self.project,self.project['chapters'][0])

    def test_manual_and_single_retry_share_unknown_group_ownership(self):
        with self.assertRaises(TimeoutError):self.narrate()
        self.project['settings']['voiceChapterBatchSize']=1
        with self.assertRaisesRegex(ValueError,'reconciliation'):self.narrate()
        self.project['settings']['voiceChapterBatchSize']=2
        self.service.db.execute("UPDATE jobs SET kind='narration'")
        with self.assertRaisesRegex(ValueError,'reconciliation'):self.narrate()
        self.assertEqual(len(self.pushes),1)
        self.assertFalse(self.project['chapters'][0].get('audio',{}).get('path'))

    def test_legacy_unknown_group_blocks_regroup_until_admission_reconciled(self):
        with self.assertRaises(TimeoutError):self.narrate()
        groups=self.service.store.folder(self.project['id'])/'.voice-groups'
        next(groups.glob('*.publication.json')).unlink()  # pre-publication-record helper
        original=next(groups.glob('*.opus.kaggle.json'))
        journal=json.loads(original.read_text())
        journal.pop('chapterIds')
        original.write_text(json.dumps(journal))
        self.assertEqual(journal['state'],'SUBMITTING')
        self.project['settings']['voiceChapterBatchSize']=3
        with self.assertRaisesRegex(ValueError,'reconciliation'):self.narrate()
        self.assertEqual(len(self.pushes),1)
        journal['state']='REJECTED'
        original.write_text(json.dumps(journal))
        with self.assertRaises(TimeoutError):self.narrate()
        self.assertEqual(len(set(self.pushes)),2)

    def test_changed_speaker_and_text_have_distinct_immutable_targets(self):
        with self.assertRaises(TimeoutError):self.narrate()
        self.project['settings'].update(voiceChapterBatchSize=1,qwenSpeaker='Aiden')
        with self.assertRaises(TimeoutError):self.narrate()
        self.project['chapters'][0]['sourceText']='The new manual passage.'
        with self.assertRaises(TimeoutError):self.narrate()
        self.assertEqual(len(set(self.pushes)),3)
        self.assertEqual(len(list(self.service.store.folder(self.project['id']).rglob('*.opus.kaggle.json'))),3)

    def test_unconfirmed_single_request_is_retained_when_batching_is_enabled(self):
        self.project['settings']['voiceChapterBatchSize']=1
        with self.assertRaises(TimeoutError):self.narrate()
        self.project['settings']['voiceChapterBatchSize']=2
        with self.assertRaisesRegex(ValueError,'reconciliation'):self.narrate()
        self.assertEqual(len(self.pushes),1)

    def test_confirmed_legacy_group_collects_exact_original_audio_from_original_notebook(self):
        with self.assertRaises(TimeoutError):self.narrate()
        groups=self.service.store.folder(self.project['id'])/'.voice-groups'
        next(groups.glob('*.publication.json')).unlink()
        journal=next(groups.glob('*.opus.kaggle.json'))
        saved=json.loads(journal.read_text())
        saved.pop('chapterIds')
        saved.update(state='SUBMITTED',privacyVerified=True)
        journal.write_text(json.dumps(saved))
        self.project['settings']['voiceChapterBatchSize']=1
        with self.assertRaisesRegex(ValueError,'original 2-chapter'):self.narrate()
        self.project['settings']['voiceChapterBatchSize']=2
        self.completed_transport()
        self.narrate()
        project=self.service.store.load(self.project['id'])
        audio=project['chapters'][0]['audio']
        self.assertEqual(self.service.store.asset(project['id'],audio['path']).read_text(),'The first gate opened.')
        self.assertEqual(audio['voiceBatch']['groupSetupSeconds'],77)
        self.assertEqual(len(self.pushes),1)


    def run_saved(self,index=0):
        project=self.service.store.load(self.project['id'])
        return self.service.narration(project,project['chapters'][index])

    def completed_transport(self,after_output=None):
        def run(args,**kwargs):
            action=args[1]
            if action=='status':return 'complete'
            folder=Path(args[args.index('-p')+1])
            if action=='push':
                self.pushes.append(json.loads((folder/'kernel-metadata.json').read_text())['id'])
                return 'Kernel version successfully pushed.'
            if action=='pull':
                (folder/'kernel-metadata.json').write_text(json.dumps({'is_private':True}))
                return
            self.assertEqual(action,'output')
            signature=next(record['signature'] for record in (
                json.loads(journal.read_text()) for journal in folder.parent.glob('*.opus.kaggle.json'))
                if folder.name.endswith(record['signature'][:16]))
            request=original_request(folder.parent,signature)
            result={'signature':signature,'model':request['model'],'duration':len(request['parts']),
                    'setupAndModelLoadSeconds':77,'speechGenerationSeconds':20,'sentences':[]}
            for index,part in enumerate(request['parts']):
                result['sentences'].append(dict(index=index,text=part['text'],start=index,end=index+1))
                if part.get('chapterId'):
                    filename=f'chapter-{index:03d}.opus'
                    (folder/filename).write_text(part['text'])
                    result.setdefault('chapterAudio',[]).append(dict(
                        id=part['chapterId'],first=index,last=index+1,path=filename))
                else:
                    (folder/'narration.opus').write_text(part['text'])
            if request['model'] in ('dia-1.6b','fish-s2-pro'):
                reference=request.get('reference')
                audio=base64.b64decode(reference['audio']) if reference else request['model'].encode()
                (folder/'voice-anchor.opus').write_bytes(audio)
                result['voiceAnchor']={'text':reference['text'] if reference else 'Original anchor speech.',
                                       'generated':True}
            (folder/'result.json').write_text(json.dumps(result))
            if after_output:after_output()
        self.enterContext(patch.object(KaggleVoice,'_run',side_effect=run))
        self.enterContext(patch('studio_audio.probe',return_value={'codec':'opus','channels':1,'duration':1}))

    def select_engine(self,engine,size=2):
        self.project['settings'].update(voiceEngine=engine,fishCommercialLicense=engine=='fish-s2-pro',
                                        voiceChapterBatchSize=size)
        self.service.store.save(self.project)

    def test_first_dia_anchor_and_matching_legacy_selection_remain_ready(self):
        self.select_engine('dia-1.6b',size=1)
        self.completed_transport()
        base=self.service.narration_signature(self.project,self.project['chapters'][0])
        self.run_saved()
        project=self.service.store.load(self.project['id'])
        chapter=project['chapters'][0]
        anchor=project['voiceAnchors']['dia-1.6b']
        self.assertEqual(chapter['audio']['voiceAnchor'],anchor)
        self.assertEqual(chapter['audio']['signature'],base)
        self.assertNotEqual(self.service.narration_signature(project,chapter),base)
        self.assertTrue(self.service.saved_narration_ready(project,chapter))
        self.run_saved()
        self.assertEqual(len(self.pushes),1)

        for change in ('text','path','source','receipt'):
            with self.subTest(change=change):
                current=self.service.store.load(self.project['id'])
                chapter=current['chapters'][0]
                current_anchor=current['voiceAnchors']['dia-1.6b']
                if change=='text':current_anchor['text']='Changed anchor transcript.'
                elif change=='path':current_anchor['path']='voice-anchor-'+'a'*20+'.opus'
                elif change=='source':chapter['sourceText']='Rerecorded chapter.'
                else:chapter['audio'].pop('voiceAnchor')
                self.assertFalse(self.service.saved_narration_ready(current,chapter))

    def test_partial_fish_group_recovers_second_chapter_after_first_anchor_is_adopted(self):
        self.select_engine('fish-s2-pro')
        self.completed_transport()
        def interrupt(message):
            if message.startswith('Saving completed chapter'):raise InterruptedError('Publication paused')
        self.service.gate.side_effect=interrupt
        with self.assertRaises(InterruptedError):self.run_saved()
        self.service.gate.side_effect=None
        root=self.service.store.folder(self.project['id'])
        groups=root/'.voice-groups'
        staged=next(groups.glob('*.publication.json'))
        manifest=json.loads(staged.read_text())
        expected=manifest['items'][1]
        audio=manifest['outputs'][expected['id']]
        self.assertFalse((root/expected['target']).exists())

        self.service.db.execute("UPDATE jobs SET kind='narration'")
        self.run_saved()
        selected=self.service.store.load(self.project['id'])
        self.assertEqual(selected['voiceAnchors']['fish-s2-pro'],selected['chapters'][0]['audio']['voiceAnchor'])
        self.assertNotEqual(self.service.narration_signature(selected,selected['chapters'][1]),expected['signature'])
        self.assertTrue(staged.exists())
        self.service.db.execute("UPDATE jobs SET kind='produce-story'")
        self.run_saved(1)
        selected=self.service.store.load(self.project['id'])
        second=selected['chapters'][1]['audio']
        self.assertEqual(second['path'],expected['target'])
        self.assertEqual(second['voiceBatch']['groupSetupSeconds'],77)
        self.assertEqual(second['kernel'],selected['chapters'][0]['audio']['kernel'])
        self.assertEqual(second['voiceAnchor'],selected['voiceAnchors']['fish-s2-pro'])
        self.assertEqual(KaggleVoice._checksum(root/second['path']),audio['sha256'])
        written=json.loads((root/second['path']).with_suffix('.voice-ready.json').read_text())
        self.assertEqual(written['inputSignature'],expected['signature'])
        self.assertFalse(staged.exists())
        self.assertEqual(len(self.pushes),1)

    def test_stale_first_anchor_is_not_adopted_if_source_changes_while_generating(self):
        self.select_engine('dia-1.6b',size=1)
        store=self.service.store
        def edit():
            store.mutate(self.project['id'],lambda p:p['chapters'][0].update(sourceText='New source during generation.'))
        self.completed_transport(after_output=edit)
        with self.assertRaisesRegex(ValueError,'changed during generation'):self.run_saved()
        project=store.load(self.project['id'])
        self.assertFalse(project.get('voiceAnchors'))
        chapter=project['chapters'][0]
        self.assertFalse(chapter.get('audio',{}).get('path'))
        self.assertEqual(chapter['sourceText'],'New source during generation.')
        self.assertFalse(chapter['audioHistory'][0]['selected'])
        self.assertEqual(store.asset(project['id'],chapter['audioHistory'][0]['path']).read_text(),'The first gate opened.')

    def test_changed_fish_anchor_transcript_retains_generated_audio_in_history(self):
        self.select_engine('fish-s2-pro')
        store=self.service.store
        reference=b'Existing reference Opus fixture.'
        name='voice-anchor-'+hashlib.sha256(reference).hexdigest()[:20]+'.opus'
        store.asset(self.project['id'],name).write_bytes(reference)
        self.project['voiceAnchors']={'fish-s2-pro':{'path':name,'text':'Original transcript.'}}
        store.save(self.project)
        def edit():
            store.mutate(self.project['id'],lambda p:p['voiceAnchors']['fish-s2-pro'].update(text='Corrected transcript.'))
        self.completed_transport(after_output=edit)
        with self.assertRaisesRegex(ValueError,'changed during generation'):self.run_saved()
        current=store.load(self.project['id'])
        chapter=current['chapters'][0]
        self.assertFalse(chapter.get('audio',{}).get('path'))
        self.assertEqual(current['voiceAnchors']['fish-s2-pro']['text'],'Corrected transcript.')
        self.assertEqual(chapter['audioHistory'][0]['voiceAnchor']['text'],'Original transcript.')
        self.assertFalse(chapter['audioHistory'][0]['selected'])
        request=next((store.folder(current['id'])/'.voice-groups').glob('*.opus.kaggle.json'))
        signed=original_request(request.parent,json.loads(request.read_text())['signature'])
        self.assertEqual(signed['reference'],{'audio':base64.b64encode(reference).decode(),'text':'Original transcript.'})

    def test_archive_only_unselected_group_receipts_survive_manual_and_single_routing(self):
        from studio_cloud_test import Objects
        from studio_storage import R2Archive
        self.select_engine('dia-1.6b')
        self.completed_transport()
        self.run_saved()
        source=self.service.store
        pid=self.project['id']
        current=source.load(pid)
        second=current['chapters'][1]
        self.assertFalse(second.get('audio',{}).get('path'))
        base=self.service.narration_signature(current|{'voiceAnchors':{}},second)
        audio=f"{second['id']}/chapter-002-{base[:12]}.opus"
        target=source.asset(pid,audio)
        receipt=target.with_suffix('.voice-ready.json')
        original=json.loads(receipt.read_text())
        original['result']['voiceAnchor']['path']='C:\\Old Helper\\'+Path(original['result']['voiceAnchor']['path']).name
        receipt.write_text(json.dumps(original))
        objects=Objects()
        archive=R2Archive(source,source.root/'unused',objects,start_worker=False)
        self.assertTrue(archive.sync(pid))
        self.assertIn(audio,archive.manifest(pid)[0]['files'])

        for index,kind,size in ((1,'narration',2),(2,'produce-story',1)):
            with self.subTest(kind=kind):
                cold=ProjectStore(Path(self.temp.name)/f'cold-{index}')
                cold_archive=R2Archive(cold,cold.root/'unused',objects,start_worker=False)
                cold_archive.restore(pid)
                self.assertFalse(cold.asset_local(pid,audio).exists())
                self.assertFalse((cold.folder(pid)/'.voice-groups').exists())
                cold.mutate(pid,lambda p:p['settings'].update(voiceChapterBatchSize=size))
                self.service.store=cold
                self.service.db.execute('UPDATE jobs SET kind=?',(kind,))
                with patch.object(KaggleVoice,'_run',side_effect=AssertionError('Duplicate notebook')):
                    self.run_saved(1)
                restored=cold.load(pid)['chapters'][1]['audio']
                self.assertEqual(restored['path'],audio)
                self.assertEqual(restored['voiceAnchor'],current['voiceAnchors']['dia-1.6b'])
                self.assertEqual(KaggleVoice._checksum(cold.asset_local(pid,audio)),original['sha256'])
        self.assertEqual(len(self.pushes),1)

    def test_single_route_checks_archive_receipt_for_current_signature(self):
        from studio_cloud_test import Objects
        from studio_storage import R2Archive
        self.select_engine('qwen3-tts-1.7b',size=1)
        self.completed_transport()
        self.run_saved()
        source=self.service.store
        pid=self.project['id']
        original=source.load(pid)['chapters'][0]['audio']
        source.mutate(pid,lambda p:p['chapters'][0].pop('audio'))
        objects=Objects()
        archive=R2Archive(source,source.root/'unused',objects,start_worker=False)
        self.assertTrue(archive.sync(pid))
        self.service.store=ProjectStore(Path(self.temp.name)/'cold-single')
        cold=self.service.store
        R2Archive(cold,cold.root/'unused',objects,start_worker=False).restore(pid)
        self.service.db.execute("UPDATE jobs SET kind='narration'")
        self.assertFalse(cold.asset_local(pid,original['path']).exists())
        with patch.object(KaggleVoice,'_run',side_effect=AssertionError('Duplicate notebook')):
            self.run_saved()
        self.assertEqual(cold.load(pid)['chapters'][0]['audio'],original)
        self.assertEqual(len(self.pushes),1)

    def test_changed_transcript_cannot_reuse_staged_audio_from_other_anchor_identity(self):
        self.select_engine('dia-1.6b')
        self.completed_transport()
        def interrupt(message):
            if message.startswith('Saving completed chapter'):raise InterruptedError('Publication paused')
        self.service.gate.side_effect=interrupt
        with self.assertRaises(InterruptedError):self.run_saved()
        self.service.gate.side_effect=None
        root=self.service.store.folder(self.project['id'])
        group=next((root/'.voice-groups').glob('*.publication.json'))
        original=json.loads(group.read_text())
        self.service.db.execute("UPDATE jobs SET kind='narration'")
        self.run_saved()
        pid=self.project['id']
        self.service.store.mutate(pid,lambda p:p['voiceAnchors']['dia-1.6b'].update(text='Changed transcript.'))
        self.run_saved(1)
        result=self.service.store.load(pid)['chapters'][1]['audio']
        self.assertNotEqual(result['path'],original['items'][1]['target'])
        self.assertEqual(result['voiceAnchor']['text'],'Changed transcript.')
        self.assertEqual(len(set(self.pushes)),2)
        self.assertTrue(group.is_file())
        self.assertFalse((root/original['items'][1]['target']).exists())

    def test_indexed_nonoverlapping_journal_does_not_parse_private_script(self):
        self.select_engine('dia-1.6b')
        with self.assertRaises(TimeoutError):self.run_saved()
        groups=self.service.store.folder(self.project['id'])/'.voice-groups'
        journal=next(groups.glob('*.opus.kaggle.json'))
        ids=[c['id'] for c in self.project['chapters']]
        recorded=json.loads(journal.read_text())
        self.assertEqual(recorded['chapterIds'],ids[:2])
        request=original_request(groups,recorded['signature'])
        unrelated=request|{'parts':[dict(request['parts'][0],chapterId=ids[2])]}
        with patch('studio_voice_legacy.original_request',side_effect=AssertionError('Unrelated private script read')):
            self.assertIsNone(overlapping_group(groups,unrelated))
        self.assertEqual(overlapping_group(groups,request)[0],journal)
