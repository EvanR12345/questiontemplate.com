import {pairingKey,importHelperPairing,helperJson} from './helper-connection.mjs?v=connection-20261009';
import {nativeRequest} from './native-client.mjs?v=connection-20261009';
import {SAMPLE_SCRIPT,escapeHTML as esc,auditionRequest,previewCosts,usd} from './voice-lab-state.mjs?v=lab-20261026';
const $=id=>document.getElementById(id);
let key='',snapshot=null,modelId='',busy=false,polling=false,timer=null;
const objectURLs=new Map(),jobFingerprints=new Map();
const DRAFT_KEY='qt-voice-lab-draft-v1';
const json=(path,body)=>helperJson('/studio/voice-lab'+path,key,body);
const notice=message=>{$('notice').textContent=message||'';};
function selectedModel(){return snapshot?.models?.find(model=>model.id===modelId);}
function saveDraft(){try{if(modelId&&$('parameters').dataset.model===modelId){const parameters={};document.querySelectorAll('[data-parameter]').forEach(input=>parameters[input.dataset.parameter]=input.value);parameterDrafts[modelId]=parameters;}sessionStorage.setItem(DRAFT_KEY,JSON.stringify({model:modelId,mode:$('mode').value,language:$('language').value,text:$('script').value,description:$('description').value,referenceId:$('reference').value,parameterDrafts}));}catch{}}
let draft={};try{draft=JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'{}');}catch{}
const parameterDrafts=draft.parameterDrafts||{};
$('script').value=draft.text||'';$('description').value=draft.description||'';modelId=draft.model||'';
function words(){const n=$('script').value.trim().split(/\s+/).filter(Boolean).length;$('wordCount').textContent=n+' words';saveDraft();}
function modeView(){
  const model=selectedModel(),mode=model?.modes?.find(mode=>(typeof mode==='string'?mode:mode.id)===$('mode').value);
  const descriptor=typeof mode==='string'?{id:mode}:mode||{};
  $('descriptionField').hidden=!(descriptor.requiresDescription||descriptor.id==='design');
  $('referenceField').hidden=!(descriptor.requiresReference||descriptor.id==='clone');
  $('referenceHint').textContent=descriptor.requiresTranscript?'This model requires an exact transcript of the reference.':'Use a clear reference of a voice you are authorized to use.';
  saveDraft();if(snapshot)workerView();
}
function modelView(){
  const model=selectedModel();if(!model)return;
  $('models').innerHTML=snapshot.models.map(item=>`<button type="button" class="model" data-model="${esc(item.id)}" aria-pressed="${item.id===modelId}"><strong>${esc(item.name)}</strong><small>${esc(item.subtitle||({drama3:'Fish Audio',voxcpm2:'OpenBMB',qwen3:'Base + VoiceDesign'}[item.id]||''))}</small></button>`).join('');
  $('models').querySelectorAll('[data-model]').forEach(button=>button.addEventListener('click',()=>{saveDraft();modelId=button.dataset.model;draft={};modelView();}));
  $('modelNote').textContent=Array.isArray(model.notes)?model.notes.join(' '):model.notes||model.checkpoint||'';
  $('mode').innerHTML=model.modes.map(mode=>typeof mode==='string'?`<option value="${esc(mode)}">${esc(mode)}</option>`:`<option value="${esc(mode.id)}">${esc(mode.label||mode.id)}</option>`).join('');
  if(model.modes.some(mode=>(typeof mode==='string'?mode:mode.id)===draft.mode))$('mode').value=draft.mode;
  $('language').innerHTML=model.languages.map(language=>`<option value="${esc(language)}">${esc(language)}</option>`).join('');
  const language=model.languages.includes(draft.language)?draft.language:model.languages.find(item=>item.toLowerCase()==='english');
  if(language)$('language').value=language;
  $('parameters').dataset.model=modelId;
  $('parameters').innerHTML=(model.parameters||[]).map(param=>`<label>${esc(param.label)}<input data-parameter="${esc(param.id)}" type="number" min="${esc(param.min)}" max="${esc(param.max)}" step="${esc(param.step??(param.type==='integer'?1:'any'))}" value="${esc(parameterDrafts[model.id]?.[param.id]??param.default)}"><span class="muted">Default ${esc(param.default)}</span></label>`).join('');
  $('parameters').querySelectorAll('input').forEach(input=>input.addEventListener('input',saveDraft));
  $('advanced').hidden=!(model.parameters||[]).length;modeView();
}
function referencesView(){
  const selected=$('reference').value||draft.referenceId;
  $('reference').innerHTML='<option value="">Choose a reference</option>'+(snapshot.references||[]).map(ref=>`<option value="${esc(ref.id)}">${esc(ref.name)} · ${Number(ref.durationSeconds).toFixed(1)}s</option>`).join('');
  if((snapshot.references||[]).some(ref=>ref.id===selected))$('reference').value=selected;
}
function workerView(){
  const worker=snapshot.worker||{},ready=worker.ready===true;
  $('workerTitle').textContent=ready?'Worker connected':'Worker not ready';
  $('workerDetail').textContent=worker.reason||worker.message||(ready?'Models load on demand.':'Set up and connect the private RunPod worker to generate auditions.');
  $('workerCost').textContent=Number.isFinite(worker.hourlyUSD)?`${usd(worker.hourlyUSD)} / GPU hour · ${usd(worker.hourlyUSD/60)} / minute of billed worker time`:'';
  const modelReady=selectedModel()?.ready===true;
  $('generate').disabled=busy||!ready||!modelReady;
  $('generationHint').textContent=ready&&modelReady?'Submit one audition. Switching models can add loading time.':ready?'This model’s environment has not finished preparing.':'Generation becomes available when the private worker is ready.';
  const pick=snapshot.selected;
  $('pickTitle').textContent=pick?(snapshot.models.find(model=>model.id===pick.model)?.name||pick.model):'Listen, then choose';
  $('pickDetail').textContent=pick?'Saved from an audition. Your Studio production voice stays as configured.':'Use “Save this pick” on a completed preview. Your Studio production voice stays as configured.';
}
function jobMetrics(metrics={}){
  const items=[];
  if(Number.isFinite(metrics.audioSeconds))items.push(`${metrics.audioSeconds.toFixed(1)}s audio`);
  if(Number.isFinite(metrics.generationSeconds))items.push(`${metrics.generationSeconds.toFixed(1)}s generation`);
  if(Number.isFinite(metrics.loadSeconds))items.push(`${metrics.loadSeconds.toFixed(1)}s model loading`);
  if(Number.isFinite(metrics.startupSeconds))items.push(`${metrics.startupSeconds.toFixed(1)}s startup / loading`);
  const costs=previewCosts(metrics);
  if(costs)items.push(`Estimated warm 30s: ${usd(costs.warm30USD)}`,...(costs.modelLoad30USD===null?[]:[`With model loading: ${usd(costs.modelLoad30USD)}`]),...(costs.cold30USD===null?[]:[`With recorded startup: ${usd(costs.cold30USD)}`]));
  return items.map(item=>`<span>${esc(item)}</span>`).join('');
}
async function loadAudio(id,element){
  if(objectURLs.has(id)){element.src=objectURLs.get(id);return true;}
  try{const response=await nativeRequest('/studio/voice-lab/audio?id='+encodeURIComponent(id),key);const url=URL.createObjectURL(await response.blob());objectURLs.set(id,url);if(element.isConnected)element.src=url;return true;}
  catch(error){notice(error.message);return false;}
}
function jobsView(){
  const jobs=snapshot.jobs||[],host=$('jobs'),ids=new Set(jobs.map(job=>job.id));
  for(const element of host.querySelectorAll('[data-job]'))if(!ids.has(element.dataset.job)){element.remove();jobFingerprints.delete(element.dataset.job);}
  if(!jobs.length){host.innerHTML='<p class="muted" id="emptyHistory">Your first preview will appear here.</p>';return;}
  $('emptyHistory')?.remove();
  for(const job of [...jobs].sort((a,b)=>b.createdAt-a.createdAt)){
    const fingerprint=JSON.stringify(job),existing=[...host.children].find(node=>node.dataset.job===job.id);
    if(existing&&jobFingerprints.get(job.id)===fingerprint)continue;
    const status=String(job.status||'UNKNOWN').toLowerCase(),complete=status==='completed',active=['queued','submitting','running','unknown','cancel_requested'].includes(status);
    const article=document.createElement('article');article.className='audition';article.dataset.job=job.id;
    const model=snapshot.models.find(model=>model.id===job.model);
    article.innerHTML=`<div class="audition-head"><h3>${esc(model?.name||job.model)} · ${esc(job.mode)}</h3><span class="status ${esc(status)}">${esc(status.replaceAll('_',' '))}</span></div><p>${esc(job.text||'')}</p><div class="metrics">${jobMetrics(job.metrics)}</div>${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${job.audioReady?'<audio controls preload="none" aria-label="Audition preview"></audio>':''}<div class="job-actions">${complete&&job.audioReady?'<button class="secondary" data-load type="button">Load preview</button><button class="secondary" data-pick type="button">Save this pick</button><a data-download download="audition.opus" hidden>Download Opus</a>':''}${active?'<button class="secondary" data-cancel type="button">Cancel job</button>':''}<span class="muted">${esc(new Date(job.createdAt*1000).toLocaleString())}</span></div>`;
    if(existing){const oldAudio=existing.querySelector('audio'),newAudio=article.querySelector('audio');if(oldAudio&&newAudio)newAudio.replaceWith(oldAudio);existing.replaceWith(article);}else host.append(article);
    jobFingerprints.set(job.id,fingerprint);
    const audio=article.querySelector('audio'),load=article.querySelector('[data-load]'),download=article.querySelector('[data-download]');
    if(audio&&objectURLs.has(job.id)){if(!audio.src)audio.src=objectURLs.get(job.id);if(load)load.hidden=true;if(download){download.href=objectURLs.get(job.id);download.hidden=false;}}
    load?.addEventListener('click',async()=>{load.disabled=true;load.textContent='Loading…';const success=await loadAudio(job.id,audio);load.disabled=false;load.textContent=success?'Loaded':'Retry preview';load.hidden=success;if(success&&download){download.href=objectURLs.get(job.id);download.hidden=false;}});
    article.querySelector('[data-pick]')?.addEventListener('click',()=>act(async()=>{await json('/select',{model:job.model,jobId:job.id});await refresh();notice('Audition choice saved.');}));
    article.querySelector('[data-cancel]')?.addEventListener('click',()=>act(async()=>{await json('/cancel',{id:job.id});await refresh();}));
  }
  // Reorder existing nodes without replacing an audio element that is playing.
  let cursor=host.firstElementChild;
  for(const job of [...jobs].sort((a,b)=>b.createdAt-a.createdAt)){const node=[...host.children].find(node=>node.dataset.job===job.id);if(node===cursor)cursor=cursor.nextElementSibling;else if(node)host.insertBefore(node,cursor);}
}
async function refresh(){
  if(polling||!key)return;polling=true;
  try{
    const first=!snapshot;snapshot=await json('');
    if(!Array.isArray(snapshot.models)||snapshot.models.length!==3)throw Error('Update the helper to use the three-model Voice Lab.');
    if(!snapshot.models.some(model=>model.id===modelId))modelId=snapshot.models[0].id;
    $('lab').hidden=false;$('connectionTitle').textContent='Private helper connected';$('connectionDetail').textContent='References and audition history are available through your pairing.';
    if(first)modelView();referencesView();workerView();jobsView();
  }finally{polling=false;}
}
async function act(action){
  if(busy)return;busy=true;$('generate').disabled=true;
  try{notice('');await action();}catch(error){notice(error.message);}finally{busy=false;if(snapshot)workerView();}
}
async function connect(){key=pairingKey();if(!key)throw Error('Open your shared helper pairing link, or import its pairing file.');await refresh();clearInterval(timer);timer=setInterval(()=>{if(document.visibilityState==='visible')refresh().catch(error=>notice(error.message));},5000);}
$('connect').addEventListener('click',()=>act(connect));
$('pair').addEventListener('click',()=>$('pairFile').click());
$('pairFile').addEventListener('change',()=>act(async()=>{const file=$('pairFile').files[0];if(!file)return;if(file.size>1024)throw Error('Choose the helper’s small pairing text file.');importHelperPairing(await file.text());await connect();}));
$('example').addEventListener('click',()=>{$('script').value=SAMPLE_SCRIPT;words();});
$('script').addEventListener('input',words);$('description').addEventListener('input',saveDraft);$('mode').addEventListener('change',modeView);$('language').addEventListener('change',saveDraft);$('reference').addEventListener('change',saveDraft);
$('refresh').addEventListener('click',()=>act(refresh));
$('saveReference').addEventListener('click',()=>act(async()=>{
  const file=$('referenceFile').files[0];if(!file)throw Error('Choose an audio reference.');
  if(!file.type.startsWith('audio/')||file.size>12*1024*1024)throw Error('Choose an audio file smaller than 12 MB.');
  if(!$('rights').checked)throw Error('Confirm permission to use this voice.');
  const audioBase64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Cannot read this file.'));reader.readAsDataURL(file);});
  const reference=await json('/reference',{audioBase64,mime:file.type,name:$('referenceName').value.trim()||file.name,transcript:$('transcript').value.trim(),rightsConfirmed:true});
  await refresh();$('reference').value=reference.id;saveDraft();notice('Reference saved privately.');
}));
$('generate').addEventListener('click',()=>act(async()=>{
  const parameters={};document.querySelectorAll('[data-parameter]').forEach(input=>parameters[input.dataset.parameter]=input.value);
  const body=auditionRequest(selectedModel(),{mode:$('mode').value,language:$('language').value,text:$('script').value,description:$('description').value,referenceId:$('reference').value,parameters});
  await json('/jobs',body);saveDraft();await refresh();notice('Audition queued. You can leave this page while the helper runs.');
}));
window.addEventListener('pagehide',event=>{clearInterval(timer);if(!event.persisted){for(const url of objectURLs.values())URL.revokeObjectURL(url);objectURLs.clear();}});
window.addEventListener('pageshow',event=>{if(event.persisted&&key)connect().catch(error=>notice(error.message));});
words();if(pairingKey())act(connect);
