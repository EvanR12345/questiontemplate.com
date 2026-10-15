"""Private GPU infrastructure adapters. Discovery never allocates compute.

Vast: official /api/v0/bundles, /asks and /instances contracts.
Lium: public v1 feed and the documented /api pod/rent contracts.
Provider responses are reduced before use; credentials never enter reports.
"""
import datetime,ipaddress,json,math,re,shlex,time,urllib.error,urllib.request,uuid
from pathlib import Path


class GPUProviderError(ValueError):pass
class GPUCapacityUnavailable(GPUProviderError):pass
class GPURequestUncertain(GPUProviderError):pass
class GPUConnectNotReady(GPUProviderError):pass
class GPUEncoderUnavailable(GPUProviderError):pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*_args,**_kwargs):
        raise GPUProviderError('Provider redirected the request; no credential forwarded.')


def number(value,minimum=0):
    if type(value) not in (int,float) or not math.isfinite(value) or value<minimum:
        raise GPUProviderError('Provider returned an invalid price or resource count.')
    return value


def utc_stamp(value):
    parsed=datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def cuda_version(value):
    # 12.10 is newer than 12.8; comparing decimal floats gives the wrong result.
    match=re.fullmatch(r'(\d+)\.(\d+)(?:\.\d+)?',str(value))
    if not match:raise GPUProviderError('GPU driver compatibility was not reported.')
    return tuple(map(int,match.groups()))


def resource_id(provider,value):
    value=str(value)
    valid=(re.fullmatch(r'[1-9]\d{0,15}',value) if provider=='vast' else
           re.fullmatch(r'[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}',value))
    if not valid:raise GPUProviderError('Invalid provider resource identity.')
    return value


def label(value):
    if not re.fullmatch(r'studio-cloud-[a-f0-9]{32}',value or ''):
        raise GPUProviderError('Only an explicitly owned Studio allocation may be controlled.')
    return value


def direct_ssh(host,port,user='root'):
    try:ipaddress.ip_address(host)
    except (ValueError,TypeError):raise GPUProviderError('A verified direct SSH address is not ready.') from None
    if type(port) is not int or not 1<=port<=65535 or user!='root':
        raise GPUProviderError('Invalid direct SSH connection.')
    return {'host':host,'port':port,'username':user}


def lium_ssh(command):
    # Never execute a command supplied by a marketplace as shell text.
    tokens=shlex.split(command or '')
    if len(tokens)!=4 or tokens[0]!='ssh' or tokens[2]!='-p' or not tokens[1].startswith('root@'):
        raise GPUProviderError('Unrecognized Lium SSH connection format.')
    try:port=int(tokens[3])
    except ValueError:raise GPUProviderError('Invalid SSH port.') from None
    return direct_ssh(tokens[1][5:],port)


