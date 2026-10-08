import test from 'node:test';
import assert from 'node:assert/strict';
import {GoogleBucket,publisherBucket} from './google-bucket.mjs';
import {handle} from './worker.mjs';

test('private Google reads pin JSON and video ranges to the metadata generation',async()=>{
  const calls=[];
  const fetcher=async(url,options)=>{
    calls.push({url,options});assert.equal(options.headers.Authorization,'Bearer test-access');assert.equal(options.redirect,'error');
    if(!url.includes('alt=media'))return Response.json({generation:'42',size:'100',metadata:{sha256:'a'.repeat(64)}});
    if(options.headers.Range)return new Response(new Uint8Array(10),{status:206,headers:{'Content-Range':'bytes 20-29/100'}});
    return Response.json({revision:3});
  };
  const bucket=new GoogleBucket('private-test',null,fetcher,async()=>'test-access');
  const object=await bucket.get('folder/project.json');assert.equal(object.etag,'42');assert.equal((await object.json()).revision,3);
  assert.equal((await bucket.head('folder/video.mp4')).size,100);
  const stream=await bucket.get('folder/video.mp4',{range:{offset:20,length:10}});assert.ok(stream.body instanceof ReadableStream);
  assert.ok(calls.some(c=>c.url.includes('generation=42')&&c.options.headers.Range==='bytes=20-29'));
  assert.ok(calls.every(c=>c.url.startsWith('https://storage.googleapis.com/storage/v1/b/private-test/o/folder%2F')));
});

test('Google upload-state leases use generation conditions and report collisions without overwriting',async()=>{
  const bucket=new GoogleBucket('private-test',null,async(url,options)=>{
    const u=new URL(url);assert.equal(u.searchParams.get('ifGenerationMatch'),'17');assert.equal(u.searchParams.get('uploadType'),'media');
    assert.equal(JSON.parse(options.body).uploaded,123);return new Response(null,{status:412});
  },async()=>'test');
  assert.equal(await bucket.put('publisher/jobs/job.json','{"uploaded":123}',{onlyIf:{etagMatches:'17'}}),null);
});
test('new cloud job and project-lock records use create-only Google preconditions',async()=>{
  const bucket=new GoogleBucket('private-test',null,async(url)=>{
    assert.equal(new URL(url).searchParams.get('ifGenerationMatch'),'0');
    return new Response(null,{status:412});
  },async()=>'test');
  assert.equal(await bucket.put('studio/render-locks/project.json','{}',{onlyIf:{etagDoesNotMatch:'*'}}),null);
});

test('unexpected full-body replies cannot become an upload chunk',async()=>{
  const bucket=new GoogleBucket('private-test',null,async url=>url.includes('alt=media')?new Response(new Uint8Array(100)):Response.json({generation:'1',size:'100'}),async()=>'test');
  await assert.rejects(bucket.get('video.mp4',{range:{offset:0,length:10}}),/unexpected video range/);
});

test('GCS configuration never silently falls back to old R2 data',()=>{
  assert.throws(()=>publisherBucket({STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_BUCKET:'private-test',STUDIO:{}},fetch),/private Google storage credential/);
  assert.throws(()=>publisherBucket({STUDIO_STORAGE_PROVIDER:'invalid',STUDIO:{}},fetch),/Unsupported/);
});

test('unauthenticated requests do not contact Google or parse private configuration',async()=>{
  let calls=0;const response=await handle(new Request('https://publisher.example/status'),{STUDIO_STORAGE_PROVIDER:'gcs',PUBLISHER_TOKEN:'x'.repeat(64)},async()=>{calls++;throw Error('Unexpected network');});
  assert.equal(response.status,401);assert.equal(calls,0);
});

test('service-account exchange signs the correct scoped JWT and status retains no credential',async()=>{
  const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
  const exported=Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64');
  const account={type:'service_account',client_email:'studio-test@example.iam.gserviceaccount.com',private_key_id:'test-key',private_key:`-----BEGIN PRIVATE KEY-----\n${exported}\n-----END PRIVATE KEY-----`};
  let jwtCount=0;
  const fetcher=async(url,options)=>{
    if(url==='https://oauth2.googleapis.com/token'){
      jwtCount++;const assertion=options.body.get('assertion');const [header,payload,signature]=assertion.split('.');
      assert.equal(JSON.parse(Buffer.from(payload,'base64url')).scope,'https://www.googleapis.com/auth/devstorage.read_write');
      assert.ok(await crypto.subtle.verify('RSASSA-PKCS1-v1_5',pair.publicKey,Buffer.from(signature,'base64url'),new TextEncoder().encode(header+'.'+payload)));
      return Response.json({access_token:'private-storage-access',expires_in:3600});
    }
    return new Response(null,{status:404});
  };
  const env={STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_BUCKET:'private-test',STUDIO_GCS_SERVICE_ACCOUNT:JSON.stringify(account),PUBLISHER_TOKEN:'x'.repeat(64)};
  const makeRequest=()=>new Request('https://publisher.example/status',{headers:{Authorization:'Bearer '+env.PUBLISHER_TOKEN}});
  const raw=await (await handle(makeRequest(),env,fetcher)).text();assert.equal(JSON.parse(raw).storageProvider,'gcs');assert.ok(!raw.includes('private-storage-access'));assert.ok(!raw.includes(exported));
  await handle(makeRequest(),env,fetcher);assert.equal(jwtCount,1);
});

