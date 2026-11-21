import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from studio_cloud_test import Objects
from studio_data import ProjectStore, new_project
from studio_storage import R2Archive


class VerifiedObjects(Objects):
    def verify_object(self, key, checksum, size):
        data, _ = self.read(key)
        if data is None or len(data) != size or hashlib.sha256(data).hexdigest() != checksum:
            raise ValueError('Fixture object has missing or corrupted bytes.')


class ArchivedProjectTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.objects = VerifiedObjects()
        self.store = ProjectStore(self.root/'local')
        self.archive = R2Archive(self.store, self.root/'unused', self.objects, start_worker=False)
        self.project = new_project('Archived project')
        self.project['chapters'][0]['sourceText'] = 'PRIVATE chapter text'
        self.pid = self.project['id']
        self.head = f'studio/manifests/{self.pid}.json'

    def put_edition(self, files=None):
        files = files or {}
        manifest = {'version':1, 'project':copy.deepcopy(self.project), 'files':files, 'savedAt':123}
        key = self.archive.history_key(self.pid, self.project, files)
        self.objects.put_json(key, manifest)
        return key, key.split('/')[-1][:-5], manifest

    def test_find_then_restore_exact_edition_without_disclosing_or_altering_history(self):
        key, edition, manifest = self.put_edition()
        self.project['revision'] += 1
        self.project['chapters'][0]['sourceText'] += ' newer'
        self.put_edition()
        before = dict(self.objects.data)
        listing = self.archive.archived_projects()
        self.assertEqual(len(listing['projects']), 2)
        self.assertNotIn('PRIVATE', json.dumps(listing))
        self.assertEqual(next(s for s in listing['projects'] if s['edition']==edition)['storyCharacters'],20)
        self.assertEqual(before, self.objects.data)
        self.archive.catalogue = [];self.archive.catalogue_at = 12
        result = self.archive.recover_archived(self.pid, edition)
        self.assertEqual(result['project']['id'],self.pid)
        self.assertEqual(json.loads(self.objects.read(self.head)[0]),manifest)
        self.assertEqual(self.objects.read(key),before[key])
        self.assertEqual(self.archive.catalogue_at,0)
        self.assertFalse(self.store.folder(self.pid).exists())
        self.assertEqual(self.archive.archived_projects()['projects'],[])
        self.assertEqual(self.store.load(self.pid),manifest['project'])
        with self.assertRaises(ValueError):
            self.archive.recover_archived(self.pid,edition)

    def test_missing_and_corrupt_assets_are_rejected_then_correct_bytes_can_recover(self):
        body=b'complete picture'
        checksum=hashlib.sha256(body).hexdigest()
        obj=f'studio/assets/{self.pid}/{checksum}/picture.png'
        self.project['intro']['visualPath']='picture.png'
        _, edition, _=self.put_edition({'picture.png': {'sha256':checksum,'bytes':len(body),'key':obj}})
        for value in (None, b'broken picture!!'):
            if value is not None:self.objects.data[obj]=(value,'fixture')
            with self.assertRaisesRegex(ValueError,'verified'):
                self.archive.recover_archived(self.pid,edition)
            self.assertIsNone(self.objects.read(self.head)[0])
        self.objects.data[obj]=(body,'fixture')
        self.archive.recover_archived(self.pid,edition)
        self.store.load(self.pid)
        self.assertEqual(self.store.asset(self.pid,'picture.png').read_bytes(),body)

    def test_unlisted_asset_bad_identity_and_traversal_do_not_create_heads(self):
        self.project['intro']['visualPath']='missing.png'
        key,edition,manifest=self.put_edition()
        row=self.archive.archived_projects()['projects'][0]
        self.assertEqual(row['missingMedia'],1)
        self.assertFalse(row['recoverable'])
        with self.assertRaises(ValueError):self.archive.recover_archived(self.pid,edition)
        del manifest['project']['intro']['visualPath']
        self.objects.put_json(key,manifest)
        with self.assertRaises(ValueError):self.archive.recover_archived(self.pid,edition)
        for bad in ('../'+edition, 'a'*16, None):
            with self.assertRaises(ValueError):self.archive.recover_archived(self.pid,bad)
        self.assertIsNone(self.objects.read(self.head)[0])

    def test_diagnostic_and_provenance_paths_do_not_claim_missing_media(self):
        self.project['timings']=[{'path':'C:/other-machine/runtime.log'}]
        self.project['chapters'][0]['analysis']={'path':'unrelated/trace.png'}
        _,edition,_=self.put_edition()
        self.assertTrue(self.archive.archived_projects()['projects'][0]['recoverable'])
        self.assertEqual(self.archive.recover_archived(self.pid,edition)['project']['missingMedia'],0)

    def test_partial_project_can_recover_its_writing_once_without_claiming_media(self):
        chapter=self.project['chapters'][0]
        chapter.update(name='Original chapter', cleanNarrationText='Edited narration',
                       audio={'path':'audio.wav'}, history=[{'path':'audio.wav'}])
        self.project['notes']='Original author notes'
        _,edition,manifest=self.put_edition()
        row=self.archive.archived_projects()['projects'][0]
        self.assertEqual(row['missingMedia'],1)
        self.assertFalse(row['recoverable'])
        with self.assertRaises(ValueError):self.archive.recover_archived(self.pid,edition)
        self.assertIsNone(self.objects.read(self.head)[0])
        result=self.archive.recover_archived(self.pid,edition,'writing')
        self.assertTrue(result['writingOnly'])
        pid=result['project']['id']
        self.assertEqual(pid,row['writingCopyId'])
        self.assertNotEqual(pid,self.pid)
        copy=self.store.load(pid)
        recovered=copy['chapters'][0]
        for key in ('name','sourceText','cleanNarrationText'):
            self.assertEqual(recovered[key],chapter[key])
        self.assertEqual(copy['notes'],self.project['notes'])
        self.assertEqual(recovered['audio'],{})
        self.assertEqual(recovered['history'],[])
        self.assertEqual(recovered['status'],'NOT_ANALYZED')
        self.store.mutate(pid,lambda p:p.update(name='Manual renamed copy'))
        self.assertEqual(self.archive.recover_archived(self.pid,edition,'writing')['project']['name'],
                         'Manual renamed copy')
        self.assertIsNone(self.objects.read(self.head)[0])
        self.assertEqual(self.archive.archived_projects()['projects'][0]['id'],self.pid)

    def test_writing_copy_never_overwrites_an_unrelated_destination(self):
        _,edition,_=self.put_edition()
        pid=self.archive.archived_projects()['projects'][0]['writingCopyId']
        head=f'studio/manifests/{pid}.json'
        unrelated={'project':new_project('Independent project')}
        self.objects.put_json(head,unrelated)
        with self.assertRaises(ValueError):self.archive.recover_archived(self.pid,edition,'writing')
        self.assertEqual(json.loads(self.objects.read(head)[0]),unrelated)

    def test_r2_recovery_verifies_bytes_and_always_closes_stream(self):
        import io
        from studio_storage import S3Objects
        obj=S3Objects.__new__(S3Objects)
        body=b'R2 archive data'
        fingerprint=hashlib.sha256(body).hexdigest()
        for checksum,size,valid in [(fingerprint,len(body),True),('0'*64,len(body),False),
                                   (fingerprint,0,False)]:
            stream=io.BytesIO(body)
            with patch.object(obj,'open_object',return_value={'body':stream,'bytes':len(body)}):
                if valid:obj.verify_object('test',checksum,size)
                else:
                    with self.assertRaises(ValueError):obj.verify_object('test',checksum,size)
                self.assertTrue(stream.closed)

    def test_existing_local_and_concurrent_cloud_head_take_precedence(self):
        _, edition, _=self.put_edition()
        self.store.folder(self.pid).mkdir()
        self.assertEqual(self.archive.archived_projects()['projects'],[])
        with self.assertRaisesRegex(ValueError,'local files'):
            self.archive.recover_archived(self.pid,edition)
        self.store.folder(self.pid).rmdir()
        put=self.objects.put_json
        def compete(key, value, **kwargs):
            self.assertEqual(kwargs,{'create':True})
            put(self.head, {'independent':'saved first'})
            return put(key, value, **kwargs)
        with patch.object(self.objects,'put_json',side_effect=compete):
            with self.assertRaisesRegex(ValueError,'confirm recovery'):
                self.archive.recover_archived(self.pid,edition)
        self.assertEqual(json.loads(self.objects.read(self.head)[0]),{'independent':'saved first'})
