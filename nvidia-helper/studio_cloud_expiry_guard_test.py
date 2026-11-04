import copy,unittest
from studio_cloud_expiry_guard import Guard

class CloudGuardTests(unittest.TestCase):
    def fixture(self):
        c={'podId':'fixture12345','name':'studio-cloud-test','nonce':'secret-fixture','created':100,'deadline':160,
           'volume':{'id':'volume12345','name':'studio-volume-test','size':1,'dataCenter':'EU-RO-1','type':'STANDARD'}}
        objects={'/pods/fixture12345':{'id':c['podId'],'name':c['name'],'env':{'QT_CLOUD_NONCE':c['nonce']}},'/network-volumes/volume12345':dict(c['volume'])}
        calls=[];at={'v':120};consumers=[]
        def api(path,method='GET'):
            calls.append((method,path))
            if path.startswith('/pods?'):return list(objects[p] for p in objects if p.startswith('/pods/'))+consumers
            if method=='DELETE':objects.pop(path,None);return {}
            return copy.deepcopy(objects.get(path))
        g=Guard(c,api,clock=lambda:at['v'])
        return g,objects,calls,at,consumers
    def test_no_mutation_before_original_deadline(self):
        g,o,c,a,_=self.fixture();self.assertFalse(g.tick());self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_deadline_deletes_volume_then_exact_self_without_local_control(self):
        g,o,c,a,_=self.fixture();a['v']=161;self.assertTrue(g.tick());self.assertEqual([x for x in c if x[0]=='DELETE'],[('DELETE','/network-volumes/volume12345'),('DELETE','/pods/fixture12345')]);self.assertFalse(o)
    def test_foreign_volume_is_never_deleted(self):
        g,o,c,a,_=self.fixture();o['/network-volumes/volume12345']['name']='foreign';a['v']=161
        with self.assertRaises(ValueError):g.tick()
        self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_foreign_consumer_is_never_stopped(self):
        g,o,c,a,consumers=self.fixture();consumers.append({'id':'foreign','mounts':{'network':[{'volumeId':'volume12345'}]}});a['v']=161;self.assertFalse(g.tick());self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_nonce_mismatch_never_mutates(self):
        g,o,c,a,_=self.fixture();o['/pods/fixture12345']['env']['QT_CLOUD_NONCE']='foreign';a['v']=161
        with self.assertRaises(ValueError):g.tick()
        self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_gpu_self_expiry_does_not_delete_shared_volume(self):
        g,o,c,a,_=self.fixture();g.c.pop('volume');a['v']=161;self.assertTrue(g.tick());self.assertIn('/network-volumes/volume12345',o)
    def test_volume_guardian_cannot_mount_the_volume_it_must_delete(self):
        g,o,c,a,_=self.fixture();o['/pods/fixture12345']['mounts']={'network':[{'volumeId':'volume12345'}]}
        with self.assertRaises(ValueError):g.tick()
        self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_heartbeat_binds_identity_deadline_and_living_guard_process(self):
        g,o,c,a,_=self.fixture();g.arm();beat=g.beat()
        self.assertEqual(beat['name'],g.c['name']);self.assertEqual(beat['nonce'],g.c['nonce'])
        self.assertEqual(beat['deadline'],160);self.assertGreater(beat['pid'],0)
    def test_foreign_consumer_on_later_page_blocks_deletion(self):
        g,o,c,a,_=self.fixture();original=g.api;a['v']=161
        def paged(path,method='GET'):
            if path.startswith('/pods?'):
                if 'cursor=' not in path:return {'pods':[],'pagination':{'hasNextPage':True,'nextCursor':'next'}}
                return {'pods':[{'id':'foreign','mounts':{'network':[{'volumeId':'volume12345'}]}}],'pagination':{'hasNextPage':False,'nextCursor':None}}
            return original(path,method)
        g.api=paged;self.assertFalse(g.tick());self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_malformed_inventory_never_deletes_volume(self):
        g,o,c,a,_=self.fixture();original=g.api;a['v']=161
        def malformed(path,method='GET'):return {'unknown':[]} if path.startswith('/pods?') else original(path,method)
        g.api=malformed
        with self.assertRaises(ValueError):g.tick()
        self.assertFalse(any(x[0]=='DELETE' for x in c))
    def test_missing_or_nonboolean_pagination_never_deletes_volume(self):
        for inventory in ({'pods':[]},{'pods':[],'pagination':{'hasNextPage':None,'nextCursor':None}}):
            g,o,c,a,_=self.fixture();original=g.api;a['v']=161
            def malformed(path,method='GET'):return inventory if path.startswith('/pods?') else original(path,method)
            g.api=malformed
            with self.assertRaises(ValueError):g.tick()
            self.assertFalse(any(x[0]=='DELETE' for x in c))

if __name__=='__main__':unittest.main()
