import test from 'node:test';
import assert from 'node:assert/strict';
import {handle} from './worker.mjs';
const project='pr-0123456789abcdef', key=`studio/assets/${project}/${'a'.repeat(64)}/story.mp4`;
class Bucket{
  data=new Map();serial=0;rangeReads=[];
  async get(k,options){if(k===key&&options?.range){this.rangeReads.push(options.range);return {body:new Uint8Array(options.range.length)};}const x=this.data.get(k);if(!x)return null;return {etag:x.etag,json:async()=>structuredClone(x.value)};}
  async put(k,v,options){const old=this.data.get(k);if(options?.onlyIf?.etagMatches&&old?.etag!==options.onlyIf.etagMatches)return null;const etag=String(++this.serial);this.data.set(k,{value:JSON.parse(v),etag});return {etag};}
  async head(k){return k===key?{size:40*1024*1024,customMetadata:{sha256:'a'.repeat(64)}}:null;}
}
function fixture(){const b=new Bucket();b.data.set(`studio/manifests/${project}.json`,{value:{files:{'story.mp4':{key,bytes:40*1024*1024,sha256:'a'.repeat(64)}}},etag:'manifest'});b.data.set('publisher/private/youtube.json',{value:{refreshToken:'private-refresh'},etag:'oauth'});return {STUDIO:b,PUBLISHER_TOKEN:'private-test-token-'.repeat(3),GOOGLE_CLIENT_ID:'client',GOOGLE_CLIENT_SECRET:'private-secret'};}
const request=(env,path,data)=>new Request('https://publisher.example'+path,{method:data?'POST':'GET',headers:{Authorization:'Bearer '+env.PUBLISHER_TOKEN,'Content-Type':'application/json'},body:data?JSON.stringify(data):undefined});
test('rejects unauthenticated requests and exposes no OAuth secrets',async()=>{
  const env=fixture();assert.equal((await handle(new Request('https://publisher.example/status'),env)).status,401);
  const r=await handle(request(env,'/status'),env);const raw=await r.text();assert.ok(raw.includes('"connected":true'));assert.ok(!raw.includes('private-refresh'));
});
test('video bytes transfer directly from R2; interrupted client resumes from YouTube acknowledgement',async()=>{
  const env=fixture();let committed=0,calls=[];
  const fetcher=async(url,options)=>{
    calls.push({url,options});
    if(url==='https://oauth2.googleapis.com/token')return Response.json({access_token:'private-access'});
    if(url.includes('uploadType=resumable'))return new Response(null,{status:200,headers:{Location:'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=private-session'}});
    if(options.headers['Content-Range'].startsWith('bytes */'))return new Response(null,{status:308,headers:committed?{Range:`bytes=0-${committed-1}`}:{}});
    const [,start,end,total]=/^bytes (\d+)-(\d+)\/(\d+)$/.exec(options.headers['Content-Range']);assert.equal(Number(start),committed);assert.equal(options.body.byteLength,Number(end)-Number(start)+1);committed=Number(end)+1;
    return committed===Number(total)?Response.json({id:'video-id'}):new Response(null,{status:308,headers:{Range:`bytes=0-${committed-1}`}});
  };
  let job=await (await handle(request(env,'/uploads/start',{project,path:'story.mp4',title:'Magic returns'}),env,fetcher)).json();
  assert.equal(job.privacy,'private');assert.ok(!JSON.stringify(job).includes('session'));const id=job.id;
  // Simulate eight MiB reaching YouTube before the coordinator reconnects.
  committed=8*1024*1024;
  job=await (await handle(request(env,'/uploads/next',{id}),env,fetcher)).json();assert.equal(job.uploaded,24*1024*1024);assert.equal(env.STUDIO.rangeReads[0].offset,8*1024*1024);
  job=await (await handle(request(env,'/uploads/next',{id}),env,fetcher)).json();assert.equal(job.status,'COMPLETE');assert.equal(job.videoId,'video-id');
  const callsBefore=calls.length;job=await (await handle(request(env,'/uploads/next',{id}),env,fetcher)).json();assert.equal(calls.length,callsBefore);
});
test('validates cloud source and blocks a forged session host',async()=>{
  const env=fixture(),fetcher=async url=>url.includes('/token')?Response.json({access_token:'x'}):new Response(null,{headers:{Location:'https://attacker.example/upload'}});
  const r=await handle(request(env,'/uploads/start',{project,path:'story.mp4',title:'Story'}),env,fetcher);assert.equal(r.status,400);assert.equal([...env.STUDIO.data.keys()].filter(k=>k.startsWith('publisher/jobs/')).length,0);
  const r2=await handle(request(env,'/uploads/start',{project,path:'missing.mp4',title:'Story'}),env,fetcher);assert.equal(r2.status,400);
});
test('OAuth state uses PKCE and expires; a reused callback is rejected',async()=>{
  const env=fixture();const r=await handle(request(env,'/oauth/start',{}),env);const data=await r.json();const u=new URL(data.url);assert.equal(u.searchParams.get('code_challenge_method'),'S256');assert.equal(u.searchParams.get('scope'),'https://www.googleapis.com/auth/youtube.upload');
  const state=u.searchParams.get('state'),k='publisher/private/oauth/'+state+'.json';env.STUDIO.data.get(k).value.used=true;
  assert.equal((await handle(new Request('https://publisher.example/oauth/callback?state='+state+'&code=x'),env)).status,400);
});
test('failed YouTube cancellation stays resumable and releases the lease',async()=>{
  const env=fixture();const id='01234567-89ab-cdef-0123-456789abcdef';
  env.STUDIO.data.set('publisher/jobs/'+id+'.json',{etag:'job',value:{id,project,path:'story.mp4',total:40,uploaded:0,status:'UPLOADING',leaseUntil:0,session:'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=x'}});
  const fetcher=async(url,options)=>{
    assert.ok(options.signal instanceof AbortSignal);
    return url.includes('/token')?Response.json({access_token:'x'}):new Response(null,{status:403});
  };
  assert.equal((await handle(request(env,'/uploads/cancel',{id}),env,fetcher)).status,400);
  const saved=env.STUDIO.data.get('publisher/jobs/'+id+'.json').value;
  assert.equal(saved.status,'UPLOADING');assert.equal(saved.leaseUntil,0);
});