class ProviderHTTP:
    def __init__(self,provider,secret_file=None,transport=None):
        if provider not in ('vast','lium'):raise GPUProviderError('Unknown GPU provider.')
        self.provider=provider;self.secret_file=Path(secret_file) if secret_file else None
        self.base='https://console.vast.ai/api' if provider=='vast' else 'https://lium.io/api'
        self.transport=transport or urllib.request.build_opener(NoRedirect()).open

    def key(self):
        try:value=json.loads(self.secret_file.read_text(encoding='utf-8'))[self.provider+'ApiKey']
        except (OSError,KeyError,AttributeError,ValueError):raise GPUProviderError('Save the '+self.provider+' credential privately first.') from None
        if not isinstance(value,str) or not 20<=len(value)<=4096 or any(c.isspace() for c in value):
            raise GPUProviderError('Invalid saved GPU credential.')
        return value

    def call(self,path,body=None,method=None,authenticated=True,headers=None):
        if not path.startswith('/') or '..' in path or '\r' in path or '\n' in path:
            raise GPUProviderError('Invalid provider route.')
        verb=method or ('POST' if body is not None else 'GET')
        h={'Content-Type':'application/json','Accept':'application/json','User-Agent':'QuestionTemplateStudio/1.0'}
        if authenticated:
            key=self.key();h['Authorization' if self.provider=='vast' else 'X-API-Key']=('Bearer '+key if self.provider=='vast' else key)
        h.update(headers or {})
        request=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,headers=h,method=verb)
        mutating=verb not in ('GET','HEAD') and not (self.provider=='vast' and path=='/v0/bundles')
        try:
            for attempt in range(3):
                try:
                    with self.transport(request,timeout=30) as response:raw=response.read(8*1024*1024+1)
                    break
                except urllib.error.HTTPError as error:
                    if error.code!=429 or mutating or attempt==2:raise
                    try:delay=float(error.headers.get('Retry-After','5'))
                    except (TypeError,ValueError):delay=5
                    time.sleep(max(2,min(30,delay)))
            if len(raw)>8*1024*1024:raise GPUProviderError('Provider response exceeded its safe size.')
            result=json.loads(raw)
            if isinstance(result,dict) and result.get('success') is False:
                raise GPUProviderError('Provider refused the request; inspect its account dashboard.')
            return result
        except urllib.error.HTTPError as error:
            # Only explicit "offer gone" responses prove no rental was created.
            if mutating and ('/asks/' in path or '/rent' in path) and error.code==410:
                raise GPUCapacityUnavailable('The selected offer is no longer available.') from None
            if mutating and error.code>=500:
                raise GPURequestUncertain('Provider outcome is unknown. Reconcile the saved allocation before retrying.') from None
            raise GPUProviderError(self.provider+' rejected this request (HTTP '+str(error.code)+'). Check its account grant or balance.') from None
        except (OSError,TimeoutError,ValueError) as error:
            if isinstance(error,GPUProviderError):raise
            if mutating:raise GPURequestUncertain('Provider outcome is unknown. No fallback allocation is permitted until reconciled.') from None
            raise GPUProviderError(self.provider+' could not be reached; saved work is unchanged.') from None


def vast_offer(row,disk_gb,max_hourly):
    if (row.get('gpu_name')!='RTX 5090' or row.get('num_gpus')!=1 or
            row.get('verification')!='verified' or row.get('rentable') is not True or
            row.get('rented') is not False or number(row.get('reliability'))<.99 or
            cuda_version(row.get('cuda_max_good'))<(12,8) or number(row.get('disk_space'))<disk_gb or
            number(row.get('direct_port_count'))<1):return None
    rate=number(row.get('dph_total'),.001)
    if rate>max_hourly:return None
    return {'provider':'vast','id':resource_id('vast',row['id']),'gpu':'RTX 5090','gpuCount':1,
        'hourlyUSD':rate,'location':row.get('geolocation','Unknown'),'reliability':row['reliability'],
        'uploadMbps':number(row.get('inet_up',0)),'downloadMbps':number(row.get('inet_down',0)),
        'uploadUSDPerGB':number(row.get('inet_up_cost')),'downloadUSDPerGB':number(row.get('inet_down_cost')),
        'diskGB':disk_gb,'diskIncludedInQuote':True,'availability':'PROVIDER_REPORTED','checked':time.time(),
        'cpuRAMMiB':number(row.get('cpu_ram',0))}


def lium_offer(row,disk_gb,max_hourly):
    if (row.get('gpu_model')!='RTX 5090' or row.get('available_gpu_count',0)<1 or
            row.get('min_rentable_gpu_count')!=1 or row.get('tier')!='secure' or
            number(row.get('reliability_score'))<98.5 or cuda_version(row.get('cuda_driver'))<(12,8)):return None
    count=number(row.get('gpu_count'),1);free_disk=number(row.get('disk_free_gb'))/count
    if free_disk<disk_gb:return None
    rate=number(row.get('price_per_gpu_hour'),.001)
    if rate>max_hourly:return None
    return {'provider':'lium','id':resource_id('lium',row['id']),'gpu':'RTX 5090','gpuCount':1,
        'hourlyUSD':rate,'location':row.get('country','Unknown'),'reliability':row['reliability_score']/100,
        'uploadMbps':number(row.get('network_upload_mbps',0)),'downloadMbps':number(row.get('network_download_mbps',0)),
        'uploadUSDPerGB':None,'downloadUSDPerGB':None,'diskGB':disk_gb,'diskIncludedInQuote':True,
        'availability':'PROVIDER_REPORTED','checked':time.time()}


