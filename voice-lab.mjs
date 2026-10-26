import {pairingKey,importHelperPairing,helperJson} from './helper-connection.mjs?v=connection-20261009';
import {nativeRequest} from './native-client.mjs?v=connection-20261009';
import {SAMPLE_SCRIPT,escapeHTML as esc,auditionRequest,previewCosts,usd} from './voice-lab-state.mjs?v=library-20261026';
import {REFERENCE_SCRIPT,VOICE_DESIGNS,QWEN_PRESETS,INCLUDED_VOICES,preferredReference,referenceSource,jobVoiceName} from './voice-lab-library.mjs?v=library-20261026';
const $=id=>document.getElementById(id);
let key='',snapshot=null,modelId='',busy=false,polling=false,timer=null,connectionFailure=false;
const objectURLs=new Map(),jobFingerprints=new Map();
const referenceURLs=new Map();let libraryFingerprint='',referenceOptionsFingerprint='';
let authorPresets={};
const DRAFT_KEY='qt-voice-lab-draft-v1';
const json=(path,body)=>helperJson('/studio/voice-lab'+path,key,body);
const notice=message=>{$('notice').textContent=message||'';};
function selectedModel(){return snapshot?.models?.find(model=>model.id===modelId);}
function formParameters(){const parameters={};document.querySelectorAll('[data-parameter]').forEach(input=>parameters[input.dataset.parameter]=input.type==='checkbox'?input.checked:input.value);return parameters;}
function saveDraft(){try{if(modelId&&$('parameters').dataset.model===modelId)parameterDrafts[modelId]=formParameters();sessionStorage.setItem(DRAFT_KEY,JSON.stringify({model:modelId,mode:$('mode').value,language:$('language').value,text:$('script').value,description:$('description').value,referenceId:$('reference').value,character:{name:$('characterName').value,scene:$('characterScene').value},parameterDrafts}));}catch{}}
let draft={};try{draft=JSON.parse(sessionStorage.getItem(DRAFT_KEY)||'{}');}catch{}
const parameterDrafts=draft.parameterDrafts||{};
$('script').value=draft.text||'';$('description').value=draft.description||'';modelId=draft.model||'';
$('characterName').value=draft.character?.name||'Narrator';$('characterScene').value=draft.character?.scene||'';
function words(){const n=$('script').value.trim().split(/\s+/).filter(Boolean).length;$('wordCount').textContent=n+' words';saveDraft();}
function modeView(){
  const model=selectedModel(),mode=model?.modes?.find(mode=>(typeof mode==='string'?mode:mode.id)===$('mode').value);
  const descriptor=typeof mode==='string'?{id:mode}:mode||{};
  $('descriptionField').hidden=!(descriptor.requiresDescription||descriptor.allowsDescription||descriptor.id==='design');
  $('referenceField').hidden=!(descriptor.requiresReference||descriptor.id==='clone');
  $('characterField').hidden=!(modelId==='drama3'&&descriptor.id==='design');
  $('referenceHint').textContent=descriptor.requiresTranscript?'This model requires an exact transcript of the reference.':'Use a clear reference of a voice you are authorized to use.';
  for(const label of $('parameters').querySelectorAll('[data-parameter-modes]'))if(label.dataset.parameterModes)label.hidden=!label.dataset.parameterModes.split(',').includes(descriptor.id);
  saveDraft();if(snapshot)workerView();
}
function modelView(){
  const model=selectedModel();if(!model)return;
  $('models').innerHTML=snapshot.models.map(item=>`<button type="button" class="model" data-model="${esc(item.id)}" aria-pressed="${item.id===modelId}"><strong>${esc(item.name)}</strong><small>${esc(item.subtitle||({drama3:'Fish Audio',voxcpm2:'OpenBMB',qwen3:'Base · Design · Presets'}[item.id]||''))}</small></button>`).join('');
  $('models').querySelectorAll('[data-model]').forEach(button=>button.addEventListener('click',()=>{saveDraft();modelId=button.dataset.model;draft={};modelView();}));
  $('modelNote').textContent=Array.isArray(model.notes)?model.notes.join(' '):model.notes||model.checkpoint||'';
  $('mode').innerHTML=model.modes.map(mode=>typeof mode==='string'?`<option value="${esc(mode)}">${esc(mode)}</option>`:`<option value="${esc(mode.id)}">${esc(mode.label||mode.id)}</option>`).join('');
  if(model.modes.some(mode=>(typeof mode==='string'?mode:mode.id)===draft.mode))$('mode').value=draft.mode;
  $('language').innerHTML=model.languages.map(language=>`<option value="${esc(language)}">${esc(language)}</option>`).join('');
  const language=model.languages.includes(draft.language)?draft.language:model.languages.find(item=>item.toLowerCase()==='english');
  if(language)$('language').value=language;
  $('parameters').dataset.model=modelId;
  $('parameters').innerHTML=(model.parameters||[]).map(param=>{
    const value=parameterDrafts[model.id]?.[param.id]??param.default;
    const modes=`data-parameter-modes="${esc((param.modes||[]).join(','))}"`;
    if(param.type==='boolean')return `<label ${modes} class="check parameter-check"><input data-parameter="${esc(param.id)}" type="checkbox" ${value===true?'checked':''}><span>${esc(param.label)}<small class="muted">Default ${param.default?'on':'off'}</small></span></label>`;
    if(param.type==='select')return `<label ${modes}>${esc(param.label)}<select data-parameter="${esc(param.id)}">${param.options.map(option=>{const id=typeof option==='string'?option:option.value;return `<option value="${esc(id)}" ${value===id?'selected':''}>${esc(typeof option==='string'?option:option.label)}</option>`;}).join('')}</select><span class="muted">Default ${esc(param.default)}</span></label>`;
    return `<label ${modes}>${esc(param.label)}<input data-parameter="${esc(param.id)}" type="number" min="${esc(param.min)}" max="${esc(param.max)}" step="${esc(param.step??(param.type==='integer'?1:'any'))}" value="${esc(value)}"><span class="muted">Default ${esc(param.default)}</span></label>`;
  }).join('');
  $('parameters').querySelectorAll('input,select').forEach(input=>input.addEventListener('input',saveDraft));
  $('advanced').hidden=!(model.parameters||[]).length;modeView();
}
function useReference(id,targetModel=modelId){
  const reference=snapshot.references.find(ref=>ref.id===id);if(!reference)return;
  saveDraft();modelId=targetModel;
  if(targetModel==='voxcpm2')parameterDrafts[targetModel]={...parameterDrafts[targetModel],clone_strategy:reference.transcript?'prompt_reference':'reference'};
  draft={mode:'clone',referenceId:id,language:$('language').value};$('description').value='';modelView();$('reference').value=id;saveDraft();
  $('create').scrollIntoView({behavior:'smooth',block:'start'});notice(`Reference selected: ${reference.name}. Review the script and settings, then generate.`);
}
async function loadReference(id,element){
  try{
    if(!referenceURLs.has(id)){const response=await nativeRequest('/studio/voice-lab/reference-audio?id='+encodeURIComponent(id),key);referenceURLs.set(id,URL.createObjectURL(await response.blob()));}
    if(element.isConnected){element.src=referenceURLs.get(id);element.hidden=false;}return true;
  }catch(error){notice(error.message);return false;}
}
function libraryView(){
  const priority=ref=>ref.source?.model==='qwen3'&&ref.source?.mode==='design'?0:ref.source?.kind==='audition'?1:2;
  const references=[...(snapshot.references||[])].sort((a,b)=>priority(a)-priority(b)||(b.createdAt||0)-(a.createdAt||0)),completed=(snapshot.jobs||[]).filter(job=>job.status==='COMPLETED'&&job.audioReady),fingerprint=JSON.stringify([references,completed.map(job=>job.id)]);
  if(libraryFingerprint===fingerprint)return;libraryFingerprint=fingerprint;
  const host=$('referenceLibrary'),ids=new Set(references.map(ref=>ref.id));
  for(const element of host.querySelectorAll('[data-library-reference]'))if(!ids.has(element.dataset.libraryReference))element.remove();
  $('emptyReferences')?.remove();
  if(!references.length){host.innerHTML='<p id="emptyReferences" class="muted">No references saved yet. Add a recording in Reference voice mode, or save a completed audition below.</p>';return;}
  for(const ref of references){
    if([...host.children].some(node=>node.dataset.libraryReference===ref.id))continue;
    const article=document.createElement('article');article.className='reference-voice';article.dataset.libraryReference=ref.id;
    article.innerHTML=`<div class="audition-head"><h3>${esc(ref.name)}</h3><span class="status">${Number(ref.durationSeconds).toFixed(1)}s${preferredReference(ref.durationSeconds)?' · target length':''}</span></div><p class="muted">${esc(referenceSource(ref,snapshot.models))}</p><audio hidden controls preload="none" aria-label="${esc(ref.name)} reference preview"></audio><div class="job-actions"><button class="secondary" data-reference-play type="button">Load reference audio</button>${snapshot.models.filter(model=>model.modes.some(mode=>mode.id==='clone')).map(model=>`<button class="secondary" data-reference-model="${esc(model.id)}" type="button">Use in ${esc(model.name)}</button>`).join('')}</div><div class="reference-auditions"></div><details><summary>Reference transcript</summary><p class="muted">${esc(ref.transcript||'No transcript saved. Add one before using a model that requires it.')}</p></details>`;
    host.append(article);
    article.querySelector('[data-reference-play]').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;const success=await loadReference(ref.id,article.querySelector('audio'));button.disabled=false;button.hidden=success;if(!success)button.textContent='Retry reference audio';});
    article.querySelectorAll('[data-reference-model]').forEach(button=>button.addEventListener('click',()=>useReference(ref.id,button.dataset.referenceModel)));
  }
  for(const ref of references){
    const article=[...host.children].find(node=>node.dataset.libraryReference===ref.id),target=article.querySelector('.reference-auditions');
    for(const job of completed.filter(job=>job.referenceId===ref.id)){
      if([...target.children].some(node=>node.dataset.referenceAudition===job.id))continue;
      const row=document.createElement('div');row.className='reference-audition';row.dataset.referenceAudition=job.id;
      const name=snapshot.models.find(model=>model.id===job.model)?.name||job.model;
      row.innerHTML=`<span>${esc(name)} · ${Number(job.metrics?.audioSeconds||0).toFixed(1)}s</span><button class="text-button" type="button">Load clone audition</button><audio hidden controls preload="none" aria-label="${esc(ref.name)} cloned by ${esc(name)}"></audio>`;
      target.append(row);row.querySelector('button').addEventListener('click',async event=>{const button=event.currentTarget;button.disabled=true;const success=await loadAudio(job.id,row.querySelector('audio'));button.disabled=false;button.hidden=success;if(!success)button.textContent='Retry clone audition';});
    }
  }
  let cursor=host.firstElementChild;
  for(const ref of references){const node=[...host.children].find(node=>node.dataset.libraryReference===ref.id);if(node===cursor)cursor=cursor.nextElementSibling;else if(node)host.insertBefore(node,cursor);}
}
function voiceLabel(job){return authorPresets[job.model]?.find(preset=>preset.description===job.description)?.name||jobVoiceName(job,snapshot.references);}
async function authorPresetView(){
  try{
    const response=await fetch('./voice-lab-author-presets.json?v=authors-20261026',{credentials:'omit'});if(!response.ok)throw Error('Cannot load the author preset catalog.');
    const value=await response.json();if(!Array.isArray(value.drama3)||!Array.isArray(value.voxcpm2))throw Error('Invalid author preset catalog.');authorPresets=value;
    $('authorPresets').innerHTML=['drama3','voxcpm2'].map(model=>`<section class="author-preset-group"><h4>${model==='drama3'?'Drama 3 · eight cast templates':'VoxCPM2 · four voice styles'}</h4><div class="preset-voices">${authorPresets[model].map(preset=>`<button class="secondary" type="button" data-author-model="${esc(model)}" data-author-preset="${esc(preset.id)}">${esc(preset.name)}</button>`).join('')}</div></section>`).join('');
    $('authorPresets').querySelectorAll('[data-author-preset]').forEach(button=>button.addEventListener('click',()=>{
      const model=button.dataset.authorModel,preset=authorPresets[model].find(item=>item.id===button.dataset.authorPreset);
      if(!snapshot?.models.find(item=>item.id===model)?.modes.some(mode=>mode.id==='design')){notice('Update the helper to generate this author voice template.');return;}
      saveDraft();modelId=model;draft={mode:'design',language:'Auto'};$('script').value=REFERENCE_SCRIPT;$('description').value=preset.description;$('characterName').value=preset.characterName||'Narrator';$('characterScene').value=preset.scene||'';modelView();words();$('create').scrollIntoView({behavior:'smooth',block:'start'});notice(`${preset.name} is set up. Review the script and settings before generating.`);
    }));
  }catch(error){$('authorPresets').textContent=error.message;}
}
authorPresetView();
$('voiceDesigns').innerHTML=VOICE_DESIGNS.map(voice=>`<article class="voice-design"><h4>${esc(voice.name)}</h4><p class="muted">${esc(voice.description)}</p><button class="secondary" data-design="${esc(voice.id)}" type="button">Set up this voice</button></article>`).join('');
$('voiceDesigns').querySelectorAll('[data-design]').forEach(button=>button.addEventListener('click',()=>{
  const voice=VOICE_DESIGNS.find(item=>item.id===button.dataset.design);saveDraft();modelId='qwen3';draft={mode:'design',language:'English'};$('script').value=REFERENCE_SCRIPT;$('description').value=voice.description;modelView();words();$('create').scrollIntoView({behavior:'smooth',block:'start'});notice('Voice recipe is ready. Generate, listen, then save a clean take as a clone reference.');
}));
$('includedVoices').innerHTML=INCLUDED_VOICES.map(item=>`<article class="included-voice"><h4>${esc(item.name)}</h4><p class="muted">${esc(item.summary)} <a href="${esc(item.url)}" target="_blank" rel="noopener">Official source</a></p></article>`).join('');
$('qwenPresets').innerHTML=QWEN_PRESETS.map(voice=>`<button class="secondary" data-preset="${esc(voice.id)}" type="button">${esc(voice.name)}</button>`).join('');
$('qwenPresets').querySelectorAll('[data-preset]').forEach(button=>button.addEventListener('click',()=>{
  if(!snapshot.models.find(model=>model.id==='qwen3')?.modes.some(mode=>mode.id==='preset')){notice('Update your paired helper to use the named Qwen voices.');return;}
  saveDraft();modelId='qwen3';parameterDrafts.qwen3={...parameterDrafts.qwen3,speaker:button.dataset.preset};draft={mode:'preset',language:'English'};$('script').value=REFERENCE_SCRIPT;$('description').value='';modelView();words();$('create').scrollIntoView({behavior:'smooth',block:'start'});notice('Qwen preset selected. Generate an audition, then save it as a reference to clone its voice with any model.');
}));
function referencesView(){
  const fingerprint=JSON.stringify((snapshot.references||[]).map(ref=>[ref.id,ref.name,ref.durationSeconds]));
  if(fingerprint===referenceOptionsFingerprint)return;referenceOptionsFingerprint=fingerprint;
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
  if(costs)items.push(`Estimated 30s synthesis: ${usd(costs.warm30USD)}`,...(costs.modelLoad30USD===null?[]:[`With model loading: ${usd(costs.modelLoad30USD)}`]),...(costs.cold30USD===null?[]:[`With recorded startup: ${usd(costs.cold30USD)}`]));
  return items.map(item=>`<span>${esc(item)}</span>`).join('');
}
async function loadAudio(id,element){
  if(objectURLs.has(id)){element.src=objectURLs.get(id);element.hidden=false;return true;}
  try{const response=await nativeRequest('/studio/voice-lab/audio?id='+encodeURIComponent(id),key);const url=URL.createObjectURL(await response.blob());objectURLs.set(id,url);if(element.isConnected){element.src=url;element.hidden=false;}return true;}
  catch(error){notice(error.message);return false;}
}
function jobsView(){
  const jobs=snapshot.jobs||[],host=$('jobs'),ids=new Set(jobs.map(job=>job.id));
  for(const element of host.querySelectorAll('[data-job]'))if(!ids.has(element.dataset.job)){element.remove();jobFingerprints.delete(element.dataset.job);}
  if(!jobs.length){host.innerHTML='<p class="muted" id="emptyHistory">Your first preview will appear here.</p>';return;}
  $('emptyHistory')?.remove();
  for(const job of [...jobs].sort((a,b)=>b.createdAt-a.createdAt)){
    const fingerprint=JSON.stringify([job,voiceLabel(job)]),existing=[...host.children].find(node=>node.dataset.job===job.id);
    if(existing&&jobFingerprints.get(job.id)===fingerprint)continue;
    const status=String(job.status||'UNKNOWN').toLowerCase(),complete=status==='completed',active=['queued','submitting','running','unknown','cancel_requested'].includes(status);
    const article=document.createElement('article');article.className='audition';article.dataset.job=job.id;
    article.hidden=$('historyModel').value!==''&&job.model!==$('historyModel').value;
    const model=snapshot.models.find(model=>model.id===job.model);
    article.innerHTML=`<div class="audition-head"><h3>${esc(model?.name||job.model)} · ${esc(job.mode)}</h3><span class="status ${esc(status)}">${esc(status.replaceAll('_',' '))}</span></div><p class="voice-name">${esc(voiceLabel(job))}</p><p>${esc(job.text||'')}</p><div class="metrics">${jobMetrics(job.metrics)}</div>${job.error?`<p class="job-error">${esc(job.error)}</p>`:''}${job.audioReady?'<audio hidden controls preload="none" aria-label="Audition preview"></audio>':''}<div class="job-actions">${complete&&job.audioReady?'<button class="secondary" data-load type="button">Load preview</button><button class="secondary" data-promote type="button">Save as clone reference</button><button class="secondary" data-pick type="button">Save this pick</button><a data-download download="audition.opus" hidden>Download Opus</a>':''}${active?'<button class="secondary" data-cancel type="button">Cancel job</button>':''}<span class="muted">${esc(new Date(job.createdAt*1000).toLocaleString())}</span></div>`;
    if(existing){const oldAudio=existing.querySelector('audio'),newAudio=article.querySelector('audio');if(oldAudio&&newAudio)newAudio.replaceWith(oldAudio);existing.replaceWith(article);}else host.append(article);
    jobFingerprints.set(job.id,fingerprint);
    const audio=article.querySelector('audio'),load=article.querySelector('[data-load]'),download=article.querySelector('[data-download]');
    if(audio&&objectURLs.has(job.id)){if(!audio.src)audio.src=objectURLs.get(job.id);audio.hidden=false;if(load)load.hidden=true;if(download){download.href=objectURLs.get(job.id);download.hidden=false;}}
    load?.addEventListener('click',async()=>{load.disabled=true;load.textContent='Loading…';const success=await loadAudio(job.id,audio);load.disabled=false;load.textContent=success?'Loaded':'Retry preview';load.hidden=success;if(success&&download){download.href=objectURLs.get(job.id);download.hidden=false;}});
    article.querySelector('[data-pick]')?.addEventListener('click',()=>act(async()=>{await json('/select',{model:job.model,jobId:job.id});await refresh();notice('Audition choice saved.');}));
    article.querySelector('[data-promote]')?.addEventListener('click',()=>act(async()=>{const ref=await json('/reference-from-job',{jobId:job.id,name:`${model?.name||job.model} · ${job.mode} · ${Number(job.metrics?.audioSeconds||0).toFixed(1)}s`,rightsConfirmed:true});await refresh();useReference(ref.id,job.model);notice('Voice saved in your reference library. Its original audio and script are preserved. Listen and check the transcript before further clones.');}));
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
    if(connectionFailure){notice('');connectionFailure=false;}
    if(!snapshot.models.some(model=>model.id===modelId))modelId=snapshot.models[0].id;
    $('lab').hidden=false;$('connectionTitle').textContent='Private helper connected';$('connectionDetail').textContent='References and audition history are available through your pairing.';
    if(first)modelView();referencesView();libraryView();workerView();jobsView();
  }finally{polling=false;}
}
async function act(action){
  if(busy)return;busy=true;$('generate').disabled=true;
  try{notice('');await action();}catch(error){notice(error.message);}finally{busy=false;if(snapshot)workerView();}
}
async function connect(){key=pairingKey();if(!key)throw Error('Open your shared helper pairing link, or import its pairing file.');await refresh();clearInterval(timer);timer=setInterval(()=>{if(document.visibilityState==='visible')refresh().catch(error=>{connectionFailure=true;notice(error.message);});},5000);}
$('connect').addEventListener('click',()=>act(connect));
$('pair').addEventListener('click',()=>$('pairFile').click());
$('pairFile').addEventListener('change',()=>act(async()=>{const file=$('pairFile').files[0];if(!file)return;if(file.size>1024)throw Error('Choose the helper’s small pairing text file.');importHelperPairing(await file.text());await connect();}));
$('example').addEventListener('click',()=>{$('script').value=SAMPLE_SCRIPT;words();});
$('script').addEventListener('input',words);$('description').addEventListener('input',saveDraft);$('mode').addEventListener('change',modeView);$('language').addEventListener('change',saveDraft);$('reference').addEventListener('change',saveDraft);
$('characterName').addEventListener('input',saveDraft);$('characterScene').addEventListener('input',saveDraft);
$('refresh').addEventListener('click',()=>act(refresh));
$('historyModel').addEventListener('change',()=>{for(const article of $('jobs').querySelectorAll('[data-job]')){const job=snapshot.jobs.find(item=>item.id===article.dataset.job);article.hidden=$('historyModel').value!==''&&job.model!==$('historyModel').value;}});
document.addEventListener('play',event=>{if(event.target.tagName==='AUDIO')for(const audio of document.querySelectorAll('audio'))if(audio!==event.target&&!audio.paused)audio.pause();},true);
$('openReferenceUpload').addEventListener('click',()=>{draft={mode:'clone',language:$('language').value};modelView();$('referenceField').querySelector('details').open=true;$('referenceField').scrollIntoView({behavior:'smooth',block:'start'});});
$('resetParameters').addEventListener('click',()=>{delete parameterDrafts[modelId];draft={mode:$('mode').value,language:$('language').value};$('parameters').dataset.model='';modelView();saveDraft();notice('This model’s settings were reset to their defaults.');});
$('saveReference').addEventListener('click',()=>act(async()=>{
  const file=$('referenceFile').files[0];if(!file)throw Error('Choose an audio reference.');
  if(!file.type.startsWith('audio/')||file.size>12*1024*1024)throw Error('Choose an audio file smaller than 12 MB.');
  if(!$('rights').checked)throw Error('Confirm permission to use this voice.');
  const audioBase64=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=()=>reject(Error('Cannot read this file.'));reader.readAsDataURL(file);});
  const reference=await json('/reference',{audioBase64,mime:file.type,name:$('referenceName').value.trim()||file.name,transcript:$('transcript').value.trim(),rightsConfirmed:true});
  await refresh();$('reference').value=reference.id;saveDraft();notice('Reference saved privately.');
}));
$('generate').addEventListener('click',()=>act(async()=>{
  const parameters=formParameters();
  const body=auditionRequest(selectedModel(),{mode:$('mode').value,language:$('language').value,text:$('script').value,description:$('description').value,referenceId:$('reference').value,character:{name:$('characterName').value,scene:$('characterScene').value},parameters});
  await json('/jobs',body);saveDraft();await refresh();notice('Audition queued. You can leave this page while the helper runs.');
}));
window.addEventListener('pagehide',event=>{clearInterval(timer);if(!event.persisted){for(const url of [...objectURLs.values(),...referenceURLs.values()])URL.revokeObjectURL(url);objectURLs.clear();referenceURLs.clear();}});
window.addEventListener('pageshow',event=>{if(event.persisted&&key)connect().catch(error=>notice(error.message));});
words();if(pairingKey())act(connect);
