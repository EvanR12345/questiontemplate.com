"""Small control messages to the cloud publisher; never downloads video bytes."""
import json
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request,urlopen
from urllib.error import HTTPError

def save_upload_snapshot(store,pid,result,initial=False):
    """R2 owns live byte progress; avoid archiving the whole story per chunk."""
    if not initial and result['status'] not in ('COMPLETE','CANCELLED'):return
    project=store.load(pid)
    existing=next((job for job in project.get('publishing',[]) if job['id']==result['id']),None)
    if existing and all(existing.get(key)==value for key,value in result.items()):return
    def save(project):
        jobs=project.setdefault('publishing',[])
        current=next((job for job in jobs if job['id']==result['id']),None)
        if current is None:jobs.append(dict(result))
        else:current.update(result)
    store.mutate(pid,save)

class CloudPublisher:
    def __init__(self,secret_path):self.secret_path=Path(secret_path)
    def config(self):
        if not self.secret_path.exists():return None
        c=json.loads(self.secret_path.read_text(encoding='utf-8'))
        u=urlparse(c.get('url',''))
        if u.scheme!='https' or not u.hostname or not u.hostname.endswith('.workers.dev') or u.username or u.password or u.query or u.fragment:
            raise ValueError('Configure the verified HTTPS Cloudflare Worker address.')
        if len(c.get('token',''))<32:raise ValueError('The private cloud publisher credential is missing.')
        return {'url':c['url'].rstrip('/'),'token':c['token']}
    def request(self,path,body=None):
        c=self.config()
        if not c:raise ValueError('The cloud publisher is not connected yet. Configure its Worker and authorize YouTube first.')
        r=Request(c['url']+path,headers={'Authorization':'Bearer '+c['token'],'Content-Type':'application/json',
                  'Accept':'application/json','User-Agent':'QuestionTemplateStudio/1.0'},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urlopen(r,timeout=115) as response:return json.load(response)
        except HTTPError as error:
            try:message=json.load(error).get('error','Cloud publishing failed; saved assets are unchanged.')
            except Exception:message='Cloud publishing failed; saved assets are unchanged.'
            if 'https:' in message or len(message)>250:message='Cloud publishing failed; saved assets are unchanged.'
            raise ValueError(message) from None
        except OSError:raise ValueError('Cloud publisher connection interrupted. Saved uploads can be resumed.') from None
    def status(self):
        if not self.secret_path.exists():return {'configured':False,'connected':False,'cloudTransfer':True}
        return self.request('/status')
    def call(self,operation,body):
        routes={'connect':'/oauth/start','start':'/uploads/start','next':'/uploads/next','status':'/uploads/status','cancel':'/uploads/cancel'}
        if operation not in routes:raise ValueError('Unknown publishing operation.')
        result=self.request(routes[operation],body)
        if operation=='connect':
            u=urlparse(result.get('url',''))
            if u.scheme!='https' or u.hostname!='accounts.google.com':raise ValueError('Invalid YouTube sign-in destination.')
        return result
