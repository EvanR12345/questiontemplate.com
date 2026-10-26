import os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from studio_storage import R2Archive,local_media

class FileRefreshTest(unittest.TestCase):
    def test_new_and_legacy_audio_remain_visible_in_the_audio_filter_at_every_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            audio=['narration.opus','preview.MP3','legacy.wav','legacy.m4a','legacy.flac']
            for name in audio+['story.mp4','art.png','project.json']:(root/name).write_bytes(b'fixture')
            archive=object.__new__(R2Archive);archive.enabled=False;archive.states={}
            archive.store=SimpleNamespace(folder=lambda _:root,asset_local=lambda _pid,name:root/name,
                load_local=lambda _: {'audio':{'path':'missing.OPUS'}})
            for enabled in (False,True):
                archive.enabled=enabled;archive.states={'project':{'status':'QUEUED'}}
                archive.manifest=lambda *args:({'project':{'revision':3},'files':{
                    'archived/voice.opus':{'bytes':42,'sha256':'a'*64}}},'etag')
                rows={r['path']:r for r in archive.files('project')['files']}
                for name in audio+['missing.OPUS']:self.assertEqual(rows[name]['category'],'Audio',name)
                self.assertEqual(rows['story.mp4']['category'],'Video')
                self.assertEqual(rows['art.png']['category'],'Images')
                self.assertEqual(rows['missing.OPUS']['storageStatus'],'MISSING')
                if enabled:
                    self.assertEqual(rows['archived/voice.opus']['category'],'Audio')
                    self.assertEqual(rows['archived/voice.opus']['storageStatus'],'CLOUD_SAVED')
                    self.assertEqual(rows['narration.opus']['storageStatus'],'PENDING_UPLOAD')
            self.assertTrue(all((root/name).read_bytes()==b'fixture' for name in audio))

    def test_unreadable_tree_cannot_be_reported_as_a_successful_partial_scan(self):
        def unreadable(folder, **options):
            options['onerror'](PermissionError('Synthetic unreadable folder'))
            yield
        with patch('studio_storage.os.walk',side_effect=unreadable):
            with self.assertRaises(PermissionError):list(local_media(Path('fixture')))

    def test_listing_prunes_private_and_working_trees_without_hiding_saved_nested_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            paths=['image.PNG','nested/voice.wav','nested/part.mp4','project.json','notes.txt',
                   'working/deep/clip.mp4','.private/secret.json','nested/.hidden/photo.png',
                   'nested/clip.partial.abc.mp4','batch.writing.abc/result.png']
            for name in paths:
                file=root/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(b'test')
            walk=os.walk;visited=[]
            def observed(*args,**kwargs):
                for entry in walk(*args,**kwargs):
                    visited.append(Path(entry[0]).relative_to(root).as_posix());yield entry
            with patch('studio_storage.os.walk',side_effect=observed):
                found={p.relative_to(root).as_posix() for p in local_media(root)}
            self.assertEqual(found,{'image.PNG','nested/voice.wav','nested/part.mp4','project.json'})
            self.assertEqual(set(visited),{'.','nested'})
            self.assertTrue((root/'working/deep/clip.mp4').exists(),'Listing must not delete cached work')

    def test_explicit_refresh_discovers_remote_worker_outputs_without_downloading_media(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);calls=[]
            archive=object.__new__(R2Archive);archive.enabled=True
            archive.states={}
            archive.store=SimpleNamespace(folder=lambda _:root,asset_local=lambda _pid,name:root/name,load_local=lambda _: {})
            def manifest(pid,refresh=False):
                calls.append(refresh)
                files={'cloud-renders/verified/story.mp4':{'bytes':42,'sha256':'a'*64}} if refresh else {}
                return {'project':{'revision':3},'files':files},'etag'
            archive.manifest=manifest
            self.assertEqual(archive.files('project')['files'],[])
            rows=archive.files('project',refresh=True)['files']
            self.assertEqual(rows[0]['path'],'cloud-renders/verified/story.mp4')
            self.assertTrue(rows[0]['cloud']);self.assertEqual(calls,[False,True])
            self.assertEqual(list(root.iterdir()),[])

if __name__=='__main__':unittest.main()
