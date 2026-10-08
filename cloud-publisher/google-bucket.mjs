// Private GCS equivalent of the small R2 interface used by the publisher.
// No public bucket URLs or credentials are returned to the browser.
import {federatedAccessToken} from './federation.mjs';
const tokens=new Map();
const b64=bytes=>btoa(String.fromCharCode(...bytes)).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
const encode=value=>b64(new TextEncoder().encode(JSON.stringify(value)));

export class GoogleBucket{
  constructor(bucket,credentials,fetcher=fetch,accessToken=null){
    if(!/^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$/.test(bucket??''))throw Error('Configure the private Google storage bucket.');
    if(!accessToken&&(credentials?.type!=='service_account'||!credentials.client_email||!credentials.private_key))throw Error('Configure the private Google storage credential.');
    this.bucket=bucket;this.credentials=credentials;this.fetcher=fetcher;this.tokenProvider=accessToken;
  }
  async accessToken(){
    if(this.tokenProvider)return this.tokenProvider();
    const c=this.credentials,cacheKey=this.bucket+'|'+c.client_email+'|'+c.private_key_id;
    const old=tokens.get(cacheKey);if(old?.expires>Date.now()+60000)return old.value;
    const time=Math.floor(Date.now()/1000),header=encode({alg:'RS256',typ:'JWT',kid:c.private_key_id});
    const payload=encode({iss:c.client_email,scope:'https://www.googleapis.com/auth/devstorage.read_write',aud:'https://oauth2.googleapis.com/token',iat:time,exp:time+3600});
    const pem=c.private_key.replace(/-----BEGIN PRIVATE KEY-----|-----END PRIVATE KEY-----|\s/g,'');
    const bytes=Uint8Array.from(atob(pem),char=>char.charCodeAt(0));
    const key=await crypto.subtle.importKey('pkcs8',bytes,{name:'RSASSA-PKCS1-v1_5',hash:'SHA-256'},false,['sign']);
    const assertion=header+'.'+payload+'.'+b64(new Uint8Array(await crypto.subtle.sign('RSASSA-PKCS1-v1_5',key,new TextEncoder().encode(header+'.'+payload))));
    const r=await this.fetcher('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({grant_type:'urn:ietf:params:oauth:grant-type:jwt-bearer',assertion})});
    if(!r.ok)throw Error('Google storage authorization failed. Check the private bucket credential.');
    const value=await r.json();if(!value.access_token)throw Error('Google storage authorization returned no access token.');
    if(tokens.size>=4)tokens.delete(tokens.keys().next().value);
    tokens.set(cacheKey,{value:value.access_token,expires:Date.now()+Math.min(Number(value.expires_in)||3600,3600)*1000});
    return value.access_token;
  }
  objectURL(key){return 'https://storage.googleapis.com/storage/v1/b/'+encodeURIComponent(this.bucket)+'/o/'+encodeURIComponent(key);}
  async request(url,options={}){
    return this.fetcher(url,{...options,headers:{...options.headers,Authorization:'Bearer '+await this.accessToken()},redirect:'error'});
  }
  async metadata(key){
    const r=await this.request(this.objectURL(key));if(r.status===404)return null;
    if(!r.ok)throw Error('Google storage read failed; saved files are unchanged.');
    const value=await r.json();
    if(!/^\d+$/.test(String(value.generation))||!Number.isSafeInteger(Number(value.size)))throw Error('Invalid Google storage metadata.');
    return value;
  }
  async head(key){const m=await this.metadata(key);return m?{size:Number(m.size),etag:String(m.generation),customMetadata:m.metadata??{}}:null;}
  async get(key,options={}){
    const m=await this.metadata(key);if(!m)return null;
    const url=this.objectURL(key)+'?alt=media&generation='+encodeURIComponent(m.generation),headers={};
    if(options.range){
      const {offset,length}=options.range;
      if(!Number.isSafeInteger(offset)||offset<0||!Number.isSafeInteger(length)||length<=0||offset+length>Number(m.size))throw Error('Invalid cloud video range.');
      headers.Range=`bytes=${offset}-${offset+length-1}`;
    }
    const r=await this.request(url,{headers});
    if(!r.ok)throw Error('Google storage content read failed; retry safely.');
    if(options.range){
      const {offset,length}=options.range;
      if(r.status!==206||r.headers.get('Content-Range')!==`bytes ${offset}-${offset+length-1}/${m.size}`){await r.body?.cancel();throw Error('Google storage returned an unexpected video range.');}
    }
    return {etag:String(m.generation),body:r.body,json:()=>r.json()};
  }
  async put(key,body,options={}){
    const u=new URL('https://storage.googleapis.com/upload/storage/v1/b/'+encodeURIComponent(this.bucket)+'/o');
    u.searchParams.set('uploadType','media');u.searchParams.set('name',key);
    // Unconditional writes are used only for explicit OAuth connection state.
    // Upload leases supply the generation from read(); no lost-update overwrite.
    if(options.onlyIf?.etagMatches){
      if(!/^\d+$/.test(String(options.onlyIf.etagMatches)))throw Error('Invalid Google storage revision.');
      u.searchParams.set('ifGenerationMatch',String(options.onlyIf.etagMatches));
    }else if(options.onlyIf?.etagDoesNotMatch==='*'){
      u.searchParams.set('ifGenerationMatch','0');
    }
    const r=await this.request(u.href,{method:'POST',headers:{'Content-Type':options.httpMetadata?.contentType??'application/json'},body});
    if(r.status===412)return null;
    if(!r.ok)throw Error('Google storage update failed; retry safely.');
    const value=await r.json();if(!/^\d+$/.test(String(value.generation)))throw Error('Invalid Google storage write acknowledgement.');
    return {etag:String(value.generation)};
  }
}

export function publisherBucket(env,fetcher){
  const provider=env.STUDIO_STORAGE_PROVIDER??'r2';
  if(provider==='r2')return env.STUDIO;
  if(provider!=='gcs')throw Error('Unsupported publisher storage provider.');
  if(env.STUDIO_GCS_AUTH_MODE==='federated')return new GoogleBucket(env.STUDIO_GCS_BUCKET,null,fetcher,()=>federatedAccessToken(env,fetcher));
  if(env.STUDIO_GCS_AUTH_MODE&&env.STUDIO_GCS_AUTH_MODE!=='service-account')throw Error('Unsupported private Google authentication mode.');
  let credentials;try{credentials=JSON.parse(env.STUDIO_GCS_SERVICE_ACCOUNT??'');}catch{throw Error('Configure the private Google storage credential.');}
  return new GoogleBucket(env.STUDIO_GCS_BUCKET,credentials,fetcher);
}
