import {buildPlan,schedule,formatTime} from './pipeline-engine.mjs?v=luna-forecast-20261010';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const seconds=n=>Number.isFinite(n)?n.toFixed(3)+'s':'Not measured',money=n=>'$'+n.toFixed(3);
const phaseNames={preparationSeconds:'Build request',referenceUploadSeconds:'Reference upload/cache',submissionSeconds:'Submit operation',waitAndPollingSeconds:'Wait / poll (includes server)',retrievalAndValidationSeconds:'Retrieve / decode check',pngEncodeAndDurableSaveSeconds:'PNG encode / durable save',historyAndMetadataReceiptSeconds:'History / metadata receipt',provisionAndReadinessSeconds:'Provision / readiness',downloadAndHashSeconds:'Download / verify hashes',timedCohortWallSeconds:'Timed image cohorts',experimentalWarmupClientSeconds:'Experimental warmups'};
export const ALIGNMENT_BUFFER_SECONDS=15;

export function startupBoundaries(config){
 return config.chapters<=64?Array.from({length:config.chapters},(_,i)=>i+1):
  [...new Set([1,config.chapters,config.readyChapters,...Array.from({length:64},(_,i)=>1+Math.round(i*(config.chapters-1)/63))])].sort((a,b)=>a-b);
}
export function* startupSearch(config,evidence,compact=false){
 const evaluate=(n,gpuStartSeconds=0)=>{
  const p=buildPlan({...config,policy:'proposed',earlyGpu:false,readyChapters:n,gpuStartSeconds},evidence),r=schedule(p);
  const directorEnd=Math.max(...r.tasks.filter(t=>t.kind==='handoff'||t.kind==='introPlan').map(t=>t.end));
  const imageEnd=Math.max(...r.tasks.filter(t=>['delivered','save'].includes(t.kind)).map(t=>t.end));
  const boot=r.tasks.find(t=>t.id==='boot');
  // The startup table needs aggregates, not 150 duplicate multi-chapter timelines.
  const result=compact?Object.fromEntries(Object.entries(r).filter(([k])=>k!=='tasks')):r;
  const plan=compact?{config:p.config,gpu:p.gpu,attemptCount:p.attemptCount,tasks:[p.tasks.find(t=>t.id==='boot')]}:p;
  return {readyChapters:n,gpuStartSeconds,result,plan,directorEnd,imageEnd,gap:imageEnd-directorEnd,bootStart:boot.start};
 };
 const boundaries=startupBoundaries(config);
 const values=[];for(const n of boundaries){values.push(evaluate(n));yield `Comparing ${boundaries.length===config.chapters?'all':'sampled'} chapter boundaries · ${values.length} / ${boundaries.length}`;}
 // Find a later clock start that preserves earliest image delivery and final finish.
 // This bounded binary search is a scheduling heuristic, not a global optimum.
 {
  const baseline=values.reduce((a,b)=>a.result.end<=b.result.end?a:b);
  let lo=0,hi=baseline.imageEnd,balanced=baseline;
  for(let i=0;i<16&&hi-lo>1;i++){
   const mid=(lo+hi)/2,v=evaluate(baseline.readyChapters,mid);
   if(v.imageEnd<=baseline.imageEnd+.5&&v.result.end<=baseline.result.end+.5){lo=mid;balanced=v;}else hi=mid;
   yield `Refining video finish timing · ${i+1} / 16`;
  }
  if(balanced.gpuStartSeconds>baseline.bootStart+1)values.push({...balanced,balanced:true,keepFastestVideo:true});
 }
 // A coarse scan plus local refinement avoids assuming smooth/monotonic timing.
 // Match the last-chapter billed interval, allowing at most a small waiting buffer.
 const last=values.find(v=>v.readyChapters===config.chapters),target=last.result.rentalSeconds+ALIGNMENT_BUFFER_SECONDS;
 const qualifies=v=>v.result.rentalSeconds<=target+.001;
 const first=values[0];let best=last,step=(last.bootStart-first.bootStart)/32;
 for(let i=0;i<=32;i++){
  const v=evaluate(1,first.bootStart+step*i);
  if(qualifies(v)&&v.bootStart<best.bootStart)best=v;
  yield `Finding low-wait alignment · ${i+1} / 33`;
 }
 for(let level=0;level<2&&step>.25;level++){
  const upper=best.bootStart,lower=Math.max(first.bootStart,upper-step),spacing=(upper-lower)/16;
  for(let i=0;i<=16;i++){
   const v=evaluate(1,lower+spacing*i);
   if(qualifies(v)&&v.bootStart<best.bootStart)best=v;
   yield `Refining low-wait alignment · ${level+1} / 2 · ${i+1} / 17`;
  }
  step=spacing;
 }
 values.push({...best,balanced:true,keepFastestVideo:false,bufferSeconds:ALIGNMENT_BUFFER_SECONDS,rentalReferenceSeconds:last.result.rentalSeconds,rentalExtraSeconds:Math.max(0,best.result.rentalSeconds-last.result.rentalSeconds)});
 return values.map(v=>({...v,pareto:!values.some(o=>o!==v&&o.result.end<=v.result.end+.01&&o.result.totalUSD<=v.result.totalUSD+.00001&&(o.result.end<v.result.end-.01||o.result.totalUSD<v.result.totalUSD-.00001))}));
}
export function startupCandidates(config,evidence){
 const search=startupSearch(config,evidence);let step;do{step=search.next();}while(!step.done);return step.value;
}
export function mountStartupTradeoffs(host,config,evidence,onApply){
 const cacheKey=JSON.stringify(config);
 if(host._startupKey!==cacheKey){
  host._startupKey=cacheKey;host._startupValues=null;
  const generation=(host._startupGeneration||0)+1;host._startupGeneration=generation;
  host._startupWorker?.terminate();
  host.innerHTML='<div class="section-head"><h3>Spend less on rented waiting</h3><span>Same images · same model</span></div><p class="caption" role="status" data-start-progress>Comparing startup timings… Controls remain available.</p>';
  if(typeof Worker!=='undefined'){
   const worker=new Worker(new URL('./pipeline-worker.mjs?v=luna-forecast-20261010',import.meta.url),{type:'module'});host._startupWorker=worker;
   worker.onmessage=({data})=>{
    if(host._startupGeneration!==generation)return;
    if(data.type==='progress')host.querySelector('[data-start-progress]').textContent=data.message+' · controls remain available.';
    else if(data.type==='complete'){worker.terminate();host._startupWorker=null;host._startupValues=data.values;mountStartupTradeoffs(host,config,evidence,onApply);}
    else{worker.terminate();host._startupWorker=null;host.innerHTML=`<p class="caption">Startup comparison unavailable: ${esc(data.message)}</p>`;}
   };
   worker.onerror=()=>{worker.terminate();if(host._startupGeneration===generation)host.innerHTML='<p class="caption">Background startup comparison could not load. The main schedule remains available.</p>';};
   worker.postMessage({type:'startup',config,evidence});return;
  }
  const search=startupSearch(config,evidence,true);
  host._startupTask=(async()=>{
   try{for(;;){await new Promise(resolve=>setTimeout(resolve,0));if(host._startupGeneration!==generation)return;
    const step=search.next();if(step.done){host._startupValues=step.value;mountStartupTradeoffs(host,config,evidence,onApply);return;}
    host.querySelector('[data-start-progress]').textContent=step.value+' · controls remain available.';
   }}catch(error){if(host._startupGeneration===generation)host.innerHTML=`<p class="caption" role="status">Startup comparison unavailable: ${esc(error.message)}</p>`;}
  })();
  return;
 }
 if(!host._startupValues)return;
 const values=host._startupValues,fastest=values.reduce((a,b)=>a.result.end<=b.result.end?a:b),cheapest=values.reduce((a,b)=>a.result.gpuUSD<=b.result.gpuUSD?a:b),balanced=values.find(v=>v.balanced);
 const aligned=values.find(v=>v.balanced&&!v.keepFastestVideo),featured=[...new Set([fastest,balanced,aligned,cheapest].filter(Boolean))];
 const table=list=>`<div class="evidence-table"><table><thead><tr><th>Start strategy</th><th>GPU boot</th><th>Luna done</th><th>Images done</th><th>Image tail</th><th title="Project start through final video checks, including audio, directing, images, rendering and assembly">Total time</th><th>GPU idle/setup</th><th>Rental</th><th>GPU $</th><th>Total cost</th><th></th></tr></thead><tbody>${list.map(v=>`<tr><td>${v.balanced?(v.keepFastestVideo?'Timed · keep fastest video':`Timed · align Luna + images · ${v.bufferSeconds}s buffer`):`After ${v.readyChapters} / ${config.chapters} chapter plans`}${v===fastest?' · fastest video':''}${v===cheapest?' · lowest rental':''}</td><td>${formatTime(v.bootStart)}</td><td>${formatTime(v.directorEnd)}</td><td>${formatTime(v.imageEnd)}</td><td>${v.gap<0?'−':''}${formatTime(Math.abs(v.gap))}</td><td>${formatTime(v.result.end)}</td><td>${formatTime(v.result.gpuIdleSeconds)}</td><td>${formatTime(v.result.rentalSeconds)}</td><td>${money(v.result.gpuUSD)}</td><td>${money(v.result.totalUSD)}</td><td><button class="button" data-ready="${v.readyChapters}" data-start-index="${values.indexOf(v)}">Use</button></td></tr>`).join('')}</tbody></table></div>`;
 host.innerHTML=`<div class="section-head"><h3>Spend less on rented waiting</h3><span>Same images · same model</span></div><p class="caption">${config.chapters<=64?'Test every chapter boundary':'Compare 64 evenly spaced chapter boundaries plus your selected boundary'} plus timed startups between boundaries. “Keep fastest video” preserves the faster video finish. “Align Luna + images” targets the same billed rental duration as starting after the last chapter, plus at most ${ALIGNMENT_BUFFER_SECONDS}s of waiting buffer. It starts as early as this bounded search finds within that budget; rendering can finish later. Last shots still require the final chapter plan.</p>${table(featured)}<details><summary>${config.chapters<=64?'All '+config.chapters:values.filter(v=>!v.balanced).length+' sampled'} chapter-boundary options</summary>${table(values.filter(v=>!featured.includes(v)))}</details><p class="caption">“Total time” includes narration, directing, images, rendering, assembly and final checks from project start. Rental includes setup and transfers. The buffer is extra rented waiting, not free GPU time; actual calculated costs are shown. All values are projections without manual moves. “All chapters” waits for plans, not the finished video. This bounded scan is not a global optimum or measured production run. “Use” clears manual moves and can be undone.</p>`;
 host.querySelectorAll('[data-start-index]').forEach(b=>b.onclick=()=>{const v=values[Number(b.dataset.startIndex)];onApply({readyChapters:v.readyChapters,gpuStartSeconds:v.gpuStartSeconds});});
}

