const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const finite=v=>Number.isFinite(v)&&v>=0;
const seconds=v=>finite(v)?`${(v/60).toFixed(1)} min`:'Unknown';
const money=v=>finite(v)?`$${v.toFixed(4)}`:'Unknown';

export function indexProjectSearch(project,files=[]){
  const rows=[];
  const add=(kind,label,text,target)=>{const source=String(text??'');rows.push({kind,label,source,folded:source.toLowerCase(),...target});};
  for(const person of project.characters||[])add('Character',person.name,[person.name,person.description,JSON.stringify(person.permanentIdentity||{}),JSON.stringify(person.currentAppearance||{})].join(' '),{tab:'cast'});
  for(const c of project.chapters||[]){
    add('Chapter',c.name,`${c.name} ${c.sourceText||''}`,{chapter:c.id,tab:'write'});
    add('Narration',c.name,c.cleanNarrationText,{chapter:c.id,tab:'narration'});
    for(const person of c.people||[])add('Person',`${c.name} · ${person.name}`,[person.name,person.description,person.role].join(' '),{chapter:c.id,tab:'cast'});
    for(const scene of c.scenes||[]){
      add('Scene',`${c.name} · ${scene.id}`,[scene.purpose,scene.location,scene.mood,scene.narration].join(' '),{chapter:c.id,shot:scene.shots?.[0]?.id,tab:'review'});
      for(const s of scene.shots||[])add('Shot',`${c.name} · ${s.id}`,[s.id,s.action,s.prompt,s.narration,s.narrationSegment,s.location].join(' '),{chapter:c.id,shot:s.id,tab:'review'});
    }
  }
  for(const f of files)add('File',f.path,`${f.path} ${f.category||''}`,{path:f.path,tab:'files',missing:f.storageStatus==='MISSING'});
  return rows;
}

export function searchProjectIndex(index,query){
  const q=query.trim().toLowerCase();if(!q)return [];
  const rows=[];
  for(const row of index){
    const i=row.folded.indexOf(q);
    if(i>=0){const {source,folded,...target}=row;rows.push({...target,snippet:source.slice(Math.max(0,i-55),i+130)});}
  }
  return rows;
}

export function searchProject(project,query,files=[]){
  return query.trim()?searchProjectIndex(indexProjectSearch(project,files),query):[];
}

export function coverageHTML(time={}){
  const a=time.activity,r=time.rental;
  if(!a&&!r)return '<p class="muted">Detailed activity/rental gap measurements are not recorded for this project.</p>';
  const row=(label,value)=>`<tr><td>${label}</td><td>${seconds(value)}</td></tr>`;
  return `<details><summary>Recorded activity and rental gaps</summary><table><tbody>${a?row('Recorded process activity',a.recordedSeconds)+row('Gaps between recorded processes',a.unrecordedSeconds)+row('Largest gap between processes',a.largestGapSeconds):''}${r?row('Latest measured rental window',r.seconds)+row('Rental startup to verified readiness (included in window)',r.startupToReadySeconds)+row('Rental time with a recorded stage',r.attributedStageSeconds)+row('Rental time without a recorded stage',r.unattributedSeconds)+row('Before first recorded rental stage',r.beforeFirstRecordedSeconds)+row('After last recorded rental stage',r.afterLastRecordedSeconds):''}</tbody></table><p class="muted">${esc(a?.note||r?.note)} Individual startup, transfer and idle times are shown only when recorded. These gaps are not a GPU utilization measurement or an invoice.</p></details>`;
}

export function imagePhasesHTML(report){
  if(!report?.measuredOutputs)return '<p class="muted">Per-image transfer and preparation timings are not recorded for these retained outputs.</p>';
  const labels={preparationSeconds:'Request preparation',referenceUploadSeconds:'Reference upload',submissionSeconds:'Submission',waitAndPollingSeconds:'Server wait / inference / polling',retrievalAndValidationSeconds:'Image retrieval and validation',totalClientSeconds:'Total client image time (includes phases)'};
  return `<details><summary>Image preparation, transfer and wait · ${report.measuredOutputs} measured outputs</summary><table><thead><tr><th>Phase</th><th>Recorded work</th><th>Samples</th></tr></thead><tbody>${Object.entries(labels).map(([key,label])=>`<tr><td>${label}</td><td>${report.phases?.[key]?.samples?seconds(report.phases[key].seconds):'Unknown'}</td><td>${report.phases?.[key]?.samples||0}</td></tr>`).join('')}</tbody></table><p class="muted">${report.measuredOutputs} / ${report.retainedOutputs} retained outputs have some phase measurements. ${esc(report.note)}</p></details>`;
}

