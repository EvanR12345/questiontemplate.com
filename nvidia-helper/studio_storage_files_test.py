import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from studio_storage import R2Archive

class FileRefreshTest(unittest.TestCase):
    def test_explicit_refresh_discovers_remote_worker_outputs_without_downloading_media(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);calls=[]
            archive=object.__new__(R2Archive);archive.enabled=True
            archive.store=SimpleNamespace(folder=lambda _:root,asset_local=lambda _pid,name:root/name)
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
