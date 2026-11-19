"""Persistence safety across cached, externally changed and oversized snapshots."""
import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from studio_data import ProjectStore,new_project

class StoreRevisionTest(unittest.TestCase):
    def test_read_only_scan_detects_external_same_length_edit_with_restored_timestamp(self):
        import os
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Run A'));pid=p['id']
            read=store.read_only_loader(pid)
            first=read();self.assertIs(read(),first)
            path=store.folder(pid)/'project.json';original=path.read_bytes();stamp=path.stat()
            changed=original.replace(b'Run A',b'Run B',1)
            self.assertNotEqual(original,changed);self.assertEqual(len(original),len(changed))
            path.write_bytes(changed);os.utime(path,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
            fresh=read();self.assertEqual(fresh['name'],'Run B');self.assertIsNot(fresh,first)
            self.assertEqual(first['name'],'Run A');self.assertIs(fresh,read())

    def test_large_read_only_views_refresh_across_an_independent_write(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=new_project('One');p['chapters'][0]['sourceText']='x'*(1200*1024)
            store.save(p);read=store.read_only_loader(p['id'])
            self.assertEqual(read()['name'],'One')
            other=ProjectStore(root);other.mutate(p['id'],lambda data:data.update(name='Two'))
            self.assertEqual(read()['name'],'Two')

    def test_large_save_does_not_need_a_second_whole_file_allocation(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root)
            source=new_project('Large history')
            source['chapters'][0]['sourceText']='scene notes '*(130*1024)
            project=store.save(source)
            path=store.folder(project['id'])/'project.json'
            previous=path.read_bytes()
            loaded=store.load(project['id'])
            loaded['name']='Edited'
            original=Path.read_bytes
            def constrained_read(file):
                if file==path:raise MemoryError('simulate the second allocation failing on Windows')
                return original(file)
            with patch.object(Path,'read_bytes',constrained_read):
                store.save(loaded,expected=loaded['revision'])
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),previous)
            self.assertEqual(store.load(project['id'])['name'],'Edited')

    def test_large_external_conflict_and_corruption_retain_both_snapshots(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);source=new_project('Edited elsewhere')
            source['chapters'][0]['sourceText']='archived material '*(90*1024)
            p=store.save(source)
            store.mutate(p['id'],lambda q:q.update(name='Established backup'))
            loaded=store.load(p['id']);path=store.folder(p['id'])/'project.json'
            backup=path.with_name('project.previous.json').read_bytes()
            external=dict(loaded,revision=loaded['revision']+1)
            for raw,err in ((json.dumps(external).encode(),ValueError),(b'{broken'+b' '*1100000,json.JSONDecodeError)):
                path.write_bytes(raw)
                with self.assertRaises(err):store.save(loaded,expected=loaded['revision'])
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(path.with_name('project.previous.json').read_bytes(),backup)
                self.assertFalse(path.with_name('project.previous.tmp').exists())
    def test_mutation_keeps_exact_backup_and_revision_without_second_decode(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project('Original'))
            path=store.folder(p['id'])/'project.json';before=path.read_bytes()
            original=json.loads
            with patch('studio_data.json.loads',wraps=original) as decode:
                changed=store.mutate(p['id'],lambda q:q.update(name='Updated'))
                self.assertEqual(decode.call_count,1)
            self.assertEqual(changed['revision'],p['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),before)
            self.assertIsNone(store._loaded_revision)
            self.assertEqual(store.load(p['id'])['name'],'Updated')

    def test_external_revision_wins_and_expected_conflict_retains_original_files(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project());loaded=store.load(p['id'])
            path=store.folder(p['id'])/'project.json';external=json.loads(path.read_bytes())
            external['revision']+=8;external['name']='External'
            raw=json.dumps(external,ensure_ascii=False,indent=2).encode('utf-8');path.write_bytes(raw)
            with self.assertRaisesRegex(ValueError,'Project changed'):
                store.save(loaded,expected=p['revision'])
            self.assertEqual(path.read_bytes(),raw)
            self.assertFalse(path.with_name('project.previous.json').exists())
            saved=store.save(loaded)
            self.assertEqual(saved['revision'],external['revision']+1)
            self.assertEqual(path.with_name('project.previous.json').read_bytes(),raw)

    def test_public_load_dict_mutation_cannot_change_cached_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project());loaded=store.load(p['id'])
            loaded['revision']=9000;loaded['name']='Caller update'
            self.assertEqual(store.save(loaded)['revision'],p['revision']+1)
            self.assertEqual(store.load(p['id'])['name'],'Caller update')

    def test_cache_is_bounded_and_cross_project_reads_do_not_confuse_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);a=store.save(new_project('A'));b=store.save(new_project('B'))
            loaded=store.load(a['id']);store.load(b['id'])
            self.assertEqual(store.save(loaded)['revision'],a['revision']+1)
            loaded=store.load(a['id']);loaded['notes']='x'*(8*1024*1024)
            store.save(loaded);store.load(a['id'])
            self.assertIsNone(store._loaded_revision[1])
            self.assertEqual(len(store._loaded_revision[3]),32)
            original=json.loads
            with patch('studio_data.json.loads',wraps=original) as decode:
                store.mutate(a['id'],lambda q:q.update(name='Large update'))
                self.assertEqual(decode.call_count,1)
            raw=store.folder(a['id']).joinpath('project.json').read_bytes()
            self.assertEqual(json.loads(raw)['name'],'Large update')

    def test_corrupt_external_file_is_not_overwritten_using_cached_revision(self):
        with tempfile.TemporaryDirectory() as root:
            store=ProjectStore(root);p=store.save(new_project());loaded=store.load(p['id'])
            path=store.folder(p['id'])/'project.json';path.write_bytes(b'{broken')
            with self.assertRaises(json.JSONDecodeError):store.save(loaded)
            self.assertEqual(path.read_bytes(),b'{broken')
            self.assertFalse(path.with_name('project.previous.json').exists())

if __name__=='__main__':unittest.main()