export function reportHTML(report){
  const c=report.counts||{},t=report.time||{},cost=report.cost||{};
  return `<p><strong>${esc(report.status)}</strong> · ${report.videoCurrent?'Saved full video is current':'Full video is missing or needs an updated export'}</p><p>${esc(report.scope)}</p><dl class="ws-report-metrics"><div><dt>Video duration</dt><dd>${seconds(report.duration)}</dd></div><div><dt>Recorded active intervals</dt><dd>${seconds(t.coveredWorkSeconds)}</dd></div><div><dt>Observed calendar span</dt><dd>${seconds(t.observedCalendarSeconds)}</dd></div><div><dt>API estimates</dt><dd>${money(cost.apiKnownEstimatedUSD)}</dd></div><div><dt>GPU window estimate</dt><dd>${money(cost.gpuWindowEstimatedUSD)}</dd></div><div><dt>Saved images</dt><dd>${c.savedImages??0} / ${c.images??0}</dd></div></dl><p class="muted">${esc(t.note)} ${esc(cost.note)} ${cost.apiUnknownReceipts||0} API receipts have missing or unresolved cost. ${t.unfinishedSpans||0} spans lack a measured end.</p><p>${c.failedJobs||0} retained failed jobs · ${c.repairAttempts||0} explicitly recorded repair attempts (older repair history may be incomplete).</p><details><summary>Time by process</summary><table><thead><tr><th>Process</th><th>Attempts</th><th>Work</th></tr></thead><tbody>${Object.entries(t.stages||{}).map(([name,row])=>`<tr><td>${esc(name)}</td><td>${row.attempts}</td><td>${seconds(row.sumSeconds)}</td></tr>`).join('')}</tbody></table></details>${coverageHTML(t)}${imagePhasesHTML(t.imagePhases)}<h3>Unresolved visual and continuity findings · ${(report.issues||[]).length}</h3><ul class="ws-result-list">${(report.issues||[]).slice(0,100).map((i,n)=>`<li><span>${i.blocking?'Required: ':''}${esc(i.label)}</span><button data-report-issue="${n}">Open</button></li>`).join('')||'<li>No findings are recorded. This does not certify an unchecked image.</li>'}</ul>${report.issues?.length>100?'<p>First 100 shown; download the full report for the rest.</p>':''}${(report.warnings||[]).length?`<h3>Project warnings</h3><ul>${report.warnings.map(w=>`<li>${esc(w.message)}</li>`).join('')}</ul>`:''}`;
}

