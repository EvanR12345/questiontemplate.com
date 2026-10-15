"""Lazy, checksum-bound cloud inputs. Never fetch the entire story eagerly."""
import hashlib,math,shutil,threading,time,urllib.request
from pathlib import Path
from studio_cloud_render_plan import timeout_for_bytes


class CloudInputCache:
    def __init__(self,records,resolve_path,capability,gate,deadline,url_validator,opener=urllib.request.urlopen):
        self.records={};self.resolve_path=resolve_path;self.capability=capability
        self.gate=gate;self.deadline=deadline;self.url_validator=url_validator;self.opener=opener
        self.lock=threading.RLock();self.seconds=0.;self.bytes=0;self.files=0;self.verified={}
        for record in records:
            path=record.get('path');size=record.get('bytes');sha=record.get('sha256')
            if (not isinstance(path,str) or path in self.records or type(size) is not int or not 0<size<=256*2**30
                    or not isinstance(sha,str) or len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha)):
                raise ValueError('Invalid or duplicated source identity.')
            resolve_path(path) # Path traversal/extension validation precedes any IO.
            self.records[path]=record

    def ensure(self,path):
        target=self.resolve_path(path)
        if path not in self.records:return target
        with self.lock:
            record=self.records[path]
            if target.is_file():
                stat=target.stat();stamp=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
                if self.verified.get(path)!=stamp:
                    checksum=hashlib.sha256()
                    with target.open('rb') as source:
                        for block in iter(lambda:source.read(1024*1024),b''):checksum.update(block)
                    if stat.st_size!=record['bytes'] or checksum.hexdigest()!=record['sha256']:
                        raise ValueError('Existing cloud working input differs from its saved identity.')
                    self.verified[path]=stamp
                return target
            began=time.monotonic()
            self.gate('Loading the current chapter’s saved inputs')
            # A fresh signed capability prevents long jobs using expired startup URLs.
            reply=self.capability(path)
            if any(reply.get(k)!=record.get(k) for k in ('path','bytes','sha256')):
                raise ValueError('Cloud source capability does not match the saved input.')
            target.parent.mkdir(parents=True,exist_ok=True)
            if shutil.disk_usage(target.parent).free<record['bytes']+256*2**20:
                raise RuntimeError('Cloud working disk is full; saved Google originals remain intact.')
            temporary=target.with_name(target.name+'.cloud-partial')
            checksum=hashlib.sha256();count=0
            try:
                with self.opener(self.url_validator(reply['url']),timeout=timeout_for_bytes(record['bytes'],self.deadline-time.time())) as response,temporary.open('xb') as output:
                    for block in iter(lambda:response.read(1024*1024),b''):
                        self.gate('Loading the current chapter’s saved inputs');count+=len(block)
                        if count>record['bytes']:raise ValueError('Cloud source exceeded its saved size.')
                        checksum.update(block);output.write(block)
                if count!=record['bytes'] or checksum.hexdigest()!=record['sha256']:
                    raise ValueError('Cloud source checksum mismatch.')
                temporary.replace(target)
                stat=target.stat();self.verified[path]=(stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
            finally:
                if temporary.is_file():temporary.unlink()
            self.seconds+=time.monotonic()-began;self.bytes+=count;self.files+=1
            return target

    def stats(self):return {'seconds':self.seconds,'bytes':self.bytes,'files':self.files,'totalSources':len(self.records)}