class VastProvider(ProviderHTTP):
    def __init__(self,secret_file=None,transport=None):super().__init__('vast',secret_file,transport)
    def offers(self,disk_gb=25,max_hourly=1.19):
        result=self.call('/v0/bundles',{'gpu_name':{'eq':'RTX 5090'},'num_gpus':{'eq':1},
            'verified':{'eq':True},'rentable':{'eq':True},'rented':{'eq':False},'type':'on-demand',
            'reliability':{'gte':.99},'cuda_max_good':{'gte':12.8},'disk_space':{'gte':disk_gb},
            'direct_port_count':{'gte':1},'allocated_storage':disk_gb,'limit':100,
            'order':[['dph_total','asc']]},authenticated=False)
        output=[]
        for row in result.get('offers',[]):
            try:offer=vast_offer(row,disk_gb,max_hourly)
            except (ValueError,KeyError,TypeError):continue
            if offer:output.append(offer)
        return sorted(output,key=lambda o:(o['hourlyUSD'],-o['uploadMbps']))
    def resources(self,owned_label):
        expected=label(owned_label);rows=[];token=None
        for _ in range(100):
            from urllib.parse import urlencode
            result=self.call('/v1/instances/?'+urlencode({'limit':25,**({'next_token':token} if token else {})}))
            rows.extend(r for r in result.get('instances',[]) if r.get('label')==expected)
            token=result.get('next_token')
            if not token:return rows
        raise GPURequestUncertain('Account resource pagination could not be completed.')
    def owned(self,id,owned_label):
        id=resource_id('vast',id)
        rows=[r for r in self.resources(owned_label) if str(r['id'])==id and r.get('label')==label(owned_label)]
        if len(rows)!=1:raise GPURequestUncertain('Owned Vast resource could not be verified.')
        row=rows[0]
        if row.get('gpu_name')!='RTX 5090' or row.get('num_gpus')!=1:
            raise GPUProviderError('Allocated GPU differs from the approved one.')
        return row
    def create(self,offer,owned_label,image):
        # The caller must durably record ownership/spending BEFORE this mutation.
        result=self.call('/v0/asks/'+resource_id('vast',offer['id']),{
            'image':image,'label':label(owned_label),'disk':offer['diskGB'],'runtype':'ssh_direct',
            'target_state':'running','env':{'NVIDIA_DRIVER_CAPABILITIES':'all'}},method='PUT')
        return resource_id('vast',result['new_contract'])
    def stop(self,id,owned_label):
        row=self.owned(id,owned_label)
        # cur_state is the rental contract state. Docker's actual_status can
        # continue to report "loading" after compute is stopped; the console
        # then shows Inactive and disk-only pricing. Require both contract and
        # intended state, rather than treating a requested stop alone as proof.
        def stopped(r):return r.get('actual_status')=='stopped' or (r.get('cur_state')=='stopped' and r.get('intended_status')=='stopped')
        if stopped(row):return row
        self.call('/v0/instances/'+resource_id('vast',id),{'state':'stopped'},method='PUT')
        for _ in range(10):
            row=self.owned(id,owned_label)
            if stopped(row):return row
            time.sleep(1)
        raise GPURequestUncertain('Vast has not confirmed the worker stopped.')
    def start(self,id,owned_label):
        self.owned(id,owned_label)
        self.call('/v0/instances/'+resource_id('vast',id),{'state':'running'},method='PUT')
    def destroy(self,id,owned_label):
        self.owned(id,owned_label)
        self.call('/v0/instances/'+resource_id('vast',id),method='DELETE')
        if any(str(r.get('id'))==str(id) for r in self.resources(owned_label)):
            raise GPURequestUncertain('Vast deletion has not been confirmed; do not repeat blindly.')
        return {'id':str(id),'status':'REMOVED'}
    def attach_ssh(self,id,owned_label,public_key):
        self.owned(id,owned_label)
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',public_key):
            raise GPUProviderError('Use the dedicated public SSH key; no private key leaves the laptop.')
        result=self.call('/v0/instances/'+resource_id('vast',id)+'/ssh',{'ssh_key':public_key})
        if result.get('success') is not True:raise GPUProviderError('Vast did not confirm the owned worker SSH grant.')
    def connection(self,row):
        ports=row.get('ports',{}).get('22/tcp',[])
        if not ports:raise GPUProviderError('Direct SSH is not ready yet.')
        return direct_ssh(row.get('public_ipaddr'),int(ports[0]['HostPort']))


