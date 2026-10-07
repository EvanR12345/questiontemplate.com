import {mountStartupTradeoffs,mountObservedRuns,mountFunctionIndex} from './pipeline-detail-ui.mjs';
import {DEFAULTS,CATALOG,LANES,VERSION,buildPlan,schedule,formatTime,explainMove,importSnapshot} from './pipeline-engine.mjs';
import {mountConcurrencyLab} from './pipeline-lab.mjs';
import {serverlessHTML} from './pipeline-serverless.mjs';
import {matchedHTML} from './pipeline-matched.mjs';
import {gpuChoices,selectGPUConfig,executionLabel,generationSpeed} from './pipeline-config.mjs?v=automatic-speed-20261007';
import {mountGPUExplorer} from './pipeline-gpu-explorer.mjs';
const $=id=>document.getElementById(id), esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=n=>'$'+n.toFixed(2), sec=n=>n<60?n.toFixed(1)+'s':(n/60).toFixed(1)+'m';
const STORE='questiontemplate-production-planner-v1';
const keys=Object.keys(DEFAULTS),booleans=keys.filter(k=>typeof DEFAULTS[k]==='boolean');
let evidence,config={...DEFAULTS},preferences={},plan,result,serial,selected=null,uncertainty=20,history=[],view='schedule',focus='all',scale=1,positions=new Map(),playing=false,playAt=0,playStarted=0,playFrame,saveTimer,toastTimer;
const snapshot=()=>({type:'studio-pipeline-plan',version:VERSION,config:{...config},preferences:structuredClone(preferences),uncertainty});
function checkpoint(){history.push(snapshot());if(history.length>40)history.shift();$('undo').disabled=false;}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,4500);}
function persist(){clearTimeout(saveTimer);saveTimer=setTimeout(()=>{try{localStorage.setItem(STORE,JSON.stringify(snapshot()));$('saved').textContent='Saved on this device';}catch{$('saved').textContent='Device storage unavailable — export your plan';}},180);}
function setControls(){for(const k of keys){if(!$(k))continue;if(booleans.includes(k))$(k).checked=config[k];else $(k).value=config[k];}$('uncertainty').value=uncertainty;}
function readControls(){const next={};for(const k of keys){if(!$(k))continue;next[k]=booleans.includes(k)?$(k).checked:typeof DEFAULTS[k]==='number'?Number($(k).value):$(k).value;}return {...config,...next};}
function getTask(id){return result.tasks.find(t=>t.id===id);}
function renderGenerationSpeed(){
  const speed=generationSpeed(plan,evidence);
  $('generationRate').value=speed.highest===null?'Not measured':speed.highest.toFixed(1);
  $('generationRateNote').textContent=speed.highest===null?'No completed generation-speed measurement for this timing profile. Choose a measured resolution.':
    `Highest tested: ${speed.label}. Warm delivery includes preparation and saving; startup is separate.`+
    (speed.isFastest?'':` Current manual setup: ${speed.current.toFixed(1)} images/min; the schedule uses that rate.`);
  $('fastestGPU').disabled=speed.highest===null||speed.isFastest;
}
function labelChapter(n){return n===0?'Project / intro':`Chapter ${String(n).padStart(2,'0')}`;}
function switchView(next){view=next;document.querySelectorAll('[data-view]').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.view===next)));for(const name of ['schedule','processes','gpus','observed','evidence'])$(name+'View').hidden=name!==next;if(next==='schedule')renderTimeline();if(next==='gpus')renderGPUs();}
function rebuild(){
  stopPlay();
  try{
    plan=buildPlan(config,evidence);result=schedule(plan,preferences);
    serial=schedule(buildPlan({...config,policy:'serial'},evidence));
    if(result.diagnostics.length)throw Error(result.diagnostics.join('; '));
    $('error').hidden=true;
    config=plan.config;
    renderGenerationSpeed();
    $('sampleField').hidden=config.qc!=='sampled';$('introFields').hidden=!config.intro;
    $('modeNote').textContent=config.policy==='proposed'?'Planning experiment: separates GPU work from retrieval and overlaps chapter rendering. This is not deployed production behavior.':config.policy==='current'?'Installed cloud overlap: Luna lookahead and QC share API slots. The image lane includes preparation through retrieval. Rendering begins after image work.':'Every task runs sequentially. This is the comparison baseline, not a speed recommendation.';
    $('scheduleDescription').textContent=`${config.policy==='proposed'?'Proposed':config.policy==='current'?'Installed overlap model':'Sequential baseline'} · ${plan.imageCount} images · ${result.tasks.length} operations · ${Object.keys(preferences).length} manual moves`;
    $('metricTime').textContent=formatTime(result.end);
    $('metricRange').textContent=`${formatTime(result.end*(1-uncertainty/100))}–${formatTime(result.end*(1+uncertainty/100))} assumed sensitivity`;
    const saving=1-result.end/serial.end;$('metricSaved').textContent=(saving*100).toFixed(0)+'% '+(saving>=0?'less time':'more time');
    $('metricSerial').textContent=`${formatTime(serial.end)} fully sequential`;
    $('metricCost').textContent=money(result.totalUSD);$('metricCostParts').textContent=`${money(result.gpuUSD)} rental + ${money(result.apiUSD)} API + ${money(config.storageDaily)} storage`;
    $('metricBusyLabel').textContent=plan.measurement?'Image pipeline / rented':'GPU work / rented';$('metricBusy').textContent=`${Math.round(result.gpuWork/result.rentalSeconds*100)}%`;
    $('metricImages').textContent=`${formatTime(result.rentalSeconds)} rented · ${plan.attemptCount} image attempts`;
    $('taskCount').textContent=`${result.tasks.length} tasks · ${plan.measurement?plan.measurement.workers+' measured workers on one GPU'+(config.executionMode!=='resident'?' · '+plan.measurement.clientSlots+' request slots':''):'one inference lane'}`;
    $('profileScope').textContent=plan.measurement?`${config.resolution} image pipeline · ${plan.measurement.workers} workers${config.executionMode!=='resident'?' · '+plan.measurement.clientSlots+' request slots':''} · ${config.encodingProfile} conditioning · renderer remains 720p/24fps`:'Historical 1344 × 768 images · one worker · renderer 720p/24fps';
    $('workerField').hidden=config.resolution==='legacy';
    if(plan.measurement)$('modeNote').textContent+=` Measured worker groups include image preparation through saving once. Internal stages cannot be moved separately from these aggregate measurements. ${plan.renderScope}`;
    const oldFocus=focus;
    $('chapterFocus').innerHTML='<option value="all">All chapters</option><option value="0">Project / intro</option>'+Array.from({length:config.chapters},(_,i)=>`<option value="${i+1}">Chapter ${String(i+1).padStart(2,'0')}</option>`).join('');
    focus=oldFocus==='all'||Number(oldFocus)<=config.chapters?oldFocus:'all';$('chapterFocus').value=focus;
    if(selected&&!getTask(selected))selected=null;
    renderTimeline();renderBreakdown();renderProcesses();renderPairs();renderEvidence();renderInspector();
    mountStartupTradeoffs($('startTradeoffs'),config,evidence,n=>{checkpoint();config={...config,earlyGpu:false,readyChapters:n,policy:'proposed'};preferences={};setControls();rebuild();toast('GPU startup dependency changed. Production settings remain unchanged.');});
    if(view==='gpus')renderGPUs();persist();
  }catch(e){$('error').textContent=e.message;$('error').hidden=false;}
}
function renderTimeline(){
  if(!result||view!=='schedule')return;
  const zoom=Number($('zoom').value),min=Math.max(620,$('timeline').clientWidth-155),usable=min*zoom;
  scale=usable/(result.end*1.04);const width=Math.ceil(usable+155),step=result.end>7200?1800:result.end>3600?900:300;
  positions=new Map();let html='<div class="timeline-inner" style="width:'+width+'px"><div class="ruler"><span class="label">Elapsed production time</span>';
  for(let t=0;t<=result.end;t+=step)html+=`<span class="tick" style="left:${155+t*scale}px">${Math.round(t/60)}m</span>`;
  html+='</div>';let top=34;
  const visible=result.tasks.filter(t=>focus==='all'||t.chapter===Number(focus)||['boot','models','health','cold','stop','join','probe','decode','preflight'].includes(t.id));
  const links=selected?new Set([selected,...getTask(selected).deps,...result.tasks.filter(t=>t.deps.includes(selected)).map(t=>t.id)]):new Set();
  for(const [id,name,note] of LANES){
    const laneTasks=visible.filter(t=>t.lane===id).sort((a,b)=>a.start-b.start),trackEnds=[];let bars='';
    for(const task of laneTasks){
      const left=155+task.start*scale,bw=Math.max(8,task.duration*scale-1);
      let track=trackEnds.findIndex(n=>n<=left-2);if(track<0)track=trackEnds.length;trackEnds[track]=left+bw;
      const y=11+track*32;positions.set(task.id,{x:left,y:top+y,w:bw,h:27});
      const short=task.chapter===0?'P':`C${String(task.chapter).padStart(2,'0')}`;
      bars+=`<button class="task ${task.manual?'manual':''} ${task.id===selected?'selected':''} ${links.has(task.id)?'related':''}" data-id="${esc(task.id)}" data-lane="${id}" style="left:${left}px;top:${y}px;width:${bw}px" aria-label="${esc(task.name+' · '+labelChapter(task.chapter)+' · '+task.id)}" title="${esc(task.id+' | '+task.name+' | '+formatTime(task.start)+' → '+formatTime(task.end)+' | '+sec(task.duration))}">${bw>23?`<em>${short}</em>`:''}${bw>85?esc(task.name):''}</button>`;
    }
    const height=Math.max(53,trackEnds.length*32+20);
    html+=`<div class="lane" style="height:${height}px" data-lane="${id}"><div class="lane-label"><strong>${name}</strong><small>${id==='qc'&&config.qc==='off'?'Vision off · decode stays':note}</small></div>${bars}</div>`;top+=height;
  }
  for(let t=0;t<=result.end;t+=step)html+=`<i class="gridline" style="left:${155+t*scale}px"></i>`;
  let paths='';if(selected&&positions.has(selected)){
    const to=positions.get(selected);
    for(const dep of getTask(selected).deps){const from=positions.get(dep);if(!from)continue;const a=from.x+from.w,b=from.y+13,c=to.x,d=to.y+13;paths+=`<path d="M ${a} ${b} C ${a+18} ${b},${c-18} ${d},${c} ${d}"/>`;}
  }
  html+=`<svg class="dependencies" width="${width}" height="${top}" aria-hidden="true"><g fill="none" stroke="#64826d" stroke-width="1.2" stroke-dasharray="3 3">${paths}</g></svg><i class="playhead" id="playhead" style="left:155px" hidden></i></div>`;
  $('timeline').innerHTML=html;
  for(const button of $('timeline').querySelectorAll('.task')){
    button.addEventListener('click',()=>selectTask(button.dataset.id));
    button.addEventListener('keydown',e=>{
      if(!['ArrowLeft','ArrowRight'].includes(e.key))return;e.preventDefault();const t=getTask(button.dataset.id);moveTask(t.id,t.start+(e.key==='ArrowLeft'?-1:1)*(e.shiftKey?30:1));
      $('timeline').querySelector(`[data-id="${t.id}"]`)?.focus();
    });
    button.addEventListener('pointerdown',beginDrag);
  }
}
function selectTask(id){selected=id;renderInspector();if(view==='schedule')renderTimeline();}
function beginDrag(event){
  if(event.button!==0)return;
  const button=event.currentTarget,id=button.dataset.id,task=getTask(id),startX=event.clientX,scroll=$('timeline').scrollLeft;
  let delta=0,moved=false;button.setPointerCapture(event.pointerId);
  const onMove=e=>{delta=e.clientX-startX+$('timeline').scrollLeft-scroll;if(Math.abs(delta)>4)moved=true;if(moved){button.classList.add('dragging');button.style.transform=`translateX(${delta}px)`;const target=Math.max(0,Math.round(task.start+delta/scale));$('moveFeedback').textContent=`${id} → ${formatTime(target)}. ${explainMove(plan,result,id,target)}`;}};
  const finish=e=>{button.removeEventListener('pointermove',onMove);button.removeEventListener('pointerup',finish);button.removeEventListener('pointercancel',cancel);if(button.hasPointerCapture(event.pointerId))button.releasePointerCapture(event.pointerId);if(moved){e.preventDefault();moveTask(id,Math.max(0,Math.round(task.start+delta/scale)));}else selectTask(id);};
  const cancel=()=>{button.removeEventListener('pointermove',onMove);button.removeEventListener('pointerup',finish);button.removeEventListener('pointercancel',cancel);renderTimeline();};
  button.addEventListener('pointermove',onMove);button.addEventListener('pointerup',finish);button.addEventListener('pointercancel',cancel);
}
function moveTask(id,target){
  if(!Number.isFinite(target)||target>86400){toast('Choose a start within 24 hours.');return;}
  target=Math.max(0,target);const why=explainMove(plan,result,id,target);checkpoint();preferences[id]={...preferences[id],notBefore:target};selected=id;rebuild();const moved=getTask(id);
  $('moveFeedback').textContent=`${id}: requested ${formatTime(target)}, scheduled ${formatTime(moved.start)}. ${moved.start>target+.1?why+' Dependencies or capacity pushed it later.':'Dependent tasks were re-scheduled; all resource checks pass.'}`;
}
function renderInspector(){
  const task=selected&&getTask(selected);if(!task)return;
  const meta=CATALOG.find(m=>m.id===task.kind);
  $('inspectorLabel').textContent=task.manual?'MANUAL START':'SCHEDULED';
  const resources=Object.entries(task.resources).map(([r,n])=>`<span class="res-tag">${esc(r)} · ${n} / ${plan.capacities[r]}</span>`).join('')||'<span class="res-tag">No exclusive resource</span>';
  $('taskInspector').innerHTML=`<h3 class="task-title">${esc(task.name)}</h3><div class="task-id">${esc(task.id)} · ${labelChapter(task.chapter)}</div><p class="task-desc">${esc(meta.description)}</p><div class="task-times"><div><span>Starts</span><strong>${formatTime(task.start)}</strong></div><div><span>Ends</span><strong>${formatTime(task.end)}</strong></div><div><span>Work</span><strong>${sec(task.duration)}</strong></div></div><div class="inspect-section"><h3>Resources reserved</h3>${resources}<p style="margin-top:7px">Units are scheduling constraints, not measured CPU utilization.</p></div><div class="inspect-section"><h3>Must finish first · ${task.deps.length}</h3>${task.deps.slice(0,12).map(id=>{const dep=getTask(id);return `<button class="dep" data-select="${esc(id)}">${esc(id)}<br>${esc(dep.name)} · ends ${formatTime(dep.end)}</button>`;}).join('')||'<p>No preceding task.</p>'}${task.deps.length>12?`<p>${task.deps.length-12} more dependencies are in the JSON below and exported plan.</p>`:''}</div><div class="inspect-section"><h3>Can run alongside</h3><p>${esc(meta.parallel)}</p></div><div class="inspect-section"><h3>Timing evidence</h3><p>${esc(task.basis||meta.basis)}</p>${task.images?`<p>${task.images} ${task.workers?task.workers+' worker':'sequential'} image${task.images===1?'':'s'} in this group.</p>`:''}<p>GPU rental bills continuously from Boot through Stop, including idle gaps.</p></div><div class="task-fields"><label>Requested start · seconds<input id="taskStart" type="number" min="0" max="86400" step="1" value="${Math.round(task.start)}"></label><button class="button" id="applyMove">Move this task</button><label>Scheduling priority<input id="taskPriority" type="number" min="-1000000" max="1000000" step="1" value="${preferences[task.id]?.priority??task.priority}"><small>Lower values go first among equally ready work. Dependencies still apply.</small></label><button class="button" id="applyPriority">Set priority</button><button class="button" id="clearMove" ${task.manual?'':'disabled'}>Clear manual start</button></div><details class="inspect-section"><summary>All task data</summary><pre style="font:9px/1.5 var(--mono);white-space:pre-wrap;overflow-wrap:anywhere">${esc(JSON.stringify(task,null,2))}</pre></details>`;
  $('applyMove').onclick=()=>moveTask(task.id,Number($('taskStart').value));
  $('applyPriority').onclick=()=>{const value=Number($('taskPriority').value);if(!Number.isFinite(value)||Math.abs(value)>1000000){toast('Priority must be between −1000000 and 1000000.');return;}checkpoint();preferences[task.id]={notBefore:preferences[task.id]?.notBefore||0,priority:value};rebuild();};
  $('clearMove').onclick=()=>{checkpoint();delete preferences[task.id];rebuild();};
  $('taskInspector').querySelectorAll('[data-select]').forEach(b=>b.onclick=()=>selectTask(b.dataset.select));
}
function renderBreakdown(){
  const groups=[['Directing',t=>t.lane==='director'],['Narration',t=>t.lane==='audio'],[plan.measurement?'Measured image pipeline':'Cloud GPU work',t=>t.resources.remote],['Preparation & transfer',t=>['prepare','transfer'].includes(t.lane)],['Vision QC',t=>t.lane==='qc'],['Save & state',t=>t.lane==='save'],['Rendering',t=>t.lane==='render'],['Setup & delivery',t=>['setup','finish'].includes(t.lane)]];
  const vals=groups.map(([label,predicate])=>({label,value:result.tasks.filter(predicate).reduce((n,t)=>n+t.duration,0)})),max=Math.max(...vals.map(v=>v.value));
  $('workBreakdown').innerHTML=vals.map(v=>`<div class="workrow"><span>${v.label}</span><div class="bar"><i style="width:${v.value/max*100}%"></i></div><span class="time">${sec(v.value)}</span></div>`).join('')+'<p class="caption">These are task work totals. Overlap makes their sum exceed elapsed production time. Cold loading belongs to image work. Measured worker groups also include transfer and saving; this is not GPU utilization.</p>';
  $('costBreakdown').innerHTML=[['GPU rental',money(result.gpuUSD)],['Rental time',formatTime(result.rentalSeconds)],['GPU idle/setup time',formatTime(result.gpuIdleSeconds)],['Luna / QC estimate',money(result.apiUSD)],['Retained storage / day',money(config.storageDaily)],['Total estimate',money(result.totalUSD)]].map(([k,v])=>`<div class="costrow"><span>${k}</span><strong>${v}</strong></div>`).join('');
}
function renderProcesses(){
  $('processCount').textContent=CATALOG.length;const query=$('processSearch').value.toLowerCase();
  $('processList').innerHTML=CATALOG.filter(m=>(m.name+' '+m.description+' '+m.basis).toLowerCase().includes(query)).map(m=>{
    const tasks=result.tasks.filter(t=>t.kind===m.id),duration=tasks.reduce((n,t)=>n+t.duration,0);
    const index=CATALOG.indexOf(m)+1;
    return `<div class="process-row"><span>${String(index).padStart(2,'0')}</span><div><button data-process="${m.id}">${esc(m.name)}</button><p>${esc(m.description)}</p></div><div class="resource">${esc(LANES.find(l=>l[0]===m.lane)[1])}<p>${tasks.length?sec(duration)+' work · '+tasks.length+' tasks':'Nested / conditional'}</p></div><span class="basis">${['introPlan','vision','decode','repair','retryJSON'].includes(m.id)?'Optional / conditional':tasks.length?'Scheduled parent':'Nested, not extra work'}</span></div>`;
  }).join('')||'<div class="inline-note">No processes match. Try “voice”, “reference” or “render”.</div>';
  $('processList').querySelectorAll('[data-process]').forEach(b=>b.onclick=()=>{
    const m=CATALOG.find(x=>x.id===b.dataset.process),task=result.tasks.find(t=>t.kind===m.id);
    if(task){selectTask(task.id);}else{
      $('inspectorLabel').textContent='NESTED / CONDITIONAL';$('taskInspector').innerHTML=`<h3 class="task-title">${esc(m.name)}</h3><p class="task-desc">${esc(m.description)}</p><div class="inspect-section"><h3>Requires</h3><p>${esc(m.requires)}</p></div><div class="inspect-section"><h3>Parallelism</h3><p>${esc(m.parallel)}</p></div><div class="inspect-section"><h3>Accounting</h3><p>${esc(m.basis)}</p></div>`;
    }
  });
}
function renderPairs(){
  const options=result.tasks.map(t=>`<option value="${t.id}">${esc(t.id+' · '+t.name)}</option>`).join(''),a=$('pairA').value,b=$('pairB').value;
  $('pairA').innerHTML=options;$('pairB').innerHTML=options;
  $('pairA').value=getTask(a)?a:(result.tasks.find(t=>t.kind==='prompt'&&t.chapter===2)||result.tasks[0]).id;
  $('pairB').value=getTask(b)?b:(result.tasks.find(t=>t.kind==='infer'&&t.chapter===1)||result.tasks.at(-1)).id;
  pairResult();
}
function dependsOn(task,id,seen=new Set()){
  if(seen.has(task.id))return false;seen.add(task.id);return task.deps.some(d=>d===id||dependsOn(getTask(d),id,seen));
}
function pairResult(){
  const a=getTask($('pairA').value),b=getTask($('pairB').value);if(!a||!b)return;
  if(a.id===b.id){$('pairResult').textContent='This is the same task.';return;}
  if(dependsOn(a,b.id)||dependsOn(b,a.id)){$('pairResult').textContent='Cannot overlap: one task depends on the other, directly or through intermediate stages.';return;}
  const conflicts=Object.keys(a.resources).filter(p=>a.resources[p]+(b.resources[p]||0)>plan.capacities[p]+1e-7);
  $('pairResult').textContent=conflicts.length?'Cannot overlap with these limits: '+conflicts.join(', ')+'. Change a capacity only after measuring throughput and memory.':'The dependency/resource model permits overlap. This is a scheduling result, not proof that contention leaves real throughput unchanged.';
}
function renderGPUs(){
  const choices=gpuChoices(evidence),unavailable=[];
  const comparisons=choices.flatMap(g=>{try{const c=selectGPUConfig(config,evidence,g.id),p=buildPlan(c,evidence),r=schedule(p);return [{g:p.gpu,p,r,c}];}catch{unavailable.push(g);return [];}}),max=Math.max(1,...comparisons.map(x=>x.r.end));
  $('gpuCards').innerHTML=comparisons.map(({g,r})=>`<article class="gpu-card ${config.gpu===g.id?'active':''}"><span class="badge">${g.basis==='concurrency'?'MATCHED WORKER GROUPS':g.basis==='measured'?'MATCHED · 5 WARM SAMPLES':'HISTORICAL · DIFFERENT CONDITIONS'}</span><h3>${esc(g.name)}</h3><span class="caption">${g.vram} GB VRAM · ${esc(g.region)} · ${g.date}</span><div class="specs"><div><strong>${g.meanClient.toFixed(2)}s</strong><small>mean client / job</small></div><div><strong>${money(g.hourly)}</strong><small>rental / hour</small></div><div><strong>${g.server.toFixed(2)}s</strong><small>backend</small></div></div><p>${esc(g.scope)}</p><button class="button ${config.gpu===g.id?'dark':''}" data-gpu="${g.id}">${config.gpu===g.id?'Selected profile':'Use this GPU'}</button></article>`).join('');
  $('gpuComparison').innerHTML=comparisons.map(({g,p,r})=>`<div class="compare-row"><div class="label">${esc(g.name)}<small>${esc(executionLabel(p))}</small></div><div class="track"><i style="width:${r.end/max*100}%;background:${g.id===config.gpu?'#89aa8f':'#bdccba'}"><span>${formatTime(r.end)}</span></i></div><div class="cost">${money(r.totalUSD)}</div></div>`).join('')+'<p class="caption">Each GPU uses its fastest completed compatible measurement, including its tested execution method. Same resolution, conditioning, chapter count, checks and cost assumptions; manual timeline moves are excluded from this comparison. Complete simulated time includes setup and rental waiting, director, local voice, rendering and daily storage. Prices and stock are historical, not live quotes.</p>'+(unavailable.length?`<p class="caption">No compatible completed measurement: ${unavailable.map(g=>esc(g.name)).join(', ')}. These GPUs have no substituted speed estimate.</p>`:'');
  $('gpuCards').querySelectorAll('[data-gpu]').forEach(b=>b.onclick=()=>{const chosen=comparisons.find(x=>x.g.id===b.dataset.gpu);checkpoint();config=chosen.c;preferences={};selected=null;setControls();rebuild();toast('Schedule updated: '+executionLabel(plan));});
}
function renderEvidence(){
  $('evidence').innerHTML=`<div class="evidence-block"><h3>What the matched test controlled</h3><p>${esc(evidence.imageSettings)}. Same prompts, character references and seeds. Each GPU made nine images; five warm landscape reference images calibrate this schedule. One host per GPU, different regions and network conditions.</p><p>Original 96 GB and MIG 48 GB profiles are separate historical runs. They are not normalized into a matched benchmark. Full 6000 detail splits and startup are assumptions.</p></div><div class="evidence-block"><h3>Startup and client stages</h3><div class="evidence-table"><table><thead><tr><th>GPU</th><th>Boot</th><th>Download / hash</th><th>Cold overhead</th><th>Client mean</th><th>Scope</th></tr></thead><tbody>${evidence.gpus.map(g=>`<tr><td>${esc(g.name)}</td><td>${sec(g.boot)}</td><td>${sec(g.download)}</td><td>${sec(g.cold)}</td><td>${sec(g.meanClient)}</td><td>${g.basis==='measured'?'Matched · cold overhead derived':'Historical + allowances'}</td></tr>`).join('')}</tbody></table></div><p>Cold overhead is the cold-server/warm-server difference, not isolated model-loading instrumentation. Download includes hash checking. Warm cache skips download, but first load still remains.</p></div><div class="evidence-block"><h3>No invented correction factor</h3><p>The ±${uncertainty}% range is a sensitivity assumption you can edit. It is not a statistical error bar or confidence interval. A shared matched benchmark would be needed to reliably adjust different old test conditions. Nine images per GPU cannot establish a population-wide error rate.</p></div><div class="evidence-block"><h3>Director, audio and rendering</h3><ul><li>Luna: 471.46s for 1,050.23s narration, 48 confirmed calls. Pass durations here allocate this measured aggregate by explicit weights; individual pass timings are estimates.</li><li>Local narration: 13.47s to generate a 135.515s excerpt; scaled conservatively. Short tests cannot guarantee a two-hour voice result.</li><li>Renderer: 3,459.59s for a two-hour fixture, 700 unique clip records, repeated saved assets, 720p24 and at most two workers. Nested motion, transitions and chapter assembly are included, not billed twice.</li><li>Vision QC: 8.11s mean across 12 calls. Practical and Strict have the same latency estimate here; review decisions and repair frequency can differ.</li><li>Routine preflight, intro planning, cleaning, alignment, timeline and final probe are explicit allowances. Manual review, chapter acquisition, outages, new character sheets and unbounded retries are excluded.</li></ul></div><div class="evidence-block"><h3>Installed constraints versus proposed improvements</h3><ul><li>Installed cloud overlap requires Practical/Strict checks, one image lane, a shared three-slot API pool and bounded shot admission. The provider holds its lane through retrieval; chapter rendering waits for image work.</li><li>Proposed mode separates preparation, upload, inference, polling, download and commits, with at most three in-flight scheduling groups. It overlaps chapter rendering and next-chapter planning. These queue changes are not deployed by this planner.</li><li>Next chapter’s story analysis waits for the preceding planned handoff. Images may arrive later; intended story facts remain authoritative.</li><li>Audio uses the laptop GPU; images use the cloud GPU. They do not compete for the same VRAM. Audio/render CPU overlap is an uncalibrated experiment with potential throughput loss.</li><li>The legacy profile keeps one worker. Measured profiles use the tested worker count inside one delivered-image group. New worker profiles are enabled only from completed matched cohorts. More VRAM alone cannot predict their combined throughput.</li></ul></div><div class="evidence-block"><h3>Costs and retries</h3><p>GPU cost covers Boot → Stop, including downloads and idle gaps. API cost uses historical receipt-based assumptions; daily storage is separate and editable. No free tokens are assumed. Extra image attempts are a configured count allowance, not a measured failure probability. No auto-repair is claimed with checks off. An API repair check or manual review could add more time/cost than this simple allowance.</p></div><div class="evidence-block"><h3>Source records and code</h3><ul><li>2026-10-05-four-gpu: summary, GPU receipts, model hashes and client component timings.</li><li>2026-10-05-three-model-comparison: MIG warm 4B cases.</li><li>cloud-setup/PERFORMANCE-AUDIT.md: original 96 GB completed job timings and price.</li><li>2026-10-03: fresh-director-results and local-voice-overlap-results.</li><li>2026-10-04-two-hour: render-transition-recovery-results.</li><li>2026-10-04-live-comparison: serial QC receipts.</li><li>Backend: studio_overlap.py, studio_execution.py, image_provider.py, studio_service.py and studio_render.py.</li></ul><p>Sanitized calibration is served from pipeline-evidence.json. No private story, portrait, project identifier, credentials or API key is included.</p></div>`;
}
function stopPlay(){playing=false;cancelAnimationFrame(playFrame);$('play').textContent='Play simulation';if($('playhead'))$('playhead').hidden=true;$('timeline').querySelectorAll('.active').forEach(b=>b.classList.remove('active'));}
function tick(now){
  if(!playing)return;playAt=(now-playStarted)/35000*result.end;
  if(playAt>result.end){stopPlay();$('playTime').textContent='Simulation complete';return;}
  $('playTime').textContent=formatTime(playAt)+' simulated';const active=result.tasks.filter(t=>t.start<=playAt&&t.end>playAt);
  $('activeWork').innerHTML=active.map(t=>`<div class="active-item"><b>${esc(t.id)}</b>${esc(t.name)}</div>`).join('')||'<p class="caption">Waiting for dependencies.</p>';
  const ids=new Set(active.map(t=>t.id));$('timeline').querySelectorAll('.task').forEach(b=>b.classList.toggle('active',ids.has(b.dataset.id)));
  if($('playhead')){$('playhead').hidden=false;$('playhead').style.left=(155+playAt*scale)+'px';}
  playFrame=requestAnimationFrame(tick);
}
function download(name,value,type){const blob=new Blob([value],{type}),url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function start(){
  try{
    const response=await fetch('./pipeline-evidence.json');if(!response.ok)throw Error('Calibration could not load. Open through the local preview or website server.');evidence=await response.json();
    const concurrencyResponse=await fetch('./pipeline-concurrency.json');
    if(concurrencyResponse.ok){
      evidence.concurrency=await concurrencyResponse.json();
      mountGPUExplorer($('gpuExplorer'),evidence.concurrency,settings=>{
        const next={...config,...settings,policy:config.policy==='serial'?'serial':'proposed'};
        try{buildPlan(next,evidence);checkpoint();config=next;preferences={};selected=null;setControls();rebuild();switchView('schedule');toast('Exact measured GPU configuration applied. Production settings remain unchanged.');}catch(error){toast(error.message);}
      });
      mountConcurrencyLab($('concurrencyLab'),evidence.concurrency,settings=>{
        const next={...config,...settings,policy:config.policy==='serial'?'serial':'proposed'};
        try{buildPlan(next,evidence);checkpoint();config=next;preferences={};selected=null;setControls();rebuild();switchView('schedule');toast('Measured throughput applied to the planning schedule. Production settings remain unchanged.');}catch(error){toast(error.message);}
      });
    }
    const choices=gpuChoices(evidence);
    $('gpu').innerHTML=choices.map(g=>`<option value="${g.id}">${esc(g.name)}</option>`).join('');
    const measuredDefault={...DEFAULTS,gpu:'5090',resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',measurementAttempt:'5090-attempt-8'};
    try{config=selectGPUConfig(measuredDefault,evidence,measuredDefault.gpu);}catch{config={...DEFAULTS};}
    const resetDefault={...config};
    try{const saved=JSON.parse(localStorage.getItem(STORE)||'null');if(saved){const state=importSnapshot(saved,evidence);config=state.config;preferences=state.preferences;uncertainty=Number.isFinite(saved.uncertainty)?Math.max(0,Math.min(100,saved.uncertainty)):20;}}catch{toast('Saved planner settings were invalid; using the default plan. Studio projects are unaffected.');}
    setControls();rebuild();
    for(const k of [...keys,'uncertainty'])if($(k))$(k).addEventListener('change',()=>{
      let next=readControls();if(['gpu','resolution','encodingProfile','executionMode'].includes(k))next.measurementAttempt='latest';
      // Never silently change the user's check level to satisfy installed overlap.
      if(next.policy==='current'&&!['practical','strict'].includes(next.qc)){toast('Installed overlap needs Practical/Strict checks. Choose those checks, or use Proposed mode.');setControls();return;}
      try{if(['gpu','resolution','encodingProfile'].includes(k))next=selectGPUConfig(next,evidence,next.gpu);buildPlan(next,evidence);const u=Number($('uncertainty').value);if(!Number.isFinite(u)||u<0||u>100)throw Error('Sensitivity must be 0–100%.');checkpoint();config=next;uncertainty=u;preferences={};selected=null;setControls();rebuild();if(['gpu','resolution','encodingProfile'].includes(k))toast('Automatically selected fastest tested settings: '+executionLabel(plan));}catch(e){toast(e.message);setControls();}
    });
    $('fastestGPU').onclick=()=>{try{const next=selectGPUConfig(config,evidence,config.gpu);checkpoint();config=next;preferences={};selected=null;setControls();rebuild();toast('Fastest tested settings applied: '+executionLabel(plan));}catch(e){toast(e.message);}};
    document.querySelectorAll('[data-view]').forEach((b,i)=>{b.onclick=()=>switchView(b.dataset.view);b.onkeydown=e=>{const tabs=[...document.querySelectorAll('[data-view]')];const next=e.key==='ArrowRight'?tabs[(i+1)%tabs.length]:e.key==='ArrowLeft'?tabs[(i+tabs.length-1)%tabs.length]:null;if(next){e.preventDefault();next.focus();switchView(next.dataset.view);}};});
    $('chapterFocus').onchange=()=>{focus=$('chapterFocus').value;renderTimeline();};$('zoom').onchange=renderTimeline;
    $('processSearch').oninput=renderProcesses;$('pairA').onchange=pairResult;$('pairB').onchange=pairResult;
    $('undo').onclick=()=>{const last=history.pop();if(!last)return;config=last.config;preferences=last.preferences;uncertainty=last.uncertainty;setControls();rebuild();$('undo').disabled=!history.length;toast('Previous plan restored.');};
    $('optimize').onclick=()=>{checkpoint();preferences={};rebuild();$('moveFeedback').textContent='Tasks repacked at their earliest available dependency/resource slot. This is a feasible heuristic, not a proof of the global optimum.';};
    $('reset').onclick=()=>{checkpoint();config={...resetDefault};preferences={};uncertainty=20;selected=null;setControls();rebuild();toast('Default planning scenario restored with fastest tested settings.');};
    $('play').onclick=()=>{if(playing){stopPlay();return;}if(matchMedia('(prefers-reduced-motion: reduce)').matches){toast('Animation disabled by your reduced-motion setting. Task details remain available.');return;}playing=true;playStarted=performance.now();$('play').textContent='Stop simulation';playFrame=requestAnimationFrame(tick);};
    $('export').onclick=()=>{
      const value={...snapshot(),exportedAt:new Date().toISOString(),evidenceVersion:evidence.version,execution:'planning-only',summary:{elapsedSeconds:result.end,rentalSeconds:result.rentalSeconds,imageCount:plan.imageCount,attemptCount:plan.attemptCount,gpuUSD:result.gpuUSD,apiUSD:result.apiUSD,totalUSD:result.totalUSD},capacities:plan.capacities,tasks:result.tasks,calibration:evidence,notes:evidence.notes};
      download(`studio-plan-${config.gpu}-${config.minutes}min.json`,JSON.stringify(value,null,2),'application/json');toast('Exported schedule, task IDs, dependencies, resource limits and evidence.');
    };
    $('import').onclick=()=>$('importFile').click();$('importFile').onchange=async()=>{const file=$('importFile').files[0];if(!file)return;try{if(file.size>16_000_000)throw Error('Plan export must be smaller than 16 MB.');const input=JSON.parse(await file.text()),state=importSnapshot(input,evidence);checkpoint();config=state.config;preferences=state.preferences;uncertainty=Number.isFinite(input.uncertainty)?Math.max(0,Math.min(100,input.uncertainty)):20;selected=null;setControls();rebuild();toast('Plan imported. Paid execution and Studio projects remain unchanged.');}catch(e){toast('Could not import: '+e.message);}finally{$('importFile').value='';}};
    let resizeTimer;new ResizeObserver(()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(renderTimeline,120);}).observe($('timeline'));
    try {
      const response=await fetch('./pipeline-serverless.json');
      if(response.ok){const data=await response.json();const panel=document.createElement('section');panel.id='serverlessLab';panel.innerHTML=serverlessHTML(data);$('concurrencyLab').insertAdjacentElement('afterend',panel);}
    } catch { /* Optional pilot evidence never blocks the existing planner. */ }
    try {
      const response=await fetch('./pipeline-matched.json');
      if(response.ok){const data=await response.json();const panel=document.createElement('section');panel.id='matchedLab';panel.innerHTML=matchedHTML(data,evidence);($('serverlessLab')||$('concurrencyLab')).insertAdjacentElement('afterend',panel);}
    } catch { /* Incomplete matched measurements never substitute a speed estimate. */ }
    try {
      const detailResponse=await fetch('./pipeline-details.json');
      if(!detailResponse.ok)throw Error('Optional source inventory unavailable');
      const details=await detailResponse.json();mountObservedRuns($('observedRuns'),details);mountFunctionIndex($('functionIndex'),details,kind=>{const task=result.tasks.find(t=>t.kind===kind);if(task){selected=task.id;renderInspector();}else{const meta=CATALOG.find(t=>t.id===kind);if(meta){$('inspectorLabel').textContent='NESTED / CONDITIONAL';$('taskInspector').innerHTML=`<h3>${esc(meta.name)}</h3><p class="task-desc">${esc(meta.description)}</p><p>${esc(meta.basis)}</p><p>${esc(meta.parallel)}</p>`;}}});
    } catch {
      $('functionIndex').textContent='Source inventory unavailable. Process-level explanations remain available.';
      $('observedRuns').textContent='Recorded timing file unavailable; no trace is substituted.';
    }
  }catch(e){$('error').textContent=e.message;$('error').hidden=false;$('saved').textContent='Planner could not initialize';}
}
start();
