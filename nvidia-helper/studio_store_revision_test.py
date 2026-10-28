"""Persistence safety across cached, externally changed and oversized snapshots."""
import json,tempfile,unittest
from unittest.mock import patch
from studio_data import ProjectStore,new_project

class StoreRevisionTest(unittest.TestCase):
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