class LiumProvider(ProviderHTTP):
    def __init__(self,secret_file=None,transport=None):super().__init__('lium',secret_file,transport)
    def offers(self,disk_gb=25,max_hourly=1.19):
        result=self.call('/public/v1/nodes',authenticated=False)
        generated=datetime.datetime.fromisoformat(result['generated_at'].replace('Z','+00:00')).timestamp()
        if not -60<=time.time()-generated<=360:raise GPUProviderError('Lium inventory is stale; no worker selected.')
        output=[]
        for row in result.get('nodes',[]):
            try:offer=lium_offer(row,disk_gb,max_hourly)
            except (ValueError,KeyError,TypeError):continue
            if offer:output.append(offer)
        return sorted(output,key=lambda o:(o['hourlyUSD'],-o['uploadMbps']))
    def resources(self,owned_label):
        expected=label(owned_label)
        return [r for r in self.call('/pods') if r.get('pod_name')==expected]
    def owned(self,id,owned_label):
        result=self.call('/pods/'+resource_id('lium',id))
        if result.get('pod_name')!=label(owned_label):raise GPUProviderError('Unrelated Lium resource will never be controlled.')
        if result.get('gpu_name') not in ('RTX 5090','NVIDIA GeForce RTX 5090') or str(result.get('gpu_count'))!='1':
            raise GPUProviderError('Allocated GPU differs from the approved one.')
        return result
    def create(self,offer,owned_label,template_id,public_key,identity):
        uuid.UUID(identity);uuid.UUID(template_id)
        if not re.fullmatch(r'ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]+)?',public_key):raise GPUProviderError('Use the existing dedicated public SSH key.')
        result=self.call('/executors/'+resource_id('lium',offer['id'])+'/rent',{
            'pod_name':label(owned_label),'template_id':template_id,'gpu_count':1,'user_public_key':public_key,
            'initial_port_count':1,'enable_jupyter':False,'skip_add_agent_ssh_key':True,'termination_hours':1},
            headers={'Idempotency-Key':identity})
        return resource_id('lium',result['pod_id'])
    def schedule_stop(self,id,owned_label,deadline):
        self.owned(id,owned_label)
        when=datetime.datetime.fromtimestamp(deadline,datetime.timezone.utc).isoformat().replace('+00:00','Z')
        self.call('/pods/'+resource_id('lium',id)+'/schedule-removal',{'removal_scheduled_at':when})
        row=self.owned(id,owned_label)
        confirmed=utc_stamp(row['removal_scheduled_at'])
        if abs(confirmed-deadline)>2:raise GPURequestUncertain('The provider shutdown deadline was not confirmed.')
    def stop(self,id,owned_label):
        id=resource_id('lium',id)
        # DELETE can succeed while its reply is lost, or the provider's own
        # deadline can release the pod first. A complete owned-name read is
        # authoritative for absence; do not repeat a mutation blindly.
        if not any(str(r['id'])==id for r in self.resources(owned_label)):
            return {'id':id,'status':'RELEASED'}
        self.owned(id,owned_label)
        self.call('/pods/'+id,method='DELETE')
        if any(str(r['id'])==str(id) for r in self.resources(owned_label)):
            raise GPURequestUncertain('Lium has not confirmed this rental was released.')
        return {'id':id,'status':'RELEASED'}
    def connection(self,row):return lium_ssh(row.get('ssh_connect_cmd'))