test('Google-only publisher resumes acknowledged bytes and streams the saved video with conditional leases',async()=>{
  const pair=await crypto.subtle.generateKey({name:'RSASSA-PKCS1-v1_5',modulusLength:2048,publicExponent:new Uint8Array([1,0,1]),hash:'SHA-256'},true,['sign','verify']);
  const account={type:'service_account',client_email:'stream-test@example.iam.gserviceaccount.com',private_key_id:'stream-test-key',private_key:`-----BEGIN PRIVATE KEY-----\n${Buffer.from(await crypto.subtle.exportKey('pkcs8',pair.privateKey)).toString('base64')}\n-----END PRIVATE KEY-----`};
  const project='pr-0123456789abcdef',sha='a'.repeat(64),asset=`studio/assets/${project}/${sha}/story.mp4`,total=2*1024*1024;
  const objects=new Map();let generation=0,committed=128*1024,rangeCount=0,leaseWrites=0;
  const save=(name,value)=>objects.set(name,{body:JSON.stringify(value),generation:String(++generation)});
  save(`studio/manifests/${project}.json`,{files:{'story.mp4':{key:asset,sha256:sha,bytes:total}}});
  save('publisher/private/youtube.json',{refreshToken:'fake-refresh'});
  const fetcher=async(url,options={})=>{
    const u=new URL(url);
    if(url==='https://oauth2.googleapis.com/token')return Response.json({access_token:options.body.has('assertion')?'fake-storage-access':'fake-youtube-access',expires_in:3600});
    if(u.hostname==='storage.googleapis.com'){
      assert.equal(options.headers.Authorization,'Bearer fake-storage-access');
      if(options.method==='POST'){
        const name=u.searchParams.get('name'),old=objects.get(name),condition=u.searchParams.get('ifGenerationMatch');
        if(condition){leaseWrites++;if(condition!==old?.generation)return new Response(null,{status:412});}
        save(name,JSON.parse(options.body));return Response.json({generation:objects.get(name).generation});
      }
      const name=decodeURIComponent(u.pathname.split('/o/')[1]);
      if(name===asset){
        if(!u.searchParams.has('alt'))return Response.json({generation:'100',size:String(total),metadata:{sha256:sha}});
        assert.equal(u.searchParams.get('generation'),'100');
        const [,start,end]=/^bytes=(\d+)-(\d+)$/.exec(options.headers.Range);rangeCount++;
        assert.equal(Number(start),committed);
        return new Response(new Uint8Array(Number(end)-Number(start)+1),{status:206,headers:{'Content-Range':`bytes ${start}-${end}/${total}`}});
      }
      const object=objects.get(name);if(!object)return new Response(null,{status:404});
      if(u.searchParams.has('alt')){assert.equal(u.searchParams.get('generation'),object.generation);return new Response(object.body);}
      return Response.json({generation:object.generation,size:String(Buffer.byteLength(object.body))});
    }
    assert.equal(u.hostname,'www.googleapis.com');assert.equal(options.headers.Authorization,'Bearer fake-youtube-access');
    if(u.searchParams.get('uploadType')==='resumable')return new Response(null,{headers:{Location:'https://www.googleapis.com/upload/youtube/v3/videos?upload_id=fake'}});
    if(options.headers['Content-Range'].startsWith('bytes */'))return new Response(null,{status:308,headers:{Range:`bytes=0-${committed-1}`}});
    assert.equal(options.headers['Content-Range'],`bytes ${committed}-${total-1}/${total}`);
    assert.ok(options.body instanceof ReadableStream);
    committed+=new Uint8Array(await new Response(options.body).arrayBuffer()).length;
    return Response.json({id:'fake-video-id',status:{privacyStatus:'private'}});
  };
  const env={STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_BUCKET:'private-test',STUDIO_GCS_SERVICE_ACCOUNT:JSON.stringify(account),PUBLISHER_TOKEN:'x'.repeat(64),GOOGLE_CLIENT_ID:'fake-client',GOOGLE_CLIENT_SECRET:'fake-secret',STUDIO:new Proxy({},{get(){throw Error('R2 must not be accessed');}})};
  const request=(path,value)=>new Request('https://publisher.example'+path,{method:'POST',headers:{Authorization:'Bearer '+env.PUBLISHER_TOKEN,'Content-Type':'application/json'},body:JSON.stringify(value)});
  let response=await handle(request('/uploads/start',{project,path:'story.mp4',title:'Saved story'}),env,fetcher);
  assert.equal(response.status,200);const job=await response.json();
  response=await handle(request('/uploads/next',{id:job.id}),env,fetcher);assert.equal(response.status,200);
  const result=await response.json();assert.equal(result.status,'COMPLETE');assert.equal(result.uploaded,total);assert.equal(committed,total);assert.equal(rangeCount,1);assert.equal(leaseWrites,2);
  assert.ok(!JSON.stringify(result).includes('fake-access'));assert.equal(JSON.parse(objects.get('publisher/jobs/'+job.id+'.json').body).leaseUntil,0);
});
