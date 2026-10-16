import {lunaUsage} from './studio-luna-usage.mjs';
const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const allShots=c=>(c.scenes||[]).flatMap(s=>s.shots||[]);
const active=j=>['QUEUED','RUNNING'].includes(j.status);
const duration=s=>Number.isFinite(s)?s<60?`${Math.ceil(s)} sec`:`${Math.ceil(s/60)} min`:'Not calibrated';
export function chapterStage(c){
  if(c.render?.path&&!c.renderStale)return 'Rendered';
  const shots=allShots(c);
  if(c.audio?.path&&shots.length&&shots.every(s=>s.imagePath))return 'Ready to render';
  if(shots.length)return 'Images needed';
  return c.sourceText?.trim()?'Needs analysis':'Empty';
}
export function problemList(p,queue){
  const items=[];
  for(const c of p.chapters||[]){
    if(c.continuityNeedsReview)items.push({key:c.id+'continuity',chapter:c.id,tab:'cast',label:`${c.name}: continuity changed`});
    if(c.timingNeedsReview)items.push({key:c.id+'timing',chapter:c.id,tab:'timeline',label:`${c.name}: review restored-audio timing`});
    for(const s of allShots(c)){
      const blocked=s.qcDecision?.blocking||(!s.qcDecision&&s.qc?.pass===false&&s.qc?.status!=='ACCEPTED');
      if(s.generationError||blocked)items.push({key:s.id,chapter:c.id,shot:s.id,tab:'review',label:`${c.name}: ${s.generationError||s.qc?.issues?.[0]||'Image needs review'}`});
    }
    const latest=(queue?.jobs||[]).find(j=>j.project===p.id&&j.chapter===c.id);
    if(latest?.status==='FAILED')items.push({key:latest.id,chapter:c.id,tab:'write',label:`${c.name}: ${latest.message||'Latest job failed'}`});
    else if(c.status==='FAILED'&&c.errors?.length)items.push({key:c.id+'error',chapter:c.id,tab:'write',label:`${c.name}: ${c.errors.at(-1).message}`});
  }
  for(const w of p.warnings||[])if(!w.resolved)items.push({key:w.id,tab:'write',label:w.message});
  return items;
}
export function nextAction(p,queue,connected,knownProblems){
  if(!connected)return {label:'Connect the shared helper',detail:'Your saved project remains available. Connect before running generation.',tab:'write',connect:true};
  const running=queue?.jobs?.find(j=>j.project===p.id&&active(j));
  if(running)return {label:queue.paused?'Resume when ready':'Production is running',detail:running.message||running.kind,tab:'write'};
  const empty=p.chapters.find(c=>!c.sourceText?.trim());
  if(empty)return {label:'Add the next chapter text',detail:empty.name,chapter:empty.id,tab:'write'};
  const problems=knownProblems??problemList(p,queue);
  if(problems.length)return {label:'Review an issue',detail:problems[0].label,...problems[0]};
  if(p.render?.path&&!p.renderStale)return {label:'Watch or publish your finished video',detail:'Publishing is an explicit action in Files.',tab:'timeline'};
  const chapter=p.chapters.find(c=>!c.audio?.path||!allShots(c).length||allShots(c).some(s=>!s.imagePath));
  return chapter?{label:'Ready to generate your story',detail:'Use Generate full video after reviewing your setup and spending limits.',chapter:chapter.id,tab:'write'}:
    {label:'Assemble your saved assets',detail:'Use Render full story in Video. Existing compatible chapter exports can be reused.',tab:'timeline'};
}
export function impact(kind,{count=1,seconds=null,cost=null}={}){
  const effects={image:['Replace selected image outputs','Narration and other images stay saved. Rerender affected chapters and full story.'],
    narration:['Replace chapter narration','The entire chapter recording is regenerated. Review visual timing and rerender that chapter.'],
    analyze:['Reanalyze selected chapters','Narration and direction can change. Matched manual edits remain; later continuity can require review.'],
    'render-chapter':['Render selected chapters','Reuses saved audio and images; no image generation or directing is requested.'],
    'preview':['Render a short edit preview','Local saved-media render, up to 30 seconds. No new images or directing. Full exports stay unchanged; intro, outro and reminders are excluded.'],
    'render-full':['Assemble the full story','Reuses compatible chapter exports. Rendering, transfer and storage can still cost money.'],
    reminders:['Remove subscribe reminders and ding','Requires a new video render. Images and narration are reused; the outro stays as configured.'],
    settings:['Apply saved setup','Affects future generation and rendering. Existing shot models, images and recordings are retained. Spending caps, character assets and credentials are preserved.']};
  const [title,detail]=effects[kind]||['Change this project','Saved generations remain available.'];
  return {title,detail,count,time:seconds==null?'No compatible timing estimate':duration(seconds),
    cost:cost==null?'Cost not calibrated; saved spending caps still apply':`Estimated $${cost.toFixed(4)}; not an invoice`};
}
export function fileLocation(row){
  const status=row.storageStatus;
  return status==='MISSING'?['Missing','Neither helper nor cloud copy is recorded.']:status==='PENDING_UPLOAD'?['Uploading / pending','Keep the helper copy until cloud verification finishes.']:
    row.cloud?[row.local===undefined?'Cloud saved · local status unknown':row.local?'Cloud + helper':'Cloud only',row.removalAdvice||'Checksum recorded at archive time; reverify before removing local files.']:
    ['Helper only',row.removalAdvice||'Do not remove until a matching cloud copy is verified.'];
}
export function versionsOf(c,shot){return shot?(shot.history||[]).filter(v=>v?.imagePath):(c.audioHistory||[]).filter(v=>v?.path);}
export function filterChapters(chapters,query='',filter='all'){
  const term=query.trim().toLowerCase();
  return chapters.filter(c=>(!term||`${c.number} ${c.name} ${c.sourceText||''}`.toLowerCase().includes(term))&&
    (filter==='all'||filter==='rendered'&&chapterStage(c)==='Rendered'||filter==='unfinished'&&chapterStage(c)!=='Rendered'));
}
const impactHTML=v=>`<div class="ws-impact"><strong>${esc(v.title)} · ${v.count} item${v.count===1?'':'s'}</strong><p>${esc(v.detail)}</p><p class="muted">Time: ${esc(v.time)} · ${esc(v.cost)}</p></div>`;

