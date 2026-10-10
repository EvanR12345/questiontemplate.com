import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore

class SnapshotTest(unittest.TestCase):
    def test_transient_windows_lock_retries_same_atomic_publication(self):
        error=PermissionError('temporary sharing denial');error.winerror=32
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'new.json';target=Path(folder)/'old.json'
            source.write_text('new');target.write_text('old');replace=Path.replace;calls=[]
            def publish(path,destination):
                calls.append((path,destination))
                if len(calls)==1:raise error
                return replace(path,destination)
            with patch.object(Path,'replace',publish),patch('studio_data.time.sleep'):
                ProjectStore.replace_snapshot(source,target)
            self.assertEqual(target.read_text(),'new');self.assertEqual(calls,[calls[0],calls[0]])
    def test_persistent_permission_failure_preserves_original_and_staging(self):
        for code in (None,5):
            error=PermissionError('denied')
            if code:error.winerror=code
            with tempfile.TemporaryDirectory() as folder:
                source=Path(folder)/'new.json';target=Path(folder)/'old.json'
                source.write_text('new');target.write_text('old')
                with patch.object(Path,'replace',side_effect=error) as replace,patch('studio_data.time.sleep'):
                    with self.assertRaises(PermissionError):ProjectStore.replace_snapshot(source,target)
                self.assertEqual(target.read_text(),'old');self.assertEqual(source.read_text(),'new')
                self.assertEqual(replace.call_count,5 if code else 1)

if __name__=='__main__':unittest.main()