export function createProductionTools(ctx){
  const toolbar=()=>`<nav class="ws-tools" aria-label="Project tools"><button data-project-tool="health">Project health</button><button data-project-tool="search">Search everything</button><button data-project-tool="history">Edit history / Undo</button><button data-project-tool="report">Completion report</button></nav>`;
  function readonly(title,body){const m=ctx.formDialog(title,body,()=>{});m.querySelector('.dialog-save').hidden=true;return m;}
  const current=(m,id)=>m.isConnected&&ctx.get().project.id===id;
  async function health(){
    const id=ctx.get().project.id;
    if(!ctx.get().connected)throw Error('Connect the helper to check configured voices, references, storage and caps.');
    await ctx.flush();await ctx.saveSettingsIfVisible();await ctx.saveStrategy();
    if(ctx.get().project.id!==id)throw Error('Project changed; reopen Project health.');
    const options=ctx.productionOptions(false);
    const m=readonly('Project health','<p role="status" id="wsHealth">Checking saved project setup…</p><p class="muted">Read-only checks. No paid API calls, image generation or GPU startup. Live GPU stock and the full working-disk requirement are checked again during production.</p>');
    try{const r=await ctx.api('production-readiness?project='+encodeURIComponent(id)+'&overlap='+options.overlap+'&generationStrategy='+options.generationStrategy+'&unattended='+options.unattended);
      if(!current(m,id))return;
      m.querySelector('#wsHealth').outerHTML=`<section id="wsHealth"><p><strong>${r.ready?'Setup checks passed':'Setup needs attention'}</strong> · revision ${r.revision}</p><ul class="ws-health-list">${r.checks.map(x=>`<li><strong>${x.ready?'Ready':x.blocking?'Required':'Note'}</strong><span>${esc(x.message)}</span></li>`).join('')}</ul></section>`;
    }catch(e){if(current(m,id))m.querySelector('#wsHealth').textContent='Check failed: '+e.message;}
  }
  function search(){
    const {project:p,connected}=ctx.get();let files=[],page=0,results=[],searched=null,fileError=false;
    const storyIndex=indexProjectSearch(p);let fileIndex=[];
    const m=readonly('Search this project','<label>Find character, dialogue, scene, shot or file<input id="wsSearchAll" type="search" placeholder="Name, spoken words, action or filename"></label><p id="wsSearchStatus" class="muted" role="status">Type to search your saved project.</p><ul id="wsSearchResults" class="ws-result-list"></ul><div class="toolbar"><button id="wsSearchPrev">Previous</button><button id="wsSearchNext">Next</button></div>');
    const input=m.querySelector('#wsSearchAll');
    const paint=()=>{if(!current(m,p.id))return;
      const query=input.value.trim().toLowerCase();
      if(query!==searched){results=[...searchProjectIndex(storyIndex,query),...searchProjectIndex(fileIndex,query)];searched=query;}
      page=Math.max(0,Math.min(page,Math.ceil(results.length/30)-1));const visible=results.slice(page*30,page*30+30);
      m.querySelector('#wsSearchStatus').textContent=`${results.length} matches · ${files.length} indexed files${!connected?' · helper offline; file inventory unavailable':fileError?' · file inventory failed; reconnect and reopen to retry':''}.`;
      m.querySelector('#wsSearchResults').innerHTML=visible.map((r,n)=>`<li><div><small>${esc(r.kind)}</small><strong>${esc(r.label)}</strong><p>${esc(r.snippet)}</p></div><button data-search-open="${n}" ${r.missing?'disabled':''}>${r.path?'Preview':'Open'}</button></li>`).join('');
      m.querySelector('#wsSearchPrev').disabled=page===0;m.querySelector('#wsSearchNext').disabled=(page+1)*30>=results.length;
      m.querySelectorAll('[data-search-open]').forEach(b=>b.onclick=()=>ctx.action(async()=>{const r=visible[Number(b.dataset.searchOpen)];if(!current(m,p.id))return;
        if(r.path){const url=await ctx.media(r.path,p.id);if(!current(m,p.id))return;const a=m.ownerDocument.createElement('a');a.href=url;a.target='_blank';a.rel='noopener';a.textContent='Open private file';b.replaceWith(a);a.focus();}
        else{m.close();await ctx.navigate(r);}}));};
    input.oninput=()=>{page=0;paint();};m.querySelector('#wsSearchPrev').onclick=()=>{page--;paint();};m.querySelector('#wsSearchNext').onclick=()=>{page++;paint();};
    if(connected)void ctx.api('files?project='+encodeURIComponent(p.id)).then(r=>{
      if(!current(m,p.id))return;
      files=Array.isArray(r)?r:r.files||[];fileIndex=indexProjectSearch({},files);searched=null;paint();
    }).catch(()=>{fileError=true;paint();});
    paint();input.focus();
  }
  function history(){
    const {project:p}=ctx.get(),rows=[...(p.actionHistory||[])].reverse();
    const m=readonly('Edit history / Undo',`<p>Manual edits saved through the connected helper from this update onward. Undo keeps generated files and refuses to overwrite conflicting newer work. It marks affected exports stale and images for review. Generation, publishing and older edits use their existing asset histories.</p><p id="wsUndoError" role="alert"></p><ul class="ws-result-list">${rows.map((r,i)=>`<li><div><strong>${esc(r.label)}</strong><small>${esc(new Date(r.time*1000).toLocaleString())}</small><p>${r.undone?'Undone':esc(r.reason||`${r.changes.length} changed values`)}</p></div><button data-undo="${i}" ${!r.undoable||r.undone?'disabled':''}>Undo</button></li>`).join('')||'<li>No new manual edits recorded yet.</li>'}</ul>`);
    m.querySelectorAll('[data-undo]').forEach(b=>b.onclick=()=>ctx.action(async()=>{if(!current(m,p.id))return;const row=rows[Number(b.dataset.undo)];
      b.disabled=true;
      try{await ctx.mutate('workspace',{operation:'undo',actionId:row.id,revision:p.revision});m.close();ctx.note('Edit undone. Generated files remain saved; review affected pictures and rerender when ready.');}
      catch(e){if(current(m,p.id)){m.querySelector('#wsUndoError').textContent=e.message;b.disabled=false;}}}));
  }
  async function report(){
    const {project:p,connected}=ctx.get();if(!connected)throw Error('Connect the helper to load retained production measurements.');
    const m=readonly('Completion report','<p role="status" id="wsReportBody">Loading retained measurements…</p>');
    try{const r=await ctx.api('workspace-report?project='+encodeURIComponent(p.id));if(!current(m,p.id))return;
      m.querySelector('#wsReportBody').outerHTML=`<section id="wsReportBody">${reportHTML(r)}<button id="wsDownloadReport">Download full report</button></section>`;
      m.querySelectorAll('[data-report-issue]').forEach(b=>b.onclick=()=>{if(!current(m,p.id))return;const i=r.issues[Number(b.dataset.reportIssue)];m.close();ctx.action(()=>ctx.navigate({...i,tab:i.intro?'settings':i.shot?'review':'timeline'}));});
      m.querySelector('#wsDownloadReport').onclick=()=>{const url=URL.createObjectURL(new Blob([JSON.stringify(r,null,2)],{type:'application/json'}));const a=m.ownerDocument.createElement('a');a.href=url;a.download='studio-completion-report.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
    }catch(e){if(current(m,p.id))m.querySelector('#wsReportBody').textContent='Report unavailable: '+e.message;}
  }
  function wire(){ctx.get().root.querySelectorAll('[data-project-tool]').forEach(b=>b.onclick=()=>ctx.action(({health,search,history,report})[b.dataset.projectTool]));}
  return {toolbar,wire};
}