export function createWorkspaceTools(ctx){
  const states=new Map();
  const state=()=>{const id=ctx.get().project.id;if(!states.has(id))states.set(id,{query:'',filter:'all',page:0,lo:1,hi:null,selected:new Set()});return states.get(id);};
  const navigate=target=>ctx.action(()=>ctx.navigate(target));
  function dashboard(){
    const {project:p,queue:q,connected,root}=ctx.get(),el=root.querySelector('#studioWorkspaceSummary');if(!el)return;
    // Queue failures can change without a new project revision.
    const problems=problemList(p,q),next=nextAction(p,q,connected,problems);
    const jobs=(q?.jobs||[]).filter(j=>j.project===p.id&&active(j));
    const other=(q?.jobs||[]).some(j=>j.project!==p.id&&active(j));
    const use=lunaUsage(p.production?.timings||[]),gpu=p.production?.costs?.gpuWindowEstimatedUSD;
    const cloud=q?.storage||ctx.get().health?.storage,save=cloud?.projects?.[p.id];
    const cloudLabel=!connected?'Cloud status unavailable':!cloud?.enabled?'Helper / browser save':save?.status==='SYNCED'&&save.revision>=p.revision?'Cloud saved':save?.status==='FAILED'?'Cloud save failed':save?.status==='UPLOADING'?'Uploading to cloud':'Cloud save pending';
    const rendered=p.chapters.filter(c=>chapterStage(c)==='Rendered').length;
    // ETA belongs to the shared queue, not an invented full-production forecast.
    const eta=jobs.length?(other?'Shared queue; see queue below':q?.etaStatus==='ESTIMATED'&&Number.isFinite(q.etaSeconds)?duration(q.etaSeconds)+' · queued work': 'Estimate coverage incomplete'):'No active jobs';
    const opened=el.querySelector('details')?.open;
    const html=`<section class="ws-dashboard" aria-label="Production overview">${ctx.productionTools?.toolbar()||''}<div class="ws-next"><div><span class="kicker">What’s next?</span><strong>${esc(next.label)}</strong><p>${esc(next.detail)}</p></div><button data-ws-next>${next.connect?'Connect helper':'Open step'}</button></div><dl class="ws-metrics"><div><dt>Chapters exported</dt><dd>${rendered} / ${p.chapters.length}</dd></div><div><dt>Current task</dt><dd>${esc(q?.paused&&jobs.length?'Paused':jobs.find(j=>j.status==='RUNNING')?.kind||jobs[0]?.kind||'Idle')}</dd></div><div><dt>Time remaining</dt><dd>${esc(eta)}</dd></div><div><dt>Recorded cost estimates</dt><dd>${use.calls?'API $'+use.cost.toFixed(4):'API: no receipts'} · ${Number.isFinite(gpu)?'GPU $'+gpu.toFixed(4):'GPU: unknown'}</dd></div><div><dt>Project save</dt><dd>${esc(cloudLabel)}</dd></div></dl><p class="muted ws-cost-note">Retained receipts only; storage, unresolved requests and external rentals may be missing. This is not the account bill.</p><details data-ui="workspace-problems" ${opened?'open':''}><summary>Problems inbox · ${problems.length}</summary>${problems.length?`<ul class="ws-problems">${problems.slice(0,30).map((x,i)=>`<li><span>${esc(x.label)}</span><button data-ws-problem="${i}">Open issue</button></li>`).join('')}</ul>${problems.length>30?'<p>Showing the first 30 issues. Resolve these to reveal the rest.</p>':''}`:'<p>No current blocking findings or latest-job failures are recorded. This does not replace visual review.</p>'}</details></section>`;
    // Leave focus and expanded details alone when background polling changed nothing.
    if(el.dataset.summaryHTML===html)return;
    el.innerHTML=html;el.dataset.summaryHTML=html;ctx.productionTools?.wire();
    el.querySelector('[data-ws-next]').onclick=()=>next.connect?ctx.connect():navigate(next);
    el.querySelectorAll('[data-ws-problem]').forEach(b=>b.onclick=()=>navigate(problems[Number(b.dataset.wsProblem)]));
  }
  function railHTML(){
    const {project:p,chapterId}=ctx.get(),s=state(),rows=filterChapters(p.chapters,s.query,s.filter);
    s.page=Math.max(0,Math.min(s.page,Math.ceil(rows.length/20)-1));
    const valid=new Set(p.chapters.map(c=>c.id));for(const id of s.selected)if(!valid.has(id))s.selected.delete(id);
    return `<h2>Chapters <small>${p.chapters.length}</small></h2><label class="ws-sr" for="wsChapterSearch">Search chapters</label><input id="wsChapterSearch" placeholder="Find chapter or story text" value="${esc(s.query)}"><label class="ws-sr" for="wsChapterFilter">Filter chapters</label><select id="wsChapterFilter"><option value="all">All chapters</option><option value="unfinished" ${s.filter==='unfinished'?'selected':''}>Unfinished</option><option value="rendered" ${s.filter==='rendered'?'selected':''}>Rendered</option></select><div class="ws-chapter-list">${rows.slice(s.page*20,s.page*20+20).map(c=>`<div class="ws-chapter-row"><input type="checkbox" aria-label="Select ${esc(c.name)}" data-ws-select="${c.id}" ${s.selected.has(c.id)?'checked':''}><button class="chapter-button ${c.id===chapterId?'selected':''}" data-ws-chapter="${c.id}" ${c.id===chapterId?'aria-current="true"':''}><span>${c.number.toString().padStart(2,'0')}</span><strong>${esc(c.name)}</strong><small>${esc(chapterStage(c))}</small></button></div>`).join('')||'<p>No matching chapters.</p>'}</div><div class="ws-pager"><button data-ws-page="-1" ${s.page?'':'disabled'} aria-label="Previous chapter page">←</button><small>${rows.length?`${s.page*20+1}–${Math.min(rows.length,s.page*20+20)}`:'0'} / ${rows.length}</small><button data-ws-page="1" ${(s.page+1)*20<rows.length?'':'disabled'} aria-label="Next chapter page">→</button></div><details data-ui="chapter-selection"><summary>Work on selected chapters · ${s.selected.size}</summary><div class="toolbar"><button data-ws-select-all>Select matching</button><button data-ws-clear>Clear</button></div><div class="ws-range"><label>From<input id="wsRangeStart" type="number" min="1" value="${s.lo}"></label><label>Through<input id="wsRangeEnd" type="number" min="1" value="${s.hi??p.chapters.length}"></label></div><button data-ws-range>Select range</button><p class="muted">Selection affects only these buttons. Full video still includes every chapter.</p><div class="toolbar"><button data-ws-batch="analyze">Analyze selected</button><button data-ws-batch="narration">Narrate selected</button><button data-ws-batch="render-chapter">Export selected</button></div><p class="muted">Exports are individual chapter videos in Files. Jobs run in saved chapter order; existing matching work is reused.</p></details><button class="chapter-add" id="productionAddChapter">+ New chapter</button>`;
  }
  function wireRail(){
    const {root}=ctx.get(),rail=root.querySelector('.chapter-rail');if(!rail)return;
    function repaint(){const expanded=rail.querySelector('details')?.open;const focus=rail.ownerDocument.activeElement;const selected=focus?.id==='wsChapterSearch'?{start:focus.selectionStart,end:focus.selectionEnd}:null;
      rail.innerHTML=railHTML();if(expanded)rail.querySelector('details').open=true;
      // New chapter retains the existing handler, passed explicitly after rail redraws.
      rail.querySelector('#productionAddChapter').onclick=ctx.addChapter;
      if(selected){const f=rail.querySelector('#wsChapterSearch');f.focus();f.setSelectionRange(selected.start,selected.end);}}
    rail.oninput=e=>{if(e.target.id==='wsChapterSearch'){state().query=e.target.value;state().page=0;repaint();}};
    rail.onchange=e=>{if(e.target.id==='wsChapterFilter'){state().filter=e.target.value;state().page=0;repaint();}if(e.target.id==='wsRangeStart')state().lo=e.target.value;if(e.target.id==='wsRangeEnd')state().hi=e.target.value;if(e.target.dataset.wsSelect){const s=state();e.target.checked?s.selected.add(e.target.dataset.wsSelect):s.selected.delete(e.target.dataset.wsSelect);repaint();}};
    rail.onclick=e=>{const b=e.target.closest('button');if(!b)return;const s=state();
      if(b.dataset.wsChapter)return navigate({chapter:b.dataset.wsChapter,tab:ctx.get().tab});
      if(b.dataset.wsPage){s.page+=Number(b.dataset.wsPage);repaint();}
      if(b.hasAttribute('data-ws-select-all')){for(const c of filterChapters(ctx.get().project.chapters,s.query,s.filter))s.selected.add(c.id);repaint();}
      if(b.hasAttribute('data-ws-clear')){s.selected.clear();repaint();}
      if(b.hasAttribute('data-ws-range')){const lo=Number(rail.querySelector('#wsRangeStart').value),hi=Number(rail.querySelector('#wsRangeEnd').value);if(!Number.isSafeInteger(lo)||!Number.isSafeInteger(hi)||lo<1||hi<lo){ctx.note('Choose a valid ascending chapter range.',true);return;}for(const c of ctx.get().project.chapters)if(c.number>=lo&&c.number<=hi)s.selected.add(c.id);repaint();}
      if(b.dataset.wsBatch)ctx.action(()=>batch(b.dataset.wsBatch));
    };
  }
  function confirmImpact(kind,options,fn){
    const projectId=ctx.get().project.id;
    const modal=ctx.formDialog('Before you continue',impactHTML(impact(kind,options)),async()=>{if(ctx.get().project.id!==projectId)throw Error('Project changed. Reopen this action in the correct project.');await fn();});
    modal.querySelector('.dialog-save').textContent='Continue';return modal;
  }
  async function batch(kind){
    const {project:p,connected}=ctx.get(),ids=p.chapters.filter(c=>state().selected.has(c.id)).map(c=>c.id);
    if(!connected)throw Error('Connect the helper before queuing chapter work.');
    if(!ids.length)throw Error('Select at least one chapter.');
    confirmImpact(kind,{count:ids.length},async()=>{
      let queued=0,skipped=0;
      try{for(const id of ids){if(ctx.get().project.id!==p.id)throw Error('Project changed; no more jobs were submitted.');
        const queue=await ctx.api('queue');
        if(queue.jobs.some(j=>j.project===p.id&&active(j)&&(j.kind==='produce-story'||j.kind===kind&&j.chapter===id))){skipped++;continue;}
        await ctx.api('jobs',{project:p.id,chapter:id,kind});queued++;
      }}catch(error){throw Error(`${queued} queued, ${skipped} already active; remaining chapters were not queued. ${error.message}`);}
      ctx.note(`${queued} chapter jobs queued; ${skipped} already active. Monitor the production queue.`);await ctx.refreshQueue();
    });
  }
  function setupHTML(){return `<section class="section-box ws-setups"><h3>My saved setups</h3><p class="muted">Save your current look, voice, image workflow, checks and finishing preferences by name. New projects inherit this setup library. Character photos, story content, spending caps and credentials stay with their project.</p><label>Setup name<input id="wsSetupName" maxlength="80" placeholder="My fantasy narrator"></label><button id="wsSaveSetup">Save current settings as setup</button><label>Load setups from project<select id="wsSetupProject"></select></label><button id="wsLoadSetups">Load setup list</button><label>Saved setup<select id="wsSetupChoice"></select></label><button id="wsApplySetup">Apply selected setup</button><p class="muted" id="wsSetupStatus" role="status"></p></section>`;}
  function wireSetups(){
    const {root,project:p,projects=[]}=ctx.get();const source=root.querySelector('#wsSetupProject');if(!source)return;
    source.innerHTML=[p,...projects.filter(x=>x.id!==p.id)].map(x=>`<option value="${esc(x.id)}">${esc(x.name)}</option>`).join('');
    const choice=root.querySelector('#wsSetupChoice'),status=root.querySelector('#wsSetupStatus');let sourceId=p.id;
    const paint=list=>{choice.innerHTML=list.map(x=>`<option>${esc(x.name)}</option>`).join('');choice.disabled=!list.length;root.querySelector('#wsApplySetup').disabled=!list.length;status.textContent=list.length?`${list.length} saved setups`:'No setups saved yet.';};paint(p.savedSetups||[]);
    source.onchange=()=>{sourceId=null;choice.innerHTML='';choice.disabled=true;root.querySelector('#wsApplySetup').disabled=true;status.textContent='Load this project’s setup list first.';};
    root.querySelector('#wsLoadSetups').onclick=()=>ctx.action(async()=>{const id=source.value,loaded=id===p.id?ctx.get().project:await ctx.api('project?id='+encodeURIComponent(id));if(ctx.get().project.id!==p.id||source.value!==id||!source.isConnected)return;sourceId=id;paint(loaded.savedSetups||[]);});
    root.querySelector('#wsSaveSetup').onclick=()=>ctx.action(async()=>{const name=root.querySelector('#wsSetupName').value.trim();if(!name)throw Error('Name your setup first.');await ctx.saveSettings();if(ctx.get().project.id!==p.id)throw Error('Project changed. Reopen Settings in the correct project.');await ctx.mutate('workspace',{operation:'save-setup',name,revision:ctx.get().project.revision});ctx.note('Setup saved. Existing work is unchanged.');});
    root.querySelector('#wsApplySetup').onclick=()=>ctx.action(()=>{if(!sourceId||!choice.value)throw Error('Load and choose a saved setup.');const id=sourceId,name=choice.value;confirmImpact('settings',{},async()=>{await ctx.saveSettings();if(ctx.get().project.id!==p.id)throw Error('Project changed. Reopen Settings in the correct project.');await ctx.mutate('workspace',{operation:'apply-setup',name,sourceProject:id,revision:ctx.get().project.revision});});});
  }
  function compare(shot){
    const {project:p,chapter:c}=ctx.get(),versions=versionsOf(c,shot);if(!versions.length)throw Error('No earlier saved versions are available yet.');
    const current=shot?shot.imagePath:c.audio?.path,revision=p.revision;
    const modal=ctx.formDialog(shot?'Compare image versions':'Compare narration versions',`<p>Current output stays selected until you choose an earlier version and save. Both files remain saved.</p><label>Earlier version<select id="wsVersion">${versions.map((v,i)=>`<option value="${i}">Version ${i+1} · ${esc(v.reason||v.metadata?.model||v.voice||v.imageMetadata?.model||'Saved output')}</option>`).join('')}</select></label><div class="ws-compare"><figure><figcaption>Current</figcaption>${shot?'<img id="wsCurrentMedia" alt="Current illustration">':'<audio id="wsCurrentMedia" controls></audio>'}</figure><figure><figcaption>Earlier</figcaption>${shot?'<img id="wsEarlierMedia" alt="Earlier illustration">':'<audio id="wsEarlierMedia" controls></audio>'}</figure></div><p id="wsVersionStatus" role="status"></p><p class="muted">${shot?'Selecting an older picture requires a fresh review against current shot requirements.':'Only recordings of the current script can be restored. A different duration scales the existing visual boundaries; review sentence alignment afterward.'} A new render is required to change the video.</p>`,async()=>{if(ctx.get().project.id!==p.id)throw Error('Project changed. Reopen comparison.');await ctx.mutate('workspace',{operation:shot?'restore-image':'restore-audio',chapter:c.id,shot:shot?.id,index:Number(modal.querySelector('#wsVersion').value),revision});});
    modal.querySelector('.dialog-save').textContent='Use earlier version';
    let request=0;const load=async()=>{const n=++request,v=versions[Number(modal.querySelector('#wsVersion').value)],status=modal.querySelector('#wsVersionStatus');status.textContent='Loading private previews…';
      try{const urls=await Promise.all([current?ctx.media(current,p.id):null,ctx.media(shot?v.imagePath:v.path,p.id)]);if(!modal.isConnected||n!==request)return;for(const [id,url]of[['#wsCurrentMedia',urls[0]],['#wsEarlierMedia',urls[1]]])if(url)modal.querySelector(id).src=url;status.textContent='Compare both versions. Closing keeps the current output.';}
      catch{if(modal.isConnected&&n===request)status.textContent='A preview could not load. Reconnect the helper and check file availability.';}};
    modal.addEventListener('close',()=>modal.querySelectorAll('audio').forEach(a=>a.pause()),{once:true});
    modal.querySelector('#wsVersion').onchange=load;void load();return modal;
  }
  function finishingHTML(){
    const {chapter:c}=ctx.get(),shots=allShots(c);return `<section class="section-box ws-finishing"><h3>Make a small edit</h3><p class="muted">Change one asset, then rerender the affected chapter. The saved video remains available while you edit.</p><label>Shot<select id="wsEditShot">${shots.map(s=>`<option value="${esc(s.id)}">${duration(s.start)} · ${esc((s.action||s.id).slice(0,95))}</option>`).join('')}</select></label><div class="toolbar"><button data-ws-finish="replace" ${shots.length?'':'disabled'}>Replace picture</button><button data-ws-finish="compare" ${shots.length?'':'disabled'}>Compare picture versions</button><button data-ws-finish="render-preview" ${shots.length&&c.audio?.path?'':'disabled'}>Render short edit preview</button><button data-ws-finish="sentence">Fix narration sentence</button><button data-ws-finish="audio" ${versionsOf(c).length?'':'disabled'}>Compare recordings</button><button data-ws-finish="reminders">Remove subscribe reminders</button><button data-ws-finish="preview" ${c.render?.path&&shots.length?'':'disabled'}>Preview selected shot in saved video</button></div><p class="muted">Preview plays the existing export at that shot’s time; new edits appear after rendering. Fixing narration regenerates the chapter WAV, not just a detached sentence.</p>${(c.previewRenders||[]).length?`<details><summary>Rendered edit previews · ${c.previewRenders.length}</summary><p>Local render using saved media, up to 30 seconds. No image generation, directing, intro, outro or reminders. Long cropped shots restart their movement.</p>${c.previewRenders.slice(-5).reverse().map(v=>`<figure><figcaption>${duration(v.start)} → ${duration(v.end)}${v.superseded?' · earlier edit':''}</figcaption><video controls preload="none" data-asset="${esc(v.path)}" aria-label="Rendered edit preview"></video></figure>`).join('')}</details>`:''}${c.timingNeedsReview?`<p class="notice">${esc(c.timingNote)} <button data-ws-finish="timing-reviewed">Mark timing reviewed</button></p>`:''}</section>`;
  }
  function wireFinishing(){const {root,chapter:c,project:p}=ctx.get();
    root.querySelectorAll('[data-ws-finish]').forEach(b=>b.onclick=()=>ctx.action(async()=>{
      const kind=b.dataset.wsFinish,shot=allShots(c).find(s=>s.id===root.querySelector('#wsEditShot')?.value);
      if(kind==='render-preview'){
        return confirmImpact('preview',{count:1},async()=>{
          await ctx.mutate('jobs',{chapter:c.id,kind:'render-preview',options:{shot:shot.id}});
          ctx.note('Short preview queued locally, using only saved pictures and narration. The full export is unchanged.');
        });
      }
      if(kind==='compare')return compare(shot);
      if(kind==='audio')return compare();
      if(kind==='sentence'){await ctx.navigate({chapter:c.id,tab:'narration'});const input=ctx.get().root.querySelector('#narrationScript');input?.focus();const segment=shot?.narration||shot?.narrationSegment;const i=typeof segment==='string'?input.value.indexOf(segment):-1;if(i>=0)input.setSelectionRange(i,i+segment.length);ctx.note('Edit the exact spoken script, then regenerate narration. The whole chapter audio is replaced; previous recordings stay in comparison history.');return;}
      if(kind==='reminders')return confirmImpact('reminders',{},()=>ctx.patch('project',p.id,{settings:{...p.settings,engagement:{...p.settings.engagement,popupEnabled:false,dingEnabled:false}}},true));
      if(kind==='timing-reviewed')return ctx.patch('chapter',c.id,{timingNeedsReview:false},true);
      if(kind==='replace'){
        const modal=ctx.formDialog('Replace this picture',`<p>Upload an illustration for this shot, or close this dialog and edit/regenerate it in Layout. Other shots and narration stay saved. This replacement needs review and a new render.</p><label>Illustration<input id="wsReplacement" type="file" accept="image/png,image/jpeg,image/webp"></label>`,async()=>{const file=modal.querySelector('#wsReplacement').files[0];if(!file)throw Error('Choose an image first.');if(ctx.get().project.id!==p.id||ctx.get().chapter.id!==c.id)throw Error('Chapter changed. Reopen the replacement dialog.');const result=await ctx.upload(file);if(ctx.get().project.id!==p.id||ctx.get().chapter.id!==c.id)throw Error('Chapter changed. Uploaded file is saved; reopen the replacement dialog.');await ctx.replaceImage(shot,result);});return;
      }
      if(kind==='preview'){
        const video=root.querySelector('video[data-asset="'+CSS.escape(c.render.path)+'"]');if(!video)throw Error('The saved chapter preview is unavailable.');
        if(!video.src)throw Error('Wait for the saved video link to load, then retry.');
        video.currentTime=Math.max(0,shot.start);video.scrollIntoView({block:'center',behavior:'smooth'});video.focus();
        const stop=()=>{if(video.currentTime>=shot.end){video.pause();video.removeEventListener('timeupdate',stop);}};
        if(video._workspaceStop)video.removeEventListener('timeupdate',video._workspaceStop);video._workspaceStop=stop;video.addEventListener('timeupdate',stop);await video.play();
      }
    }));
  }
  return {dashboard,railHTML,setupHTML,finishingHTML,confirmImpact,compare,wire(){
    const {root,chapter:c,tab}=ctx.get();
    if(tab==='review'){
      const displayed=c.scenes.flatMap(scene=>scene.shots);
      for(const card of root.querySelectorAll('.shot-card')){
        const button=card.querySelector('[data-shot]'),shot=displayed.find(s=>s.id===button?.dataset.shot);if(!shot)continue;
        card.id='shot-'+shot.id;card.tabIndex=-1;
        if(versionsOf(c,shot).length){const b=root.ownerDocument.createElement('button');b.textContent='Compare saved versions';b.onclick=()=>ctx.action(()=>compare(shot));card.querySelector('.toolbar').append(b);}
      }
    }
    if(tab==='narration'&&versionsOf(c).length){const b=root.ownerDocument.createElement('button');b.textContent='Compare saved recordings';b.onclick=()=>ctx.action(()=>compare());root.querySelector('#productionNarrate')?.parentElement.append(b);}
    wireRail();wireSetups();wireFinishing();dashboard();
  }};
}