export function imageAccountingHTML(profile,gpu,roundIndex=0){
 const trace=profile?.traces?.[roundIndex];if(!trace)return '';
 const attempts=gpu?.attempts||[],attempt=attempts.find(a=>a.attempt===profile.attempt),lease=attempt?.leaseAccounting;
 const phases=[...Object.keys(phaseNames).slice(0,7)];
 const rows=trace.jobs.map(j=>`<tr><td>${j.index}</td><td>C${j.worker+1} / M${(j.serverWorker??j.worker)+1}</td>${phases.map(k=>`<td>${seconds(j.accounting?.parts?.[k])}</td>`).join('')}<td>${seconds(j.accounting?.unattributedWrapperSeconds)}</td><td>${seconds(j.accounting?.measuredClientSeconds)}</td><td>${money(j.allocatedWarmCostUSD||0)}</td></tr>`).join('');
 const leaseRows=lease?Object.entries(lease.parts).map(([k,v])=>`<tr><td>${esc(phaseNames[k]||k)}</td><td>${seconds(v)}</td></tr>`).join(''):'';
 return `<section class="panel"><h3>Every image: measured client accounting</h3><p class="caption">${esc(trace.label)} · C = client slot, M = model copy. Costs allocate cohort rental evenly; latency × hourly price would double-charge overlapping images. Server execution is nested within Wait, not added again.</p><div class="evidence-table"><table class="micro-table"><thead><tr><th>Image</th><th>Client/model</th>${phases.map(k=>`<th>${phaseNames[k]}</th>`).join('')}<th>Unassigned wrapper</th><th>Total client</th><th>Warm cost/image</th></tr></thead><tbody>${rows}</tbody></table></div><p class="caption">Missing timings remain “Not measured”. Signed residuals expose instrumentation boundaries. Across concurrent clients, the sum of client latencies exceeds the cohort wall clock; it is not total rental time.</p></section>
 <section class="panel"><h3>Full rental accounting: ${esc(profile.attempt)}</h3>${lease?`<div class="evidence-table"><table><thead><tr><th>Measured parent interval</th><th>Seconds</th></tr></thead><tbody>${leaseRows}<tr><td>Unassigned setup, utility calls, bookkeeping and shutdown</td><td>${seconds(lease.unattributedSeconds)}</td></tr><tr><th>Start → verified deletion</th><th>${seconds(lease.rentalClockSeconds)}</th></tr></tbody></table></div><p class="caption">${esc(lease.scope)} Closure residual: ${seconds(lease.closureErrorSeconds)}.</p>`:'<p class="caption">This attempt has no complete verified rental accounting. No shutdown or missing seconds are invented.</p>'}</section>`;
}

