"""Private Google -> Vimeo pull uploads; laptop handles metadata only.

Submission intent is durable before POST. An uncertain reply is never replayed.
Vimeo processing can continue after Studio closes; refresh only reads its status.
"""
import datetime,hashlib,json,re,threading,time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request,urlopen,build_opener,HTTPRedirectHandler
from cost_control import atomic_replace


class NoVimeoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):raise ValueError('Vimeo redirected the request; no credential was forwarded.')


class VimeoRejected(ValueError):
    """A confirmed API rejection, distinct from an uncertain POST response."""


class VimeoPublisher:
    def __init__(self,store,archive,config,opener=None):
        self.store,self.archive,self.config,self.opener=store,archive,config,opener or build_opener(NoVimeoRedirect()).open
        self.root=store.root/'.vimeo-jobs';self.root.mkdir(exist_ok=True);self.lock=threading.RLock()

    def token(self):
        path=self.config.get('vimeoKeyFile')
        if not path:return ''
        try:return json.loads(Path(path).read_text(encoding='utf-8')).get('vimeoApiToken','')
        except (OSError,ValueError):return ''

    def request(self,path,body=None):
        if not re.fullmatch(r'/me(?:\?fields=[a-z_,.]+)?|/me/videos|/videos/[0-9]+(?:\?fields=[a-z_,.]+)?',path):
            raise ValueError('Invalid Vimeo operation.')
        token=self.token()
        if not token:raise ValueError('Connect a private Vimeo token with upload access first.')
        request=Request('https://api.vimeo.com'+path,headers={'Authorization':'Bearer '+token,
            'Accept':'application/vnd.vimeo.*+json;version=3.4','Content-Type':'application/json',
            'User-Agent':'QuestionTemplateStudio/1.0'},data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.opener(request,timeout=60) as response:return json.load(response)
        except HTTPError as error:
            kind=VimeoRejected if error.code in (400,401,403,404,405,413,415,422,429) else ValueError
            raise kind('Vimeo rejected the request. Check API upload access, token permissions and remaining account quota ('+str(error.code)+').') from None
        except OSError:raise ValueError('Vimeo connection interrupted. Check the saved job before submitting again.') from None

    def path(self,pid,identity):
        self.store.folder(pid)
        if not re.fullmatch(r'[a-f0-9]{32}',identity or ''):raise ValueError('Invalid Vimeo upload identity.')
        return self.root/(pid+'-'+identity+'.json')

    def cloud_key(self,pid,identity):return 'studio/publishing/vimeo/'+pid+'/'+identity+'.json'

    def load(self,pid,identity):
        path=self.path(pid,identity)
        objects=getattr(self.archive,'objects',None)
        if self.archive.enabled and callable(getattr(objects,'read',None)):
            try:
                raw,_=objects.read(self.cloud_key(pid,identity))
                value=json.loads(raw) if raw else None
            except Exception:raise ValueError('Vimeo upload receipt could not be read from private cloud storage. No new submission started.') from None
        else:value=json.loads(path.read_text()) if path.exists() else None
        if value and (value.get('project')!=pid or value.get('id')!=identity):raise ValueError('Vimeo receipt identity mismatch.')
        return value

    def save(self,path,value,create=False):
        objects=getattr(self.archive,'objects',None)
        if self.archive.enabled and callable(getattr(objects,'put_json',None)):
            key=self.cloud_key(value['project'],value['id'])
            try:
                _,etag=objects.read(key)
                objects.put_json(key,value,create=create,etag=None if create else etag)
            except Exception:raise ValueError('Vimeo upload receipt could not be saved to private cloud storage. Inspect its saved state before retrying.') from None
        temporary=path.with_suffix('.writing');temporary.write_text(json.dumps(value),encoding='utf-8');atomic_replace(temporary,path)

    def health(self,verify=False):
        configured=bool(self.token())
        result={'configured':configured,'connected':False,'cloudTransfer':True,'laptopVideoBytes':0,
            'sourceProvider':'Google Cloud Storage','privacy':'private','uploadRequiresExplicitAction':True}
        if configured and verify:
            account=self.request('/me?fields=uri,upload_quota')
            result.update(connected=True,quota=account.get('upload_quota',{}))
        return result

    @staticmethod
    def video_link(reply,uri):
        from urllib.parse import urlparse
        value=reply.get('link') or 'https://vimeo.com/'+uri.split('/')[-1]
        parsed=urlparse(value)
        if parsed.scheme!='https' or parsed.hostname!='vimeo.com' or parsed.username or parsed.password or parsed.query or parsed.fragment or not re.fullmatch(r'/[0-9]+(?:/[a-zA-Z0-9]+)?/?',parsed.path):
            raise ValueError('Vimeo returned an invalid viewing URL.')
        return value

    def start(self,pid,options):
        identity=options.get('id');path=self.path(pid,identity)
        selected=options.get('path');title=options.get('title','')
        if not isinstance(selected,str) or not selected.endswith('.mp4') or not isinstance(title,str) or not 1<=len(title.strip())<=128:
            raise ValueError('Choose a saved MP4 and a Vimeo title of 1–128 characters.')
        self.store.asset_local(pid,selected)
        privacy=options.get('privacy','private')
        if privacy not in ('private','unlisted'):raise ValueError('Choose private or unlisted Vimeo visibility.')
        signature=hashlib.sha256(json.dumps({'path':selected,'title':title.strip(),'privacy':privacy},sort_keys=True).encode()).hexdigest()
        with self.lock:
            saved=self.load(pid,identity)
            if saved:
                if saved['inputSignature']!=signature:raise ValueError('This upload identity belongs to a different video or title.')
                return saved
            if self.archive.provider!='Google Cloud Storage' or not self.archive.enabled:
                raise ValueError('Direct Vimeo upload currently requires private Google storage.')
            if not self.token():raise ValueError('Connect Vimeo before uploading.')
            record=self.archive.asset_record(pid,selected)
            if not record:raise ValueError('Save and verify this video in Google before uploading.')
            # Persist only object identity; a signed source capability never enters
            # project JSON, browser state, reports or upload receipts.
            saved={'id':identity,'project':pid,'path':selected,'title':title.strip(),
                'status':'SUBMITTING','inputSignature':signature,'bytes':record['bytes'],
                'sha256':record['sha256'],'created':time.time(),'privacy':privacy,'platform':'vimeo'}
            self.save(path,saved,create=True)
            submitted=False
            try:
                source=self.archive.objects.bucket.blob(record['key']).generate_signed_url(version='v4',
                    expiration=datetime.timedelta(hours=24),method='GET')
                submitted=True
                reply=self.request('/me/videos',{'name':title.strip(),'privacy':{'view':'nobody' if privacy=='private' else 'unlisted'},
                    'upload':{'approach':'pull','link':source,'size':record['bytes']}})
                uri=reply.get('uri','')
                if not re.fullmatch(r'/videos/[0-9]+',uri):raise ValueError('Vimeo did not confirm the new video identity.')
                saved.update(status='PROCESSING',videoUri=uri,videoURL=self.video_link(reply,uri))
            except BaseException as error:
                saved['status']='REJECTED' if isinstance(error,VimeoRejected) else 'SUBMISSION_UNKNOWN' if submitted else 'FAILED'
                self.save(path,saved);raise
            self.save(path,saved);return saved

    def jobs(self,pid):
        self.store.folder(pid)
        objects=getattr(self.archive,'objects',None)
        if self.archive.enabled and callable(getattr(objects,'keys',None)):
            try:
                rows=[]
                for key in objects.keys('studio/publishing/vimeo/'+pid+'/'):
                    identity=key.rsplit('/',1)[-1].removesuffix('.json')
                    if re.fullmatch(r'[a-f0-9]{32}',identity):
                        row=self.load(pid,identity)
                        if row:rows.append(row)
            except Exception:raise ValueError('Saved Vimeo uploads could not be listed. Existing uploads remain in private cloud storage.') from None
        else:rows=[json.loads(p.read_text(encoding='utf-8')) for p in self.root.glob(pid+'-*.json')]
        return sorted(rows,key=lambda j:j.get('created',0),reverse=True)

    def status(self,pid,identity):
        path=self.path(pid,identity)
        with self.lock:
            saved=self.load(pid,identity)
            if not saved:raise ValueError('This project has no matching Vimeo upload.')
            if saved['status'] in ('COMPLETE','SUBMITTING','SUBMISSION_UNKNOWN','FAILED','REJECTED'):return saved
            reply=self.request(saved['videoUri']+'?fields=uri,link,status,upload.status,transcode.status,privacy.view')
            if reply.get('uri')!=saved['videoUri']:raise ValueError('Vimeo returned a different video identity.')
            status=reply.get('status');transcode=reply.get('transcode',{}).get('status')
            if status=='available' and transcode=='complete':saved['status']='COMPLETE'
            elif status in ('quota_exceeded','uploading_error','transcoding_error') or transcode=='error':saved['status']='FAILED'
            saved['videoURL']=self.video_link(reply,saved['videoUri'])
            saved.update(uploadStatus=reply.get('upload',{}).get('status'),transcodeStatus=transcode,
                actualPrivacy=reply.get('privacy',{}).get('view'),updated=time.time())
            self.save(path,saved);return saved
