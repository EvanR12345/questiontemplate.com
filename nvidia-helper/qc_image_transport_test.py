"""QC-only exact transport, bounded cache and raw result-cache compatibility."""
import base64
import copy
import hashlib
import io
import json
import struct
import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from PIL.PngImagePlugin import PngInfo
import qc_image_transport as transport
import openai_director_test as fixtures
from openai_director import OpenAIDirector
from director_provider import obj,STR


def png(mode='RGB',color='red',size=(128,128),**kwargs):
    image=Image.new(mode,size,color);buffer=io.BytesIO();image.save(buffer,format='PNG',**kwargs)
    return buffer.getvalue()
def data(raw):return 'data:image/png;base64,'+base64.b64encode(raw).decode('ascii')


class ExactTransportTest(unittest.TestCase):
    def test_rgb_and_rgba_pixels_including_transparent_rgb_remain_exact(self):
        cache=transport.QCImageTransport()
        for mode,color in [('RGB',(40,70,120)),('RGBA',(20,50,90,0)),('RGBA',(40,70,120,128))]:
            with self.subTest(mode=mode,color=color):
                raw=png(mode,color);converted=cache.convert(data(raw))
                self.assertTrue(converted.startswith('data:image/webp;base64,'))
                encoded=base64.b64decode(converted.split(',',1)[1]);self.assertLess(len(encoded),len(raw))
                with Image.open(io.BytesIO(raw)) as original,Image.open(io.BytesIO(encoded)) as decoded:
                    self.assertEqual(original.mode,decoded.mode);self.assertEqual(original.size,decoded.size)
                    self.assertEqual(original.tobytes(),decoded.tobytes())
                    self.assertEqual(original.convert('RGBA').tobytes(),decoded.convert('RGBA').tobytes())

    def test_metadata_and_unsupported_modes_remain_original(self):
        info=PngInfo();info.add_text('note','preserve metadata')
        originals=[png(pnginfo=info),png(icc_profile=b'profile'),png('L',100),png('P',1)]
        cache=transport.QCImageTransport()
        for raw in originals:
            value=data(raw);self.assertEqual(cache.convert(value),value)
        self.assertEqual(cache.snapshot()['entries'],0)

    def test_sixteen_bit_truecolor_rgb_and_rgba_fall_back_before_pillow_loses_precision(self):
        def chunk(name,value):return struct.pack('>I',len(value))+name+value+struct.pack('>I',zlib.crc32(name+value)&0xffffffff)
        for color,channels in ((2,3),(6,4)):
            with self.subTest(color=color):
                header=struct.pack('>IIBBBBB',16,16,16,color,0,0,0)
                pixel=b''.join(struct.pack('>H',n) for n in (0x1234,0x5678,0x9abc,0xfedc)[:channels])
                raw=(b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',header)+
                    chunk(b'IDAT',zlib.compress((b'\x00'+pixel*16)*16))+chunk(b'IEND',b''))
                with Image.open(io.BytesIO(raw)) as decoded:
                    self.assertIn(decoded.mode,('RGB','RGBA')) # Pillow exposes an 8-bit mode here.
                value=data(raw);self.assertEqual(transport.QCImageTransport().convert(value),value)

    def test_animation_remains_original(self):
        out=io.BytesIO();Image.new('RGB',(32,32),'red').save(out,format='PNG',save_all=True,
            append_images=[Image.new('RGB',(32,32),'blue')],duration=100,loop=0)
        value=data(out.getvalue());self.assertEqual(transport.QCImageTransport().convert(value),value)

    def test_invalid_non_data_non_png_and_oversize_inputs_fall_back_unchanged(self):
        value=data(png());cache=transport.QCImageTransport(max_source_bytes=1)
        self.assertEqual(cache.convert(value),value)
        self.assertEqual(transport.QCImageTransport(max_pixels=1).convert(value),value)
        for original in ('https://example.com/image.png','data:image/jpeg;base64,AA==',
                'data:image/png;base64,@@@',data(b'not a PNG'),None,123):
            self.assertEqual(transport.QCImageTransport().convert(original),original)

    def test_encoder_error_and_not_smaller_output_preserve_input(self):
        value=data(png());cache=transport.QCImageTransport()
        with patch.object(Image.Image,'save',side_effect=OSError('unavailable encoder')):
            self.assertEqual(cache.convert(value),value)
        self.assertEqual(cache.snapshot()['pending'],0)
        def larger(image,buffer,**kwargs):buffer.write(b'x'*10000)
        with patch.object(Image.Image,'save',larger):self.assertEqual(cache.convert(value),value)
        self.assertTrue(cache.convert(value).startswith('data:image/webp;base64,'))
        equal_url='data:image/webp;base64,'+'A'*(len(value)-len('data:image/webp;base64,'))
        with patch.object(transport.QCImageTransport,'_encode',return_value=equal_url):
            self.assertEqual(transport.QCImageTransport().convert(value),value)

    def test_changed_decode_pixels_or_mode_cannot_enter_cache(self):
        value=data(png());cache=transport.QCImageTransport();original=Image.Image.save
        def wrong(image,buffer,**kwargs):
            return original(Image.new('RGB',image.size,'blue'),buffer,**kwargs)
        with patch.object(Image.Image,'save',wrong):self.assertEqual(cache.convert(value),value)
        self.assertEqual(cache.snapshot()['entries'],0)

    def test_full_content_hash_invalidates_same_length_replacement(self):
        a=png(color='red');b=png(color='blue');self.assertEqual(len(a),len(b))
        cache=transport.QCImageTransport();first=cache.convert(data(a));second=cache.convert(data(b))
        self.assertNotEqual(first,second);self.assertEqual(cache.snapshot()['entries'],2)

    def test_cache_lru_and_payload_budget_remain_bounded(self):
        cache=transport.QCImageTransport(max_bytes=600,max_entries=2)
        values=[data(png(color=color)) for color in ('red','blue','green','yellow')]
        for value in values:
            cache.convert(value);self.assertLessEqual(cache.snapshot()['accountedBytes'],600)
        self.assertEqual(cache.snapshot()['entries'],2)
        keys=[key[0] for key in cache.cache]
        expected=[hashlib.sha256(base64.b64decode(value.split(',',1)[1])).hexdigest() for value in values[-2:]]
        self.assertEqual(keys,expected)
        tiny=transport.QCImageTransport(max_bytes=1);self.assertNotEqual(tiny.convert(values[0]),values[0])
        self.assertEqual(tiny.snapshot()['entries'],0)

    def test_codec_version_is_part_of_cache_key(self):
        cache=transport.QCImageTransport();value=data(png());cache.convert(value)
        with patch('qc_image_transport.PILLOW_VERSION','different-version'):cache.convert(value)
        self.assertEqual(cache.snapshot()['entries'],2)

    def test_same_input_single_flight_and_other_input_encodes_without_global_lock(self):
        cache=transport.QCImageTransport();value=data(png());other=data(png(color='blue'))
        entered=threading.Event();release=threading.Event();original=cache._encode;calls=[];lock=threading.Lock()
        first_raw=base64.b64decode(value.split(',',1)[1])
        def encode(raw):
            with lock:calls.append(raw)
            if raw==first_raw:entered.set();self.assertTrue(release.wait(3))
            return original(raw)
        with patch.object(cache,'_encode',side_effect=encode),ThreadPoolExecutor(max_workers=7) as pool:
            futures=[pool.submit(cache.convert,value) for _ in range(6)]
            try:
                self.assertTrue(entered.wait(3))
                self.assertNotEqual(pool.submit(cache.convert,other).result(timeout=2),other)
            finally:release.set()
            self.assertEqual(len({future.result(timeout=3) for future in futures}),1)
        self.assertEqual(sum(raw==first_raw for raw in calls),1)
        self.assertEqual(cache.snapshot()['pending'],0)