export function mountObservedRuns(host,details){
 const records=details.observed,serial=records.find(r=>r.mode==='serial'),overlap=records.find(r=>r.mode==='overlap');
 host.innerHTML=`<div class="schedulebar"><div><h2>How the saved experiment actually ran</h2><p class="caption">Recorded clocks, separate from the drag-and-drop estimate.</p></div></div><div class="observed-summary"><div><span>Serial image + review</span><strong>${seconds(serial.wallSeconds)}</strong></div><div><span>Installed overlap test</span><strong>${seconds(overlap.wallSeconds)}</strong></div><div><span>Observed shorter window</span><strong>${(100*(1-overlap.wallSeconds/serial.wallSeconds)).toFixed(1)}%</strong></div></div>${records.map(r=>{
 const stages=[...new Set(r.spans.map(s=>s.stage))],w=780,x=n=>150+n/r.wallSeconds*600;
 const height=stages.length*49+48;
 const chart=`<svg class="recorded-chart" viewBox="0 0 ${w} ${height}" role="img" aria-label="Recorded ${r.mode} image and review intervals">${[0,.25,.5,.75,1].map(f=>`<path d="M${x(r.wallSeconds*f)} 16V${height-22}" stroke="#dce3dc"/><text x="${x(r.wallSeconds*f)}" y="${height-4}" text-anchor="middle">${(r.wallSeconds*f).toFixed(1)}s</text>`).join('')}${stages.map((stage,i)=>`<text x="2" y="${i*49+37}">${esc(stage)}</text>`).join('')}${r.spans.map(s=>`<g><title>${esc(s.stage)}: ${seconds(s.start)}–${seconds(s.end)} (${seconds(s.seconds)}), ${esc(s.status)}${s.nested?', nested in image + review':''}</title><rect x="${x(s.start)}" y="${stages.indexOf(s.stage)*49+20}" width="${Math.max(.4,(s.end-s.start)/r.wallSeconds*600)}" height="22" rx="2" fill="${s.nested?'#94b79e':'#456850'}"/></g>`).join('')}</svg>`;
 return `<section class="panel"><h3>${r.mode==='serial'?'Original serial':'Installed overlap'} · ${r.images} images · ${seconds(r.wallSeconds)}</h3>${chart}<p class="caption">${esc(r.scope)} Hover for exact span times. This fixture does not include chapter directing, narration, pod boot or final rendering.</p><details><summary>All ${r.spans.length} recorded spans</summary><div class="evidence-table"><table><thead><tr><th>Span</th><th>Start</th><th>End</th><th>Duration</th><th>Scope/status</th></tr></thead><tbody>${r.spans.map(s=>`<tr><td>${esc(s.id)} · ${esc(s.stage)}</td><td>${seconds(s.start)}</td><td>${seconds(s.end)}</td><td>${seconds(s.seconds)}</td><td>${s.nested?'Nested':'Parent'} · ${esc(s.status)}</td></tr>`).join('')}</tbody></table></div></details></section>`;
 }).join('')}<div class="inline-note">This is a controlled image-stage comparison, not proof of the full video speedup. GPU benchmark traces are available in GPU benchmarks. The Schedule tab simulates the complete pipeline, including measured aggregates and explicit allowances.</div>`;
}

