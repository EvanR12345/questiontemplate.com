import base64,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from image_provider import ImageEncodingCache

class EncodingCacheTest(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'reference.png';self.path.write_bytes(b'original image bytes')
 def tearDown(self):self.tmp.cleanup()
 def test_identical_bytes_reuse_encoding_but_same_size_edits_invalidate(self):
  cache=ImageEncodingCache();read=Path.read_bytes;calls=[]
  def count(path):calls.append(path);return read(path)
  with patch.object(Path,'read_bytes',count):
   a=cache.encode(self.path);self.assertEqual(a,cache.encode(self.path));self.assertEqual(len(calls),2)
   self.path.write_bytes(b'changed  image bytes');b=cache.encode(self.path)
  self.assertNotEqual(a,b);self.assertEqual(base64.b64decode(b.split(',',1)[1]),b'changed  image bytes')
 def test_parallel_readers_get_one_complete_identical_encoding(self):
  cache=ImageEncodingCache();read=Path.read_bytes;calls=[]
  def count(path):calls.append(path);return read(path)
  with patch.object(Path,'read_bytes',count),ThreadPoolExecutor(4) as pool:
   values=list(pool.map(lambda _:cache.encode(self.path),range(12)))
  self.assertEqual(len(calls),12);self.assertTrue(all(v==values[0] for v in values))
 def test_timestamp_collision_cannot_serve_an_old_portrait(self):
  cache=ImageEncodingCache()
  with patch.object(cache,'stamp',return_value=(1,20,42,42)):
   first=cache.encode(self.path);self.path.write_bytes(b'changed  image bytes');second=cache.encode(self.path)
  self.assertNotEqual(first,second);self.assertEqual(base64.b64decode(second.split(',',1)[1]),b'changed  image bytes')
 def test_cache_is_bounded_and_oversized_images_are_not_retained(self):
  cache=ImageEncodingCache(max_bytes=80)
  for i in range(6):
   p=Path(self.tmp.name)/f'{i}.png';p.write_bytes(b'x'*20);cache.encode(p)
   self.assertLessEqual(cache.bytes,80)
  huge=Path(self.tmp.name)/'huge.png';huge.write_bytes(b'x'*100);cache.encode(huge)
  self.assertNotIn(str(huge.resolve()),cache.items);self.assertLessEqual(cache.bytes,80)
 def test_deleted_reference_never_returns_cached_stale_bytes(self):
  cache=ImageEncodingCache();cache.encode(self.path);self.path.unlink()
  with self.assertRaises(FileNotFoundError):cache.encode(self.path)

if __name__=='__main__':unittest.main()
