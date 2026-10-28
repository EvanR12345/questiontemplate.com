"""Exact lossless QC wire images; original assets and result-cache inputs stay raw."""
import base64
import hashlib
import io
import struct
import threading
from collections import OrderedDict
from concurrent.futures import Future
from PIL import Image,__version__ as PILLOW_VERSION


class QCImageTransport:
    def __init__(self,max_bytes=8*1024*1024,max_source_bytes=8*1024*1024,
            max_pixels=2*1024*1024,max_entries=32):
        self.max_bytes=max_bytes;self.max_source_bytes=max_source_bytes
        self.max_pixels=max_pixels;self.max_entries=max_entries
        self.lock=threading.Lock();self.cache=OrderedDict();self.pending={};self.accounted_bytes=0

    def snapshot(self):
        with self.lock:return {'entries':len(self.cache),'accountedBytes':self.accounted_bytes,
            'maxBytes':self.max_bytes,'pending':len(self.pending)}

    @staticmethod
    def _plain_png(raw):
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):return False
        offset=8;names=[]
        while offset<len(raw):
            if offset+12>len(raw):return False
            length=struct.unpack('>I',raw[offset:offset+4])[0];end=offset+length+12
            if end>len(raw):return False
            name=raw[offset+4:offset+8]
            if name not in (b'IHDR',b'IDAT',b'IEND'):return False
            if name==b'IHDR':
                if length!=13:return False
                width,height,depth,color,compression,filters,interlace=struct.unpack('>IIBBBBB',raw[offset+8:end-4])
                if (depth!=8 or color not in (2,6) or compression!=0 or filters!=0
                        or interlace not in (0,1)):return False
            names.append(name);offset=end
        return (len(names)>=3 and names[0]==b'IHDR' and names[-1]==b'IEND'
            and names.count(b'IHDR')==1 and names.count(b'IEND')==1 and b'IDAT' in names)

    def _encode(self,raw):
        if not self._plain_png(raw):return None
        with Image.open(io.BytesIO(raw)) as image:
            if (image.format!='PNG' or image.mode not in ('RGB','RGBA') or image.info
                    or getattr(image,'n_frames',1)!=1 or image.width*image.height>self.max_pixels
                    or image.width<=0 or image.height<=0 or len(image.getexif())):return None
            image.load();buffer=io.BytesIO()
            image.save(buffer,format='WEBP',lossless=True,exact=True,method=0)
            encoded=buffer.getvalue()
            if len(encoded)>=len(raw):return None
            with Image.open(io.BytesIO(encoded)) as decoded:
                decoded.load()
                if (decoded.format!='WEBP' or decoded.mode!=image.mode or decoded.size!=image.size
                        or decoded.tobytes()!=image.tobytes()
                        or decoded.convert('RGBA').tobytes()!=image.convert('RGBA').tobytes()):return None
        return 'data:image/webp;base64,'+base64.b64encode(encoded).decode('ascii')

    def convert(self,value):
        prefix='data:image/png;base64,'
        if not isinstance(value,str) or not value.startswith(prefix):return value
        text=value[len(prefix):]
        if len(text)>((self.max_source_bytes+2)//3)*4:return value
        try:raw=base64.b64decode(text,validate=True)
        except Exception:return value
        if len(raw)>self.max_source_bytes:return value
        key=(hashlib.sha256(raw).hexdigest(),PILLOW_VERSION,'WEBP',True,True,0)
        owner=False
        with self.lock:
            if key in self.cache:
                result,size=self.cache.pop(key);self.cache[key]=(result,size);return result
            future=self.pending.get(key)
            if future is None and len(self.pending)<self.max_entries:
                future=Future();self.pending[key]=future;owner=True
        if future is not None and not owner:
            result=future.result()
            return result if result is not None else value
        result=None
        try:
            result=self._encode(raw)
            if result is not None and len(result)>=len(value):result=None
            if result is not None:
                size=len(result)+len(key[0])+len(PILLOW_VERSION)+16
                with self.lock:
                    if size<=self.max_bytes and self.max_entries>0:
                        if key in self.cache:
                            _,previous=self.cache.pop(key);self.accounted_bytes-=previous
                        while self.cache and (self.accounted_bytes+size>self.max_bytes
                                or len(self.cache)>=self.max_entries):
                            _,(_,removed)=self.cache.popitem(last=False);self.accounted_bytes-=removed
                        self.cache[key]=(result,size);self.accounted_bytes+=size
        except Exception:
            result=None # Any unsupported decoder/encoder condition preserves the input.
        finally:
            if owner:
                with self.lock:self.pending.pop(key,None)
                future.set_result(result)
        return result if result is not None else value


_transport=QCImageTransport()


def qc_image_urls(images):
    return [_transport.convert(image) for image in images]
