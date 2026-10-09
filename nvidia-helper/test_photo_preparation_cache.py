import unittest
from studio_gpu_render import PhotoPreparation


class Future:
    def __init__(self,value):self.value=value;self.cancelled=False
    def result(self):return self.value
    def cancel(self):self.cancelled=True


class Pool:
    def __init__(self):self.submitted=[]
    def submit(self,fn,path,gate):
        future=Future(('prepared-'+path).encode())
        self.submitted.append((path,future))
        return future
    def shutdown(self,**kwargs):pass


class PhotoCacheTests(unittest.TestCase):
    def fixture(self):
        obj=PhotoPreparation.__new__(PhotoPreparation)
        obj.paths=['A','B'];obj.positions={'A':0,'B':1}
        obj.next=None;obj.last=None;obj.pool=Pool();obj.prepared=[]
        def prepare(path,gate):
            obj.prepared.append(path)
            return ('prepared-'+path).encode()
        obj.prepare=prepare
        return obj

    def test_consecutive_same_photo_reuses_fit_and_preserves_prefetch(self):
        obj=self.fixture()
        first=obj.take('A',lambda:None)
        upcoming=obj.next
        self.assertEqual(obj.take('A',lambda:None),first)
        self.assertIs(obj.next,upcoming)
        self.assertFalse(upcoming[1].cancelled)
        self.assertEqual(obj.prepared,['A'])
        self.assertEqual(len(obj.pool.submitted),1)
        self.assertEqual(obj.take('B',lambda:None),b'prepared-B')
        self.assertIsNone(obj.next)
        self.assertEqual(obj.last,('B',b'prepared-B'))

    def test_cache_is_one_photo_not_an_unbounded_dictionary(self):
        obj=self.fixture()
        obj.take('A',lambda:None);obj.take('B',lambda:None)
        obj.take('A',lambda:None)
        self.assertEqual(obj.prepared,['A','A'])
        self.assertEqual(obj.last,('A',b'prepared-A'))


if __name__=='__main__':unittest.main()
