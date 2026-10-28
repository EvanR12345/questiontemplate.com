"""Exact-byte preparation/cache tests; every network response is a local fake."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import random
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from PIL import Image
import image_provider as images


def data_url(raw):return 'data:image/png;base64,'+base64.b64encode(raw).decode()
def legacy(value):
    raw=base64.b64decode(value.split(',',1)[-1]);return raw,hashlib.sha256(raw).hexdigest()


class DecodedReferenceCacheTest(unittest.TestCase):
    def test_repeated_immutable_reference_decodes_and_hashes_once(self):
        cache=images.ImageEncodingCache();url=data_url(b'exact immutable reference')
        with patch('image_provider.base64.b64decode',wraps=base64.b64decode) as decoder,patch('image_provider.hashlib.sha256',wraps=hashlib.sha256) as digest:
            first=cache.reference_bytes(url)
            for _ in range(12):self.assertIs(cache.reference_bytes(url),first)
            self.assertEqual(decoder.call_count,1);self.assertEqual(digest.call_count,1)
        self.assertEqual(first,legacy(url))

    def test_file_encoder_primes_exact_bytes_without_a_second_decode_or_digest(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ref.png';path.write_bytes(b'original portrait')
            cache=images.ImageEncodingCache();url=cache.encode(path)
            with patch('image_provider.base64.b64decode',side_effect=AssertionError('duplicate decode')),patch('image_provider.hashlib.sha256',side_effect=AssertionError('duplicate digest')):
                self.assertEqual(cache.reference_bytes(url)[0],b'original portrait')

    def test_same_size_timestamp_collision_new_reference_never_reuses_old_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ref.png';path.write_bytes(b'original portrait')
            cache=images.ImageEncodingCache()
            with patch.object(cache,'stamp',return_value=(1,17,42,42)):
                old=cache.encode(path);path.write_bytes(b'changed  portrait');new=cache.encode(path)
            self.assertNotEqual(old,new)
            self.assertEqual(cache.reference_bytes(new),legacy(new))
            self.assertNotEqual(cache.reference_bytes(old),cache.reference_bytes(new))

    def test_parallel_same_input_returns_one_complete_immutable_preparation(self):
        cache=images.ImageEncodingCache();url=data_url(b'x'*20000)
        with patch('image_provider.base64.b64decode',wraps=base64.b64decode) as decoder,ThreadPoolExecutor(6) as pool:
            values=list(pool.map(cache.reference_bytes,[url]*30))
        self.assertEqual(decoder.call_count,1)
        self.assertTrue(all(value is values[0] for value in values))

    def test_memory_budget_counts_keys_values_and_lru_storage(self):
        for budget in (80,512,1024,4096):
            cache=images.ImageEncodingCache(max_bytes=budget)
            for i in range(150):
                value=data_url(bytes([i])*40);cache.reference_bytes(value)
                self.assertLessEqual(cache.decoded_retained_bytes,budget)
                self.assertLessEqual(len(cache.decoded),128)
            cost=sum(sys.getsizeof(key)+sys.getsizeof(entry)+sum(sys.getsizeof(x) for x in entry) for key,entry in cache.decoded.items())
            self.assertEqual(cost,cache.decoded_bytes)

    def test_oversized_input_bypasses_and_does_not_evict_useful_entries(self):
        cache=images.ImageEncodingCache(max_bytes=4096);small=data_url(b'portrait');cache.reference_bytes(small)
        before=cache.decoded_retained_bytes;huge=data_url(b'x'*8000)
        with patch('image_provider.base64.b64decode',wraps=base64.b64decode) as decoder:
            for _ in range(2):self.assertEqual(cache.reference_bytes(huge),legacy(huge))
        self.assertEqual(decoder.call_count,4) # Two uncached prepares plus two oracle decodes.
        self.assertNotIn(huge,cache.decoded);self.assertIn(small,cache.decoded)
        self.assertEqual(cache.decoded_retained_bytes,before)

    def test_many_small_references_obey_entry_limit_and_content_keys(self):
        cache=images.ImageEncodingCache(max_bytes=2**20)
        for i in range(300):
            value=data_url(str(i).encode());self.assertEqual(cache.reference_bytes(value),legacy(value))
        self.assertEqual(len(cache.decoded),128)
        self.assertLessEqual(cache.decoded_retained_bytes,cache.max_bytes)

    def test_lru_hit_protects_recent_input_and_invalid_base64_never_is_retained(self):
        cache=images.ImageEncodingCache(max_bytes=1500);values=[data_url(bytes([i])*90) for i in range(4)]
        cache.reference_bytes(values[0]);cache.reference_bytes(values[1]);cache.reference_bytes(values[0]);cache.reference_bytes(values[2])
        self.assertIn(values[0],cache.decoded)
        with self.assertRaises(Exception):cache.reference_bytes('data:image/png;base64,a')
        self.assertNotIn('data:image/png;base64,a',cache.decoded)

    def fake_workflows(self,use_cache):
        cache=images.ImageEncodingCache()
        if not use_cache:cache.reference_bytes=legacy
        provider=images.ComfyImageProvider({'comfyPersistentHTTP':False});settings=dict(model='unchanged',steps=4,seed=42,width=64,height=64)
        template=dict(model='unchanged',name='exact-edit',prompt={
            str(i):dict(class_type='LoadImage',inputs=dict(image='unset')) for i in range(3)},
            bindings={('reference'+str(i)):[[str(i),'image']] for i in range(3)})
        provider.validateSettings=lambda s:copy.deepcopy(s)
        provider.resolve_template=lambda r,s:(copy.deepcopy(template),s)
        provider.template=lambda:copy.deepcopy(template)
        provider.healthCheck=lambda **_:dict(installed=True)
        provider.getCapabilities=lambda:dict(maxReferenceImages=3)
        source,first,second=map(data_url,(b'source exact',b'portrait A',b'portrait B'))
        request=dict(settings=settings,prompt='unchanged prompt',negativePrompt='unchanged negative',operation='edit',
                     sourceImage=source,referenceImages=[first,second],workflow='exact-edit')
        before=copy.deepcopy(request);uploads=[];submissions=[];events=[]
        output=io.BytesIO();Image.new('RGB',(2,2),'gray').save(output,format='PNG')
        def urlopen(url,**_):
            if isinstance(url,str):return io.BytesIO(output.getvalue())
            raw=url.data.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0];uploads.append(raw)
            return io.BytesIO(json.dumps(dict(name=hashlib.sha256(raw).hexdigest()+'.png')).encode())
        def network(url,data=None,**_):
            if url.endswith('/prompt'):
                submissions.append(copy.deepcopy(data['prompt']));return dict(prompt_id='same-owned-prompt')
            return {'same-owned-prompt':dict(outputs={'out':dict(images=[dict(filename='result.png')])})}
        with patch.object(images,'_image_encoding_cache',cache),patch('image_provider.urllib.request.urlopen',side_effect=urlopen),patch('image_provider.request_json',side_effect=network):
            results=[provider.generateImage(request,lambda *x:events.append(x)) for _ in range(2)]
        self.assertEqual(request,before)
        self.assertEqual(len(uploads),3);self.assertEqual(uploads,[b'source exact',b'portrait A',b'portrait B'])
        self.assertEqual([r['providerTimings']['referenceUploadCacheHits'] for r in results],[0,3])
        return submissions,[r['resolvedWorkflow'] for r in results],uploads,events,[r['settings'] for r in results]

    def test_cached_preparation_preserves_exact_workflow_reference_order_and_uploads(self):
        self.assertEqual(self.fake_workflows(False),self.fake_workflows(True))


def benchmark():
    """Paired real PNG file prep only: no image generation/provider calls."""
    with tempfile.TemporaryDirectory() as folder:
        paths=[]
        for seed in range(3):
            path=Path(folder)/f'reference-{seed}.png'
            Image.frombytes('RGB',(1024,768),random.Random(seed).randbytes(1024*768*3)).save(path,format='PNG');paths.append(path)
        runs=[]
        for _ in range(3):
            pair={};identities={}
            for enabled in (False,True):
                cache=images.ImageEncodingCache()
                if not enabled:cache._remember_reference=lambda value,raw,fingerprint:(raw,fingerprint)
                start=time.perf_counter();proof=[]
                for _shot in range(30):
                    for path in paths:
                        url=cache.encode(path);raw,fingerprint=cache.reference_bytes(url) if enabled else legacy(url)
                        proof.append((len(raw),fingerprint))
                name='cached' if enabled else 'baseline';pair[name]=time.perf_counter()-start;identities[name]=proof
                if enabled:pair['newDecodedCacheRetainedBytes']=cache.decoded_retained_bytes
            assert identities['cached']==identities['baseline'];runs.append(pair)
        report=dict(scope='Local exact PNG file read/identity + reference preparation only; not GPU/API/render throughput.',
            references=3,shotsPerRun=30,pairedRuns=3,referenceBytes=[p.stat().st_size for p in paths],
            exactBytesAndHashes=True,workflowParity='Separate fully mocked ComfyUI test verifies exact workflow/ref order/settings/upload bytes.',
            runs=runs,baselineSeconds=sum(r['baseline'] for r in runs)/3,cachedSeconds=sum(r['cached'] for r in runs)/3,
            providerCalls=0,generationCalls=0)
        report['meanPreparationSecondsSaved']=report['baselineSeconds']-report['cachedSeconds']
        print(json.dumps(report,indent=2));return report


if __name__=='__main__':
    if '--benchmark' in sys.argv:benchmark()
    else:unittest.main()