_inventory_cache={}


def provider_status(config,refresh=False):
    """Private helper UI only. This is inventory, not startup verification."""
    order=config.get('gpuProviderOrder',['runpod','vast','lium'])
    secret=config.get('gpuProviderKeyFile')
    disk=config.get('gpuWorkingDiskGB',25);maximum=config.get('gpuMaxHourlyUSD',1.19)
    rows=[]
    for name,kind in [('vast',VastProvider),('lium',LiumProvider)]:
        client=kind(secret)
        try:client.key();configured=True
        except GPUProviderError:configured=False
        row={'provider':name,'credentialSaved':configured,'renderVerified':name in config.get('gpuRenderVerifiedProviders',[]),
             'imageVerified':name in config.get('gpuImageVerifiedProviders',[])}
        proof=config.get('gpuImageVerification',{}).get(name,{})
        if proof:row['imageBenchmark']={k:proof[k] for k in ('images','model','resolution','warmServerMedianSeconds','warmClientMedianSeconds','completeCloudSavedImagesPerMinute','scope') if k in proof}
        cache_key=(name,disk,maximum,config.get('gpuMinDownloadMbps',0),config.get('gpuMinReliability',0))
        cached=_inventory_cache.get(cache_key)
        if refresh and (not cached or time.time()-cached['checked']>=60):
            try:
                offers=client.offers(disk,maximum)
                offers=[o for o in offers if o.get('downloadMbps',0)>=config.get('gpuMinDownloadMbps',0)
                        and o.get('reliability',0)>=config.get('gpuMinReliability',0)]
                if name=='vast':offers=[o for o in offers if o['downloadUSDPerGB']<=config.get('gpuMaxDownloadUSDPerGB',.001)
                    and o['uploadUSDPerGB']<=config.get('gpuMaxUploadUSDPerGB',.001)]
                cached={'checked':time.time(),'offers':offers,'error':''}
            except GPUProviderError as error:cached={'checked':time.time(),'offers':[],'error':str(error)}
            _inventory_cache[cache_key]=cached
        if cached:row.update(copy_inventory(cached))
        rows.append(row)
    from studio_runpod import configured as runpod_configured,RunpodClient
    runpod_ready=runpod_configured(config)
    row={'provider':'runpod','credentialSaved':runpod_ready,'imageVerified':runpod_ready,
         'renderVerified':'runpod' in config.get('gpuRenderVerifiedProviders',[])}
    cached=_inventory_cache.get(('runpod',))
    if refresh and (not cached or time.time()-cached['checked']>=60):
        try:
            gpu=next(g for g in RunpodClient(config).gpu_catalog() if g.get('gpuId')=='NVIDIA GeForce RTX 5090')
            cached={'checked':time.time(),'availability':gpu.get('available'),'regionalStock':gpu.get('dataCenterAvailability',[]),
                    'hourlyUSD':gpu.get('securePricePerHr'),'error':''}
        except Exception:cached={'checked':time.time(),'error':'Runpod inventory could not be confirmed. No rental started.'}
        _inventory_cache[('runpod',)]=cached
    if cached:row.update(copy_inventory(cached))
    rows.insert(0,row)
    return {'providerOrder':order,'maxHourlyUSD':maximum,'workingDiskGB':disk,'providers':rows,
            'automaticRenderingReady':any(r['credentialSaved'] and r['renderVerified'] for r in rows),
            'automaticImagesReady':any(r['credentialSaved'] and r['imageVerified'] and r['provider'] in order for r in rows),
            'availabilityGuarantee':False}


def copy_inventory(value):
    # Avoid exposing mutable cached rows or accidentally attaching raw accounts.
    return json.loads(json.dumps(value))
