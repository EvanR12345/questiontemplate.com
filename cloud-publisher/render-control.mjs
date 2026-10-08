import {federatedAccessToken} from './federation.mjs';
const idOK=id=>/^[a-f0-9]{32}$/.test(id??'');
const projectOK=id=>/^pr-[a-f0-9]{16}$/.test(id??'');
const terminal=new Set(['COMPLETE','FAILED','CANCELLED']);
const jobKey=id=>'studio/render-jobs/'+id+'.json';
async function read(bucket,key){const o=await bucket.get(key);return o?{value:await o.json(),etag:o.etag}:null;}
async function write(bucket,key,value,etag){
  const result=await bucket.put(key,JSON.stringify(value),{httpMetadata:{contentType:'application/json'},
    onlyIf:etag?{etagMatches:etag}:{etagDoesNotMatch:'*'}});
  if(!result)throw Error('Cloud render state changed. Refresh before retrying.');
}
export function renderSettings(env){
  if(!env.STUDIO_RENDER_JOB)return {configured:false};
  if(env.STUDIO_STORAGE_PROVIDER!=='gcs'||env.STUDIO_GCS_AUTH_MODE!=='federated'||
     !/^projects\/[a-z][a-z0-9-]+\/locations\/[a-z]+-[a-z]+\d\/jobs\/studio-render$/.test(env.STUDIO_RENDER_JOB))
    throw Error('Configure the private Google render job and matching Google archive.');
  return {configured:true,job:env.STUDIO_RENDER_JOB};
}
function publicState(s){
  return {id:s.id,project:s.project,revision:s.revision,destination:s.destination,status:s.status,
    stage:s.stage,elapsed:s.elapsed??0,completedAssets:s.completedAssets??0,
    cancelRequested:s.cancelRequested===true,result:s.result??null,errorType:s.errorType??null};
}
function preflight(manifest,body){
  const p=manifest?.project;
  if(p?.id!==body.project||p.revision!==body.revision)throw Error('Save the current project to Google before rendering.');
  const chapters=(p.chapters??[]).filter(c=>c.sourceText?.trim());
  if(!chapters.length)throw Error('Add and generate a chapter before rendering.');
  const present=path=>typeof path==='string'&&manifest.files?.[path]?.key;
  for(const c of chapters){
    if(!present(c.audio?.path))throw Error('Save every chapter narration to Google before rendering.');
    const shots=(c.scenes??[]).flatMap(s=>s.shots??[]);
    if(!shots.length||shots.some(s=>!present(s.imagePath)))throw Error('Save every story image to Google before rendering.');
  }
}
// Called only after the Worker's existing private publisher authorization.
export async function renderControl(path,env,body,fetcher=fetch,access=federatedAccessToken){
  const config=renderSettings(env);
  if(!config.configured)throw Error('Cloud rendering has not been deployed. No local fallback was started.');
  if(path==='/renders/status'&&!body?.id){
    if(!projectOK(body?.project))throw Error('Choose a saved project.');
    const lock=await read(env.STUDIO,'studio/render-locks/'+body.project+'.json');
    if(!lock)return {project:body.project,status:'NOT_STARTED'};
    body={...body,id:lock.value.id};
  }
  if(!idOK(body?.id))throw Error('Invalid cloud render identity.');
  const key=jobKey(body.id),existing=await read(env.STUDIO,key);
  if(path==='/renders/status'||path==='/renders/cancel'){
    if(!existing)throw Error('Cloud render was not found.');
    if(body.project&&existing.value.project!==body.project)throw Error('This cloud render belongs to another project.');
    if(path==='/renders/cancel'&&!terminal.has(existing.value.status)){
      existing.value.cancelRequested=true;existing.value.updated=Date.now()/1000;
      await write(env.STUDIO,key,existing.value,existing.etag);
    }
    return publicState(existing.value);
  }
  if(path!=='/renders/start')throw Error('Unknown cloud render action.');
  if(!projectOK(body.project)||!Number.isSafeInteger(body.revision)||body.revision<0||
     !['youtube','patreon'].includes(body.destination))throw Error('Choose a saved project revision and export destination.');
  if(existing){
    if(existing.value.project!==body.project||existing.value.revision!==body.revision||
       existing.value.destination!==body.destination)throw Error('Cloud render identity belongs to another request.');
    // An ambiguous request is never submitted twice, even after a timeout.
    return publicState(existing.value);
  }
  const manifest=await read(env.STUDIO,'studio/manifests/'+body.project+'.json');
  preflight(manifest?.value,body);
  const lockKey='studio/render-locks/'+body.project+'.json',lock=await read(env.STUDIO,lockKey);
  if(lock){
    const prior=await read(env.STUDIO,jobKey(lock.value.id));
    if(!prior||!terminal.has(prior.value.status))throw Error('This project already has a cloud render. Resume or cancel it first.');
  }
  const state={id:body.id,project:body.project,revision:body.revision,destination:body.destination,
    status:'SUBMITTING',stage:'Starting private Google render worker',created:Date.now()/1000,files:{}};
  await write(env.STUDIO,key,state);
  try{await write(env.STUDIO,lockKey,{id:body.id},lock?.etag);}
  catch{
    const failed=await read(env.STUDIO,key);failed.value.status='FAILED';
    failed.value.stage='Another render acquired this project; no worker started';
    await write(env.STUDIO,key,failed.value,failed.etag);throw Error(failed.value.stage);
  }
  let token;
  try{token=await access(env,fetcher,'render');}
  catch{
    const failed=await read(env.STUDIO,key);failed.value.status='FAILED';
    failed.value.stage='Google render authorization failed; no worker started';
    failed.value.errorType='CloudRunAuthorization';await write(env.STUDIO,key,failed.value,failed.etag);
    return publicState(failed.value);
  }
  // Persist ambiguity BEFORE the paid run call. A lost response cannot trigger
  // a second billed execution. Operator reconciliation is required if unknown.
  const saved=await read(env.STUDIO,key);saved.value.status='SUBMISSION_UNKNOWN';
  await write(env.STUDIO,key,saved.value,saved.etag);
  let response;
  try{
    response=await fetcher('https://run.googleapis.com/v2/'+config.job+':run',{
      method:'POST',redirect:'manual',headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},
      body:JSON.stringify({overrides:{taskCount:1,containerOverrides:[{env:[{name:'STUDIO_RENDER_JOB_ID',value:body.id}]}]}})});
  }catch{return publicState(saved.value);}
  const current=await read(env.STUDIO,key);
  if(!response.ok){
    current.value.status='FAILED';current.value.stage='Google rejected the render request; no fallback started';
    current.value.errorType='CloudRunRequestRejected';await write(env.STUDIO,key,current.value,current.etag);
    return publicState(current.value);
  }
  // Worker may already have claimed the request while the HTTP reply arrives.
  // Only change submission state, never regress RESTORING/RENDERING/COMPLETE.
  if(current.value.status==='SUBMISSION_UNKNOWN'){
    current.value.status='QUEUED';current.value.stage='Waiting for Google render worker';
    await write(env.STUDIO,key,current.value,current.etag);
  }
  return publicState(current.value);
}
