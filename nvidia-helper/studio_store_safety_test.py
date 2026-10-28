"""Fault and shared-store concurrency checks for immutable revision reuse."""
import json
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch
from studio_data import ProjectStore,new_project,UNCHANGED


class StoreSafetyTest(unittest.TestCase):
    def test_shared_store_concurrent_mutations_keep_every_change_and_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project());pid=p['id']
            def append(n):
                return store.mutate(pid,lambda q:q.setdefault('auditMarks',[]).append(n))['revision']
            with ThreadPoolExecutor(max_workers=4) as pool:revisions=list(pool.map(append,range(24)))
            saved=store.load(pid)
            self.assertEqual(sorted(saved['auditMarks']),list(range(24)))
            self.assertEqual(sorted(revisions),list(range(p['revision']+1,p['revision']+25)))
            previous=json.loads(store.folder(pid).joinpath('project.previous.json').read_bytes())
            self.assertEqual(previous['revision'],saved['revision']-1)
            self.assertEqual(len(previous['auditMarks']),23)

    def test_nested_mutation_cannot_make_explicit_compare_and_swap_accept_old_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Original'));pid=p['id']
            def outer(q):
                store.mutate(pid,lambda inner:inner.update(name='Inner committed'))
                q['name']='Outer stale'
                with self.assertRaisesRegex(ValueError,'Project changed'):store.save(q,expected=q['revision'])
                return UNCHANGED
            store.mutate(pid,outer)
            saved=store.load(pid)
            self.assertEqual(saved['name'],'Inner committed')
            self.assertEqual(saved['revision'],p['revision']+1)

    def test_archive_failure_keeps_primary_exact_backup_and_fresh_cas(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Before'));pid=p['id']
            path=store.folder(pid)/'project.json';before=path.read_bytes();loaded=store.load(pid)
            def fail(_):raise OSError('Archive unavailable')
            store.archive=SimpleNamespace(enqueue=fail)
            loaded['name']='Saved despite archive error'
            with self.assertRaisesRegex(OSError,'Archive unavailable'):store.save(loaded,expected=p['revision'])
            self.assertIsNone(store._loaded_revision)
            actual=path.read_bytes();saved=json.loads(actual)
            self.assertEqual(saved['name'],'Saved despite archive error')
            self.assertEqual(saved['revision'],p['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),before)
            with self.assertRaisesRegex(ValueError,'Project changed'):store.save(p,expected=p['revision'])
            self.assertEqual(path.read_bytes(),actual)

    def test_metadata_publish_failure_cannot_leave_cached_old_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Before'));pid=p['id']
            path=store.folder(pid)/'project.json';before=path.read_bytes();loaded=store.load(pid)
            original=store.replace_snapshot
            def replace(source,target):
                if target.name=='revision.json':raise OSError('Metadata unavailable')
                return original(source,target)
            loaded['name']='Primary published'
            with patch.object(store,'replace_snapshot',side_effect=replace):
                with self.assertRaisesRegex(OSError,'Metadata unavailable'):store.save(loaded)
            self.assertIsNone(store._loaded_revision)
            self.assertEqual(json.loads(path.read_bytes())['revision'],p['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),before)
            changed=store.mutate(pid,lambda q:q.update(name='Next committed'))
            self.assertEqual(changed['revision'],p['revision']+2)
            self.assertEqual(store.revision(pid)['revision'],changed['revision'])

    def test_backup_publish_failure_preserves_primary_and_retry_uses_same_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Before'));pid=p['id']
            path=store.folder(pid)/'project.json';before=path.read_bytes();loaded=store.load(pid)
            original=store.replace_snapshot
            def replace(source,target):
                if target.name=='project.previous.json':raise OSError('Backup unavailable')
                return original(source,target)
            loaded['name']='Changed'
            with patch.object(store,'replace_snapshot',side_effect=replace):
                with self.assertRaisesRegex(OSError,'Backup unavailable'):store.save(loaded,expected=p['revision'])
            self.assertEqual(path.read_bytes(),before)
            saved=store.save(loaded,expected=p['revision'])
            self.assertEqual(saved['revision'],p['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),before)

    def test_large_external_equal_size_and_timestamp_change_uses_actual_sha(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=new_project('A');p['notes']='x'*(8*1024*1024)
            p=store.save(p);pid=p['id'];path=store.folder(pid)/'project.json'
            loaded=store.load(pid);self.assertIsNone(store._loaded_revision[1])
            before=path.read_bytes();stamp=path.stat();changed=json.loads(before)
            changed['revision']=p['revision']+1;changed['name']='Z'
            external=json.dumps(changed,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            self.assertEqual(len(before),len(external))
            path.write_bytes(external);os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            with self.assertRaisesRegex(ValueError,'Project changed'):store.save(loaded,expected=p['revision'])
            self.assertEqual(path.read_bytes(),external)
            saved=store.save(loaded)
            self.assertEqual(saved['revision'],changed['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),external)


if __name__=='__main__':unittest.main()
