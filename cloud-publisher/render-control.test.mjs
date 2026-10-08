import test from 'node:test';
import assert from 'node:assert/strict';
import {renderControl,renderSettings} from './render-control.mjs';
class Bucket{
  data=new Map();serial=0;
  async get(key){const x=this.data.get(key);return x?{etag:x.etag,json:async()=>JSON.parse(x.text)}:null;}
  async put(key,text,options={}){
    const old=this.data.get(key),condition=options.onlyIf;
    if(condition?.etagMatches&&condition.etagMatches!==old?.etag)return null;
    if(condition?.etagDoesNotMatch==='*'&&old)return null;
    const etag=String(++this.serial);this.data.set(key,{etag,text});return {etag};
  }
}
async function fixture(){
  const bucket=new Bucket(),project='pr-0123456789abcdef',id='a'.repeat(32);
  await bucket.put('studio/manifests/'+project+'.json',JSON.stringify({project:{id:project,revision:4,
    chapters:[{sourceText:'Story.',audio:{path:'chapter.wav'},scenes:[{shots:[{imagePath:'art.png'}]}]}]},
    files:{'chapter.wav':{key:'audio'},'art.png':{key:'art'}}}));
  return {env:{STUDIO:bucket,STUDIO_STORAGE_PROVIDER:'gcs',STUDIO_GCS_AUTH_MODE:'federated',
    STUDIO_RENDER_JOB:'projects/example-project/locations/us-east1/jobs/studio-render'},
    body:{id,project,revision:4,destination:'patreon'}};
}
test('rendering remains disabled until matching Google archive and job are configured',()=>{
  assert.equal(renderSettings({}).configured,false);
  assert.throws(()=>renderSettings({STUDIO_RENDER_JOB:'https://attacker.example/run'}));
});
test('refresh discovers the active cloud job without needing browser-local state',async()=>{
  const {env,body}=await fixture();
  assert.equal((await renderControl('/renders/status',env,{project:body.project})).status,'NOT_STARTED');
  await renderControl('/renders/start',env,body,async()=>Response.json({}),async()=> 'test');
  const state=await renderControl('/renders/status',env,{project:body.project});
  assert.equal(state.id,body.id);assert.equal(state.status,'QUEUED');
  await assert.rejects(renderControl('/renders/cancel',env,{id:body.id,project:'pr-1111111111111111'}),/another project/);
  assert.equal((await renderControl('/renders/status',env,{project:body.project})).cancelRequested,false);
});
test('starts one pinned cloud render with only a job ID override and no media transfer',async()=>{
  const {env,body}=await fixture();let calls=0;
  const access=async(e,f,purpose)=>{assert.equal(purpose,'render');return 'test-only-access';};
  const run=async(url,options)=>{
    calls++;assert.equal(url,'https://run.googleapis.com/v2/'+env.STUDIO_RENDER_JOB+':run');
    assert.equal(options.redirect,'manual');assert.deepEqual(JSON.parse(options.body),{
      overrides:{taskCount:1,containerOverrides:[{env:[{name:'STUDIO_RENDER_JOB_ID',value:body.id}]}]}});
    return Response.json({name:'test-operation'});
  };
  assert.equal((await renderControl('/renders/start',env,body,run,access)).status,'QUEUED');
  assert.equal((await renderControl('/renders/start',env,body,run,access)).status,'QUEUED');
  assert.equal(calls,1);
  await assert.rejects(renderControl('/renders/start',env,{...body,id:'b'.repeat(32)},run,access),/already has/);
  assert.equal(calls,1);
});
test('failed or missing preflight cannot start cloud compute',async()=>{
  const {env,body}=await fixture();let calls=0;
  const never=async()=>{calls++;throw Error('Should not call cloud');};
  await assert.rejects(renderControl('/renders/start',env,{...body,revision:5},never,never),/Save the current/);
  await assert.rejects(renderControl('/renders/start',env,{...body,destination:'unknown'},never,never));
  const key='studio/manifests/'+body.project+'.json',record=await env.STUDIO.get(key),p=await record.json();
  delete p.files['chapter.wav'];await env.STUDIO.put(key,JSON.stringify(p));
  await assert.rejects(renderControl('/renders/start',env,body,never,never),/narration/);
  assert.equal(calls,0);
});
test('a lost Cloud Run response is saved as ambiguous and never submitted again',async()=>{
  const {env,body}=await fixture();let calls=0;
  const lost=async()=>{calls++;throw Error('upstream secret must not enter job state');};
  const access=async()=> 'test-only-access';
  assert.equal((await renderControl('/renders/start',env,body,lost,access)).status,'SUBMISSION_UNKNOWN');
  assert.equal((await renderControl('/renders/start',env,body,lost,access)).status,'SUBMISSION_UNKNOWN');
  assert.equal(calls,1);
  assert.ok(!JSON.stringify([...env.STUDIO.data]).includes('upstream secret'));
});
test('a quickly started worker cannot be regressed to queued by the launch response',async()=>{
  const {env,body}=await fixture();
  const started=async()=>{
    const key='studio/render-jobs/'+body.id+'.json',o=await env.STUDIO.get(key),s=await o.json();
    s.status='RENDERING';await env.STUDIO.put(key,JSON.stringify(s),{onlyIf:{etagMatches:o.etag}});
    return Response.json({});
  };
  assert.equal((await renderControl('/renders/start',env,body,started,async()=> 'test')).status,'RENDERING');
  const cancelled=await renderControl('/renders/cancel',env,{id:body.id});
  assert.equal(cancelled.cancelRequested,true);assert.equal(cancelled.status,'RENDERING');
});
