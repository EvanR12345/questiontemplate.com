"""Cloud-side exact-resource expiry. Contains no credential or project source."""
import json,math,os,pathlib,re,time,urllib.request,urllib.error,urllib.parse

def all_pods(api):
    """Inspect all consumer pages; malformed/incomplete inventory fails closed."""
    rows=[];cursor=None;seen=set()
    for _ in range(100):
        route='/pods?includeClusterPods=true&limit=100'+('&cursor='+urllib.parse.quote(cursor,safe='') if cursor else '')
        value=api(route)
        if isinstance(value,list):return rows+value
        if not isinstance(value,dict):raise ValueError('Unreadable consumer inventory')
        page=value.get('pods',value.get('data',value.get('items')))
        if not isinstance(page,list):raise ValueError('Malformed consumer inventory')
        rows.extend(page);pagination=value.get('pagination')
        if not isinstance(pagination,dict) or type(pagination.get('hasNextPage')) is not bool or 'nextCursor' not in pagination:
            raise ValueError('Malformed consumer pagination')
        if pagination['hasNextPage'] is False:return rows
        cursor=pagination.get('nextCursor')
        if not isinstance(cursor,str) or not cursor or cursor in seen:raise ValueError('Incomplete consumer pagination')
        seen.add(cursor)
    raise ValueError('Consumer inventory exceeds bounded pagination')

class Guard:
    def __init__(self,config,api,clock=time.time,sleep=time.sleep,emit=lambda x:None):
        self.c=config;self.api=api;self.clock=clock;self.sleep=sleep;self.emit=emit
        if not re.fullmatch(r'[a-z0-9]{8,40}',config['podId']):raise ValueError('Missing provider-assigned own pod ID')
        if not config['name'].startswith('studio-'):raise ValueError('Unowned name')
        if not all(type(config[k]) in (float,int) and math.isfinite(config[k]) for k in ('created','deadline')):raise ValueError('Invalid deadline')
        if not 0<config['deadline']-config['created']<=4*3600:raise ValueError('Unbounded expiry')
        self.verified=False
    def own(self):
        p=self.api('/pods/'+self.c['podId'])
        if p is None:return None
        if p.get('id')!=self.c['podId'] or p.get('name')!=self.c['name']:raise ValueError('Own pod identity mismatch')
        # The live single-pod response may omit env. Resolve only this exact
        # identity from the complete inventory; never substitute another pod.
        if 'env' not in p:
            matches=[x for x in all_pods(self.api) if x.get('id')==self.c['podId']]
            if len(matches)!=1 or matches[0].get('name')!=self.c['name']:
                raise ValueError('Own pod inventory identity mismatch')
            p=p|{'env':matches[0].get('env',{})}
        env=p.get('env',{})
        if env.get('QT_CLOUD_NONCE')!=self.c['nonce']:raise ValueError('Cloud ownership nonce differs')
        return p
    def volume(self):
        spec=self.c.get('volume')
        if not spec:return True
        p=self.api('/network-volumes/'+spec['id'])
        if p is None:return True
        if any(p.get(k)!=spec[k] for k in ('id','name','size','dataCenter','type')):raise ValueError('Volume identity mismatch')
        pods=all_pods(self.api)
        attached=[x for x in pods if any(m.get('volumeId')==spec['id'] for m in (x.get('mounts') or {}).get('network',[])) and x.get('id')!=self.c['podId']]
        if attached:return False # Never stop/adopt an unregistered consumer.
        self.api('/network-volumes/'+spec['id'],method='DELETE')
        return self.api('/network-volumes/'+spec['id']) is None
    def arm(self):
        own=self.own()
        if own is None:raise ValueError('Own running allocation is absent')
        if self.c.get('volume') and any(m.get('volumeId')==self.c['volume']['id'] for m in (own.get('mounts') or {}).get('network',[])):
            raise ValueError('The volume guardian must run on an unmounted separate CPU')
        self.verified=True;self.emit(self.beat())
    def beat(self):
        return {'status':'ARMED','podId':self.c['podId'],'name':self.c['name'],'nonce':self.c['nonce'],
                'pid':os.getpid(),'deadline':self.c['deadline'],'cloudResident':True,'atEpoch':self.clock()}
    def tick(self):
        if not self.verified:self.arm()
        if self.clock()<self.c['deadline']:
            self.emit(self.beat());return False
        if self.own() is None:return True
        if not self.volume():return False
        self.emit({'status':'EXPIRING','podId':self.c['podId'],'volumeAbsent':True,'atEpoch':self.clock()})
        self.api('/pods/'+self.c['podId'],method='DELETE')
        return self.own() is None

def main():
    c=json.loads(os.environ['QT_CLOUD_CONFIG']);c['podId']=os.environ.get('RUNPOD_POD_ID','')
    key=os.environ['QT_CLOUD_API_KEY']
    def api(path,method='GET'):
        if not path.startswith(('/pods','/network-volumes')) or '..' in path:raise ValueError('Invalid resource route')
        req=urllib.request.Request('https://api.runpod.io/v2'+path,headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method=method)
        try:
            with urllib.request.urlopen(req,timeout=15) as response:
                raw=response.read();return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as error:
            if error.code==404:return None
            raise RuntimeError('Cloud expiry provider status '+str(error.code)) from None
    target=pathlib.Path('/tmp/questiontemplate-cloud-guard.json')
    def emit(value):
        temp=target.with_suffix('.writing');temp.write_text(json.dumps(value));temp.replace(target)
    guard=Guard(c,api,emit=emit)
    while True:
        try:
            if guard.tick():break
        except Exception as error:
            emit({'status':'RETRY_EXACT_OWNED_CLEANUP','errorType':type(error).__name__,'podId':c['podId'],'atEpoch':time.time()})
        time.sleep(2)

if __name__=='__main__':main()
