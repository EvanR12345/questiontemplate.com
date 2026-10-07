// Private R2 -> YouTube resumable transfer. Video bytes never pass through Studio.
// Bind the same private R2 bucket as STUDIO. Store the three credentials as secrets.
const json=(body,status=200)=>Response.json(body,{status,headers:{'Cache-Control':'no-store'}});
const CHUNK=16*1024*1024;
const allowedUpload=url=>{const u=new URL(url);return u.protocol==='https:'&&u.hostname==='www.googleapis.com'&&u.pathname.startsWith('/upload/youtube/');};
const pid=id=>/^pr-[a-f0-9]{16}$/.test(id);
async function read(bucket,key){const o=await bucket.get(key);return o?{value:await o.json(),etag:o.etag}:null;}
async function write(bucket,key,value,etag){const r=await bucket.put(key,JSON.stringify(value),{httpMetadata:{contentType:'application/json'},...(etag?{onlyIf:{etagMatches:etag}}:{})});if(!r)throw Error('Upload is busy. Retry later.');return r;}
async function token(env,fetcher){
  const auth=await read(env.STUDIO,'publisher/private/youtube.json');
  if(!auth?.value.refreshToken)throw Error('Authorize your YouTube channel first.');
  const r=await fetcher('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,client_secret:env.GOOGLE_CLIENT_SECRET,refresh_token:auth.value.refreshToken,grant_type:'refresh_token'})});
  if(!r.ok)throw Error('YouTube authorization expired. Reconnect your channel.');
  return (await r.json()).access_token;
}
async function publicJob(job){return {id:job.id,project:job.project,path:job.path,title:job.title,privacy:job.actualPrivacy??job.privacy,actualPrivacy:job.actualPrivacy??null,total:job.total,uploaded:job.uploaded,status:job.status,videoId:job.videoId??null};}
async function acknowledge(response,job){
  if(response.status===308){
    const range=response.headers.get('Range');const end=range?Number(/^bytes=0-(\d+)$/.exec(range)?.[1]): -1;
    if(!Number.isInteger(end)||end>=job.total)throw Error('Invalid upload acknowledgement; source remains saved.');
    job.uploaded=end+1;job.status='UPLOADING';
  }else if(response.ok){const value=await response.json();if(!value.id)throw Error('YouTube returned no video ID.');job.videoId=value.id;job.status='COMPLETE';job.uploaded=job.total;if(['private','unlisted','public'].includes(value.status?.privacyStatus))job.actualPrivacy=value.status.privacyStatus;}
  else if(response.status===404||response.status===410)throw Error('YouTube upload session expired. Start a new upload explicitly.');
  else throw Error('YouTube transfer was interrupted. Retry to query the saved session.');
}
async function authorized(request,env){
  if(!env.PUBLISHER_TOKEN||env.PUBLISHER_TOKEN.length<32)return false;
  const given=request.headers.get('Authorization')?.replace(/^Bearer /,'')??'';
  const encoder=new TextEncoder();const a=encoder.encode(given),b=encoder.encode(env.PUBLISHER_TOKEN);
  if(a.length!==b.length)return false;
  if(crypto.subtle.timingSafeEqual)return crypto.subtle.timingSafeEqual(a,b);
  let difference=0;for(let i=0;i<a.length;i++)difference|=a[i]^b[i];return difference===0;
}
export async function handle(request,env,fetcher=fetch){
  const transport=fetcher;
  fetcher=(url,options={})=>transport(url,{...options,signal:AbortSignal.timeout(100000)});
  const url=new URL(request.url);
  try{
    if(url.pathname==='/oauth/callback'){
      const state=url.searchParams.get('state');
      if(!/^[a-f0-9]{64}$/.test(state??''))return json({error:'Invalid sign-in state.'},400);
      const key='publisher/private/oauth/'+state+'.json',saved=await read(env.STUDIO,key);
      if(!saved||saved.value.expires<Date.now()||saved.value.used)return json({error:'Sign-in expired. Start again from Studio.'},400);
      if(url.searchParams.get('error'))return json({error:'YouTube permission was not granted.'},403);
      saved.value.used=true;await write(env.STUDIO,key,saved.value,saved.etag);
      const code=url.searchParams.get('code');if(!code)return json({error:'Missing sign-in code.'},400);
      const r=await fetcher('https://oauth2.googleapis.com/token',{method:'POST',body:new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,client_secret:env.GOOGLE_CLIENT_SECRET,code,redirect_uri:url.origin+'/oauth/callback',grant_type:'authorization_code',code_verifier:saved.value.verifier})});
      if(!r.ok)return json({error:'YouTube sign-in did not complete. Reconnect in Studio.'},400);
      const credentials=await r.json();if(!credentials.refresh_token)return json({error:'No lasting YouTube authorization returned. Reconnect with consent.'},400);
      await write(env.STUDIO,'publisher/private/youtube.json',{refreshToken:credentials.refresh_token,connectedAt:Date.now()});
      return new Response('<!doctype html><meta charset="utf-8"><title>Studio connected</title><h1>YouTube connected</h1><p>Return to Studio and refresh publishing status. New uploads are private by default.</p>',{headers:{'Content-Type':'text/html;charset=utf-8','Cache-Control':'no-store','Content-Security-Policy':"default-src 'none'; frame-ancestors 'none'"}});
    }
    if(!await authorized(request,env))return json({error:'Private publisher authorization required.'},401);
    if(url.pathname==='/status')return json({configured:true,connected:!!(await read(env.STUDIO,'publisher/private/youtube.json'))?.value.refreshToken,chunkBytes:CHUNK,cloudTransfer:true});
    if(request.method!=='POST')return json({error:'Method not allowed.'},405);
    const body=await request.json();
    if(url.pathname==='/oauth/start'){
      if(!env.GOOGLE_CLIENT_ID||!env.GOOGLE_CLIENT_SECRET)throw Error('Configure the Google OAuth client before connecting your channel.');
      const random=()=>Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join('');
      const state=random(),verifier=random();const challenge=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(verifier));
      const b64=btoa(String.fromCharCode(...new Uint8Array(challenge))).replaceAll('+','-').replaceAll('/','_').replace(/=+$/,'');
      await write(env.STUDIO,'publisher/private/oauth/'+state+'.json',{verifier,expires:Date.now()+600000,used:false});
      const u=new URL('https://accounts.google.com/o/oauth2/v2/auth');u.search=new URLSearchParams({client_id:env.GOOGLE_CLIENT_ID,redirect_uri:url.origin+'/oauth/callback',response_type:'code',scope:'https://www.googleapis.com/auth/youtube.upload',access_type:'offline',prompt:'consent',state,code_challenge:b64,code_challenge_method:'S256'}).toString();
      return json({url:u.href});
    }
    if(url.pathname==='/uploads/start'){
      if(!pid(body.project)||typeof body.path!=='string')throw Error('Choose a saved project video.');
      const manifest=await read(env.STUDIO,'studio/manifests/'+body.project+'.json');const file=manifest?.value.files?.[body.path];
      if(!file||!body.path.endsWith('.mp4')||file.key!==`studio/assets/${body.project}/${file.sha256}/${body.path}`)throw Error('Upload the completed video to cloud storage first.');
      const renders=[manifest.value.project?.render,...(manifest.value.project?.chapters??[]).map(c=>c.render),...(manifest.value.project?.render?.parts??[])];
      const duration=renders.find(r=>r?.path===body.path)?.duration;
      if(!Number.isSafeInteger(file.bytes)||file.bytes<=0)throw Error('Cloud video size is invalid.');
      if(file.bytes>256000000000||duration>43200)throw Error('YouTube accepts up to 12 hours or 256 GB per video. Choose a saved video part instead.');
      const head=await env.STUDIO.head(file.key);if(!head||head.size!==file.bytes||head.customMetadata?.sha256!==file.sha256)throw Error('Cloud video verification failed.');
      const title=String(body.title??'').trim(),description=String(body.description??'');
      if(!title||title.length>100||description.length>5000)throw Error('Use a title of 1–100 characters and description up to 5000.');
      const privacy=body.privacy??'private';if(!['private','unlisted','public'].includes(privacy))throw Error('Choose a valid video visibility.');
      const jobId=crypto.randomUUID(),access=await token(env,fetcher);
      const r=await fetcher('https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status',{method:'POST',headers:{Authorization:'Bearer '+access,'Content-Type':'application/json','X-Upload-Content-Length':String(file.bytes),'X-Upload-Content-Type':'video/mp4'},body:JSON.stringify({snippet:{title,description,categoryId:'24'},status:{privacyStatus:privacy,selfDeclaredMadeForKids:body.madeForKids===true}})});
      const session=r.headers.get('Location');if(!r.ok||!session||!allowedUpload(session))throw Error('YouTube could not start the upload. Check channel eligibility and API quota.');
      const job={id:jobId,project:body.project,path:body.path,key:file.key,total:file.bytes,sha256:file.sha256,title,privacy,session,uploaded:0,status:'UPLOADING',leaseUntil:0};
      await write(env.STUDIO,'publisher/jobs/'+jobId+'.json',job);return json(await publicJob(job));
    }
    if(['/uploads/next','/uploads/status','/uploads/cancel'].includes(url.pathname)){
      if(!/^[a-f0-9-]{36}$/.test(body.id??''))throw Error('Invalid upload ID.');
      const key='publisher/jobs/'+body.id+'.json',saved=await read(env.STUDIO,key);if(!saved)throw Error('Upload not found.');
      const job=saved.value;
      if(url.pathname==='/uploads/status'||job.status==='COMPLETE'||job.status==='CANCELLED')return json(await publicJob(job));
      if(job.leaseUntil>Date.now())throw Error('Upload is busy. Retry later.');
      job.leaseUntil=Date.now()+120000;const locked=await write(env.STUDIO,key,job,saved.etag);
      try{
        const access=await token(env,fetcher);if(!allowedUpload(job.session))throw Error('Invalid saved upload session.');
        if(url.pathname==='/uploads/cancel'){
          const cancelled=await fetcher(job.session,{method:'DELETE',headers:{Authorization:'Bearer '+access}});
          if(!cancelled.ok&&![404,410].includes(cancelled.status))throw Error('YouTube could not cancel the upload. Retry cancellation.');
          job.status='CANCELLED';
        }else{
          // Always query the server. A timed-out previous request may already
          // have committed bytes; never trust a stale local progress counter.
          const status=await fetcher(job.session,{method:'PUT',headers:{Authorization:'Bearer '+access,'Content-Length':'0','Content-Range':`bytes */${job.total}`}});
          await acknowledge(status,job);
          if(job.status!=='COMPLETE'){
            const end=Math.min(job.total,job.uploaded+CHUNK)-1;
            const source=await env.STUDIO.get(job.key,{range:{offset:job.uploaded,length:end-job.uploaded+1}});
            if(!source)throw Error('Cloud source video is unavailable.');
            const result=await fetcher(job.session,{method:'PUT',headers:{Authorization:'Bearer '+access,'Content-Type':'video/mp4','Content-Length':String(end-job.uploaded+1),'Content-Range':`bytes ${job.uploaded}-${end}/${job.total}`},body:source.body});
            await acknowledge(result,job);
          }
        }
      }finally{job.leaseUntil=0;await write(env.STUDIO,key,job,locked.etag);}
      return json(await publicJob(job));
    }
    return json({error:'Unknown publisher operation.'},404);
  }catch(error){return json({error:error.message?.includes('https:')?'Cloud publishing failed. Saved files are unchanged.':error.message??'Cloud publishing failed.'},400);}
}
export default {fetch:(request,env)=>handle(request,env)};