class AdapterTransportTest(unittest.TestCase):
    def test_raw_result_cache_identity_and_cache_hit_without_encoding(self):
        with tempfile.TemporaryDirectory() as folder:
            director=OpenAIDirector({},folder);director.key=lambda:'synthetic-key'
            context={'expectedShot':{'action':'unchanged'},'_images':[data(png())]};before=copy.deepcopy(context)
            schema=obj({'summary':STR});role='Visual quality reviewer: test'
            identity={'provider':'openai-luna','adapterVersion':1,'model':director.model,'role':role,
                'context':context,'schema':schema,'reasoning':'medium','vision':True}
            expected=Path(folder)/'director-cache'/(hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()+'.json')
            with patch('openai_director.urllib.request.urlopen',return_value=fixtures.stream()) as request:
                director.call(role,context,schema,lambda *_:None,vision=True)
            body=json.loads(request.call_args.args[0].data)
            self.assertTrue(body['input'][1]['content'][1]['image_url'].startswith('data:image/webp;base64,'))
            self.assertEqual(body['input'][1]['content'][1]['detail'],'low')
            self.assertEqual(context,before);self.assertTrue(expected.is_file())
            with patch('qc_image_transport.qc_image_urls',side_effect=AssertionError('Cached QC encoded')), \
                 patch('openai_director.urllib.request.urlopen') as request:
                self.assertEqual(director.call(role,context,schema,lambda *_:None,vision=True),{'summary':'complete'})
                request.assert_not_called()

    def test_other_vision_roles_and_nonvision_never_apply_qc_transport(self):
        with tempfile.TemporaryDirectory() as folder:
            director=OpenAIDirector({},folder);director.key=lambda:'synthetic-key'
            with patch('qc_image_transport.qc_image_urls',side_effect=AssertionError('Not QC')), \
                 patch('openai_director.urllib.request.urlopen',side_effect=lambda *_a,**_k:fixtures.stream()):
                director.call('Other vision task',{'_images':[data(png())]},obj({'summary':STR}),lambda *_:None,vision=True)
                director.call('Visual quality reviewer: text only',{},obj({'summary':STR}),lambda *_:None)


if __name__=='__main__':unittest.main()