export function mountFunctionIndex(host,details,onStage){
 let query='',module='all',page=0;
 host.innerHTML=`<div class="schedulebar"><div><h3>Inside every helper module</h3><p class="caption">${details.functions.length} functions across ${details.modules.length} modules. Source inventory, not an execution trace.</p></div></div><div class="function-controls"><label>Search functions<input type="search" data-find placeholder="phonemes, crop, retry, save…"></label><label>Module<select data-module><option value="all">All modules</option>${details.modules.map(m=>`<option value="${esc(m.name)}">${esc(m.name)} · ${m.functions}</option>`).join('')}</select></label></div><p class="caption">${esc(details.scope)} Function durations are included in their measured parent, or unmeasured; no invented per-function seconds.</p><div data-functions></div><div class="function-paging"><button class="button" data-prev>Previous</button><span data-page></span><button class="button" data-next>Next</button></div>`;
 const draw=()=>{
  const filtered=details.functions.filter(f=>(module==='all'||f.file.endsWith('/'+module))&&(`${f.name} ${f.purpose} ${f.category} ${f.calls.join(' ')}`).toLowerCase().includes(query));
  page=Math.max(0,Math.min(page,Math.max(0,Math.ceil(filtered.length/30)-1)));
  host.querySelector('[data-functions]').innerHTML=filtered.slice(page*30,page*30+30).map(f=>`<details class="function-row"><summary><code>${esc(f.name)}()</code><span>${esc(f.category)}</span></summary><p>${esc(f.purpose)}</p><div class="function-meta"><span>${esc(f.file)}:${f.line}</span><span>${f.installedSourceMatches?'Installed helper matches this module':'Workspace snapshot; installed helper differs or unavailable'}</span></div><p class="caption">Arguments: ${esc(f.arguments.join(', ')||'none')}<br>${esc(f.timing)}</p><p class="caption">Static calls: ${esc(f.calls.join(' → ')||'no calls')} ${f.moreCalls?' · '+f.moreCalls+' additional calls':''}</p><button class="button" data-stage="${esc(f.stage)}">Inspect parent process</button></details>`).join('')||'<p class="caption">No functions match.</p>';
  host.querySelector('[data-page]').textContent=`${filtered.length} matching · page ${page+1} / ${Math.max(1,Math.ceil(filtered.length/30))}`;
  host.querySelector('[data-prev]').disabled=page===0;host.querySelector('[data-next]').disabled=(page+1)*30>=filtered.length;
  host.querySelectorAll('[data-stage]').forEach(b=>b.onclick=()=>onStage(b.dataset.stage));
 };
 host.querySelector('[data-find]').oninput=e=>{query=e.target.value.toLowerCase();page=0;draw();};
 host.querySelector('[data-module]').onchange=e=>{module=e.target.value;page=0;draw();};
 host.querySelector('[data-prev]').onclick=()=>{page--;draw();};host.querySelector('[data-next]').onclick=()=>{page++;draw();};draw();
}
