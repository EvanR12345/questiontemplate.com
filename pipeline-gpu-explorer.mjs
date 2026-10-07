import {buildPlan,schedule} from './pipeline-engine.mjs';
const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const missing=value=>value===null||value===undefined||value===''||(typeof value==='number'&&!Number.isFinite(value));
const GROUPS={profiles:'resident',productionProfiles:'resident',pipelineProfiles:'pipeline',hybridProfiles:'hybrid'};
const LABELS={gpu:'GPU',vram:'VRAM · GB',coverage:'Test coverage',resolution:'Resolution',conditioning:'Conditioning',method:'Execution method',copies:'Model copies',slots:'Request slots',region:'Test region',stock:'Stock at test',rate:'Images / min',warmCost:'Warm $ / 1,000 images',hourly:'Measured rental $ / hour',quote:'Secure quote $ / hour',community:'Community quote $ / hour',startup:'Boot / readiness · s',download:'Download / verify · s',cold:'First warmup client · s',client:'Mean client latency · s',server:'Mean server latency · s',rounds:'Completed rounds',images:'Test images',date:'Test date',attempt:'Rental attempt',gpuStatus:'GPU test status',catalogRegions:'Catalogue regions / stock'};
Object.assign(LABELS,{projectRendering:'Rendering · min work (est.)',projectGPUCost:'GPU rental · $ (est.)',projectRental:'Rental · min (est.)',projectIdle:'GPU idle/setup · min (est.)'});
const PROJECT_FIELDS=new Set(['projectRendering','projectGPUCost','projectRental','projectIdle']);
export const EXPLORER_DEFAULTS={view:'best',resolution:'720p',conditioning:'fresh',method:'all',coverage:'all',search:'',region:'all',minVRAM:'',maxHourly:'',includeControls:false,sort:[{field:'rate',direction:'desc'}],rules:[],columns:['gpu','rate','warmCost','hourly','vram','region','method','copies','slots','coverage'],pageSize:10};
const presets={
  performance:['gpu','rate','warmCost','hourly','vram','region','method','copies','slots','coverage'],
  project:['gpu','rate','projectRendering','projectGPUCost','projectRental','projectIdle','resolution','method','copies','slots','coverage'],
  startup:['gpu','region','stock','startup','download','cold','hardware.cudaVariant','hardware.torch','hardware.comfy','hardware.hostRAMBytes','coverage'],
  evidence:['gpu','resolution','conditioning','method','copies','slots','rounds','images','date','attempt','coverage']
};
function flatten(object,prefix,out){
  for(const [key,value] of Object.entries(object||{})){
    if(key==='traces')continue; // Per-image traces remain in the existing accounting inspector.
    const path=prefix+'.'+key;
    if(value&&typeof value==='object'&&!Array.isArray(value))flatten(value,path,out);
    else out[path]=Array.isArray(value)?JSON.stringify(value):value;
  }
}
export function explorerRows(data){
  return data.gpus.flatMap(g=>{
    const common={gpu:g.name,gpuId:g.id,vram:g.vramGB,gpuStatus:g.status,model:data.model,modelSettings:data.settings,experimentRentalCeilingUSD:g.rentalUpperUSD,quote:g.catalog?.secureHourlyUSD,community:g.catalog?.communityHourlyUSD,catalogRegions:(g.catalog?.availableRegions||[]).map(r=>`${r.dataCenterId}: ${r.stockStatus}`).join(', ')};
    flatten(g.catalog,'catalog',common);
    const profiles=Object.entries(GROUPS).flatMap(([key,mode])=>(g[key]||[]).map((p,i)=>({p,mode,key,i})));
    if(!profiles.length)return [{...common,id:g.id+':catalog',coverage:'Unmeasured',region:g.preferredRegion||null,profile:null,hardware:null}];
    return profiles.map(({p,mode,key,i})=>{
      const h=g.hardware?.find(h=>h.attempt===p.attempt)||null;
      const completed=p.rounds>=2&&!p.postControl&&!!h;
      const row={...common,id:`${g.id}:${key}:${i}`,profile:p,hardware:h,coverage:p.postControl?'Control':completed?'Completed':'Incomplete',resolution:p.resolution,conditioning:p.encodingProfile||'cached',method:mode,copies:p.workers,slots:p.clientSlots||p.workers,region:p.region||h?.region||null,stock:h?.stockAtSelectedRegion||null,rate:Number.isFinite(p.imagesPerSecond)?p.imagesPerSecond*60:null,warmCost:Number.isFinite(p.warmUSDPerImage)?p.warmUSDPerImage*1000:null,hourly:h?.computeContainerHourlyUSD??h?.hourlyUSD??null,startup:h?.startupSeconds??null,download:h?.downloadHashSeconds??null,cold:h?.firstWarmupClientSeconds??null,client:p.meanClientLatencySeconds,server:p.meanServerLatencySeconds,rounds:p.rounds,images:p.images,date:p.runStartedAt??h?.runStartedAt??null,attempt:p.attempt};
      flatten(p,'test',row);flatten(h,'hardware',row);
      const size=data.resolutions?.[p.resolution];row.generatedWidth=size?.[0];row.generatedHeight=size?.[1];
      return row;
    });
  });
}
export function explorerFields(rows){
  const internal=new Set(['id','gpuId','profile','hardware']);
  return [...new Set([...Object.keys(LABELS),...rows.flatMap(Object.keys)])].filter(k=>!internal.has(k)).map(key=>({key,label:LABELS[key]||key.replace(/^(test|hardware|catalog)\./,(_,group)=>group+' / ').replace(/\./g,' / ')}));
}
export function compareValues(a,b,direction='asc'){
  if(missing(a)||missing(b))return missing(a)?(missing(b)?0:1):-1;
  const delta=typeof a==='number'&&typeof b==='number'?a-b:String(a).localeCompare(String(b),undefined,{numeric:true,sensitivity:'base'});
  return direction==='desc'?-delta:delta;
}
function matchesRule(row,rule){
  const value=row[rule.field];
  if(rule.op==='missing')return missing(value);
  if(rule.op==='exists')return !missing(value);
  if(missing(value))return false;
  const text=String(value).toLowerCase(),wanted=String(rule.value??'').toLowerCase();
  if(rule.op==='contains')return text.includes(wanted);
  if(rule.op==='equals')return text===wanted;
  if(rule.op==='notEqual')return text!==wanted;
  if(wanted.trim()===''||!Number.isFinite(Number(value))||!Number.isFinite(Number(wanted)))return false;
  return rule.op==='gte'?Number(value)>=Number(wanted):rule.op==='lte'?Number(value)<=Number(wanted):false;
}
export function projectMetrics(row,config,evidence){
  if(row.coverage!=='Completed'||!config||!evidence)return {};
  try{
    const p=buildPlan({...config,gpu:row.gpuId,resolution:row.resolution,encodingProfile:row.conditioning,executionMode:row.method,imageWorkers:String(row.method==='resident'?row.copies:row.slots),measurementAttempt:row.attempt,policy:config.policy==='serial'?'serial':'proposed'},evidence),r=schedule(p);
    return {projectRendering:r.tasks.filter(t=>t.lane==='render').reduce((sum,t)=>sum+t.duration,0)/60,projectGPUCost:r.gpuUSD,projectRental:r.rentalSeconds/60,projectIdle:r.gpuIdleSeconds/60};
  }catch{return {};}
}
export function queryExplorer(rows,state,metrics){
  const eligible=row=>state.includeControls||!['Control','Incomplete'].includes(row.coverage);
  const filtered=rows.filter(row=>eligible(row)
    &&(row.coverage==='Unmeasured'||state.resolution==='all'||row.resolution===state.resolution)
    &&(row.coverage==='Unmeasured'||state.conditioning==='all'||row.conditioning===state.conditioning)
    &&(row.coverage==='Unmeasured'||state.method==='all'||row.method===state.method)
    &&(state.coverage==='all'||row.coverage.toLowerCase()===state.coverage)
    &&(!state.search||[row.gpu,row.attempt,row.region,row.catalogRegions].some(v=>String(v||'').toLowerCase().includes(state.search.toLowerCase())))
    &&(state.region==='all'||row.region===state.region)
    &&(state.minVRAM===''||(!missing(row.vram)&&row.vram>=Number(state.minVRAM)))
    &&(state.maxHourly===''||(!missing(row.hourly??row.quote)&&(row.hourly??row.quote)<=Number(state.maxHourly)))
    &&state.rules.every(rule=>matchesRule(PROJECT_FIELDS.has(rule.field)&&metrics?{...row,...metrics(row)}:row,rule)));
  const groups=new Map();for(const row of filtered){if(!groups.has(row.gpuId))groups.set(row.gpuId,[]);groups.get(row.gpuId).push(row);}
  const selected=state.view==='tests'?filtered:[...groups.values()].map(group=>group.reduce((a,b)=>
    // "Best" always means highest completed throughput, independent of table sort.
    a.coverage==='Completed'&&b.coverage!=='Completed'?a:b.coverage==='Completed'&&a.coverage!=='Completed'?b:compareValues(a.rate,b.rate,'desc')<=0?a:b));
  return selected.map(row=>metrics?{...row,...metrics(row)}:row).sort((a,b)=>{
    for(const rule of state.sort){const delta=compareValues(a[rule.field],b[rule.field],rule.direction);if(delta)return delta;}
    return a.id.localeCompare(b.id);
  });
}
export function explorerCSV(rows,fields){
  const cell=value=>{let text=missing(value)?'':String(value);if(typeof value==='string'&&/^[=+@\-\t\r]/.test(text))text="'"+text;return '"'+text.replace(/"/g,'""')+'"';};
  return [fields.map(f=>cell(f.label)).join(','),...rows.map(row=>fields.map(f=>cell(row[f.key])).join(','))].join('\r\n');
}
function formatted(value,key){
  if(missing(value))return 'Not measured / recorded';
  if(key==='date'||/At$/.test(key)||/runStartedAt$/.test(key)){if(typeof value==='number')return new Date(value*1000).toISOString().replace('T',' ').slice(0,19)+' UTC';}
  if(typeof value==='boolean')return value?'Yes':'No';
  if(typeof value==='number')return Number.isInteger(value)?String(value):value.toFixed(key==='warmCost'?3:2);
  const text=String(value);return text.length>100?text.slice(0,97)+'…':text;
}
export function mountGPUExplorer(host,data,onApply){
  const rows=explorerRows(data),fields=explorerFields(rows),byKey=new Map(fields.map(f=>[f.key,f])),STORE='questiontemplate-gpu-explorer-v1';
  let state=structuredClone(EXPLORER_DEFAULTS),page=0,projectConfig,projectEvidence,projectKey='',projectCache=new Map();
  const metrics=row=>{if(!projectCache.has(row.id))projectCache.set(row.id,projectMetrics(row,projectConfig,projectEvidence));return projectCache.get(row.id);};
  // Compute schedules only when projected fields are displayed, filtered or sorted.
  const matches=()=>queryExplorer(rows,state,[...state.columns,...state.sort.map(r=>r.field),...state.rules.map(r=>r.field)].some(k=>PROJECT_FIELDS.has(k))?metrics:undefined);
  try{const saved=JSON.parse(localStorage.getItem(STORE)||'null');if(saved&&Array.isArray(saved.sort)&&Array.isArray(saved.rules)&&Array.isArray(saved.columns)){
    state={...state,...saved,columns:saved.columns.filter(k=>byKey.has(k)),sort:saved.sort.filter(r=>r&&byKey.has(r.field)&&['asc','desc'].includes(r.direction)).slice(0,6),rules:saved.rules.filter(r=>r&&byKey.has(r.field)&&['exists','missing','contains','equals','notEqual','gte','lte'].includes(r.op)).slice(0,12)};
    for(const [key,valid] of Object.entries({view:['best','tests'],resolution:['all','720p','1080p'],conditioning:['all','fresh','cached'],method:['all','resident','pipeline','hybrid'],coverage:['all','completed','unmeasured','incomplete','control']}))if(!valid.includes(state[key]))state[key]=EXPLORER_DEFAULTS[key];
    if(![10,25,50].includes(state.pageSize))state.pageSize=10;
    state.search=typeof state.search==='string'?state.search:'';state.region=rows.some(r=>r.region===state.region)?state.region:'all';state.includeControls=state.includeControls===true;
    for(const key of ['minVRAM','maxHourly'])if(state[key]!==''&&(state[key]===null||!['string','number'].includes(typeof state[key])||!Number.isFinite(Number(state[key]))||Number(state[key])<0))state[key]='';
    if(!state.columns.length)state.columns=[...EXPLORER_DEFAULTS.columns];if(!state.sort.length)state.sort=structuredClone(EXPLORER_DEFAULTS.sort);
  }}catch{ /* A malformed explorer preference never affects Studio or the planner. */ }
  const select=(attr,options,value)=>`<select ${attr}>${options.map(([v,label])=>`<option value="${esc(v)}" ${String(value)===String(v)?'selected':''}>${esc(label)}</option>`).join('')}</select>`;
  const fieldSelect=(attr,value)=>select(attr,fields.map(f=>[f.key,f.label]),value);
  host.innerHTML=`<div class="schedulebar"><div><h2>GPU explorer</h2><p class="caption">Sort every recorded setting. Compare GPUs or individual test configurations.</p></div><button class="button" data-reset>Reset explorer</button></div>
    <div class="explorer-controls"><label>Search GPU / host / region<input type="search" data-filter="search" placeholder="5090, Blackwell, EU…" value="${esc(state.search)}"></label>
    <label>Compare${select('data-filter="view"',[['best','Best tested result per GPU'],['tests','Every test configuration']],state.view)}</label>
    <label>Resolution${select('data-filter="resolution"',[['720p','720p'],['1080p','1080p'],['all','All resolutions']],state.resolution)}</label></div><details class="explorer-advanced"><summary>More GPU filters</summary><div class="explorer-controls">
    <label>Conditioning${select('data-filter="conditioning"',[['fresh','Fresh prompt + references'],['cached','Reused conditioning · control'],['all','All conditioning']],state.conditioning)}</label>
    <label>Execution${select('data-filter="method"',[['all','All methods'],['resident','Independent model copies'],['pipeline','One copy · pipelined requests'],['hybrid','Copies + buffered requests']],state.method)}</label>
    <label>Coverage${select('data-filter="coverage"',[['all','All GPUs'],['completed','Completed measurements'],['unmeasured','Unmeasured candidates'],['incomplete','Incomplete tests'],['control','Post-control tests']],state.coverage)}</label>
    <label>Recorded test region${select('data-filter="region"',[['all','All regions'],...[...new Set(rows.map(r=>r.region).filter(Boolean))].sort().map(r=>[r,r])],state.region)}</label>
    <label>Minimum VRAM · GB<input type="number" min="0" data-filter="minVRAM" value="${esc(state.minVRAM)}"></label>
    <label>Maximum rental / quote · $/h<input type="number" min="0" step="0.01" data-filter="maxHourly" value="${esc(state.maxHourly)}"></label></div>
    <label class="explorer-check"><input type="checkbox" data-controls ${state.includeControls?'checked':''}> Include incomplete and post-control tests</label>
    </details>
    <div class="explorer-rule-header"><h3>Sort order</h3><button class="button" data-add-sort>Add tie-breaker</button></div><div data-sorts></div>
    <details class="explorer-advanced"><summary>Filters for any recorded field</summary><p class="caption">All conditions must match. Missing values never become zero.</p><div data-rules></div><button class="button" data-add-rule>Add field filter</button></details>
    <details class="explorer-advanced"><summary>Choose columns · <span data-column-count></span> fields available</summary><div class="explorer-presets"><button class="button" data-preset="performance">Speed & cost</button><button class="button" data-preset="startup">Startup & host</button><button class="button" data-preset="evidence">Test evidence</button><button class="button" data-all-columns>All recorded fields</button></div><div class="explorer-columns">${fields.map(f=>`<label><input type="checkbox" data-column="${esc(f.key)}" ${state.columns.includes(f.key)?'checked':''}> ${esc(f.label)}</label>`).join('')}</div></details>
    <p class="caption" data-project-scope></p><div class="explorer-rule-header"><p data-count role="status"></p><button class="button" data-preset="project">Rental & rendering</button><button class="button" data-csv>Export filtered CSV</button></div>
    <div class="evidence-table explorer-table" tabindex="0" aria-label="Sortable GPU measurements"><table><thead></thead><tbody></tbody></table></div>
    <div class="explorer-paging"><button class="button" data-prev>Previous</button><span data-page></span><button class="button" data-next>Next</button><label>Rows per page${select('data-size',[[10,'10'],[25,'25'],[50,'50']],state.pageSize)}</label></div>
    <p class="caption">Rates cover warm image delivery; startup, API, narration, reviews and rendering are separate. Best tested is not a hardware maximum. Each row retains its own host, location and settings. Catalogue price/stock are historical snapshots, not live availability. Untested candidates have no inferred speed. Raw per-image traces remain in the benchmark accounting section.</p><section data-inspector hidden class="explorer-inspector"></section>`;
  const $=selector=>host.querySelector(selector),save=()=>{try{localStorage.setItem(STORE,JSON.stringify(state));}catch{}};
  const drawRules=()=>{
    $('[data-sorts]').innerHTML=state.sort.map((s,i)=>`<div class="explorer-rule"><label>${i+1}. Sort by${fieldSelect(`data-sort-field="${i}"`,s.field)}</label><label>Direction${select(`data-sort-direction="${i}"`,[['desc','High → low / Z → A'],['asc','Low → high / A → Z']],s.direction)}</label><button class="button" data-remove-sort="${i}" aria-label="Remove sort ${i+1}" ${state.sort.length===1?'disabled':''}>Remove</button></div>`).join('');
    $('[data-add-sort]').disabled=state.sort.length>=6;
    $('[data-rules]').innerHTML=state.rules.map((r,i)=>`<div class="explorer-rule filter-rule"><label>Field${fieldSelect(`data-rule-field="${i}"`,r.field)}</label><label>Condition${select(`data-rule-op="${i}"`,[['gte','At least ≥'],['lte','At most ≤'],['equals','Equals'],['notEqual','Does not equal'],['contains','Contains text'],['exists','Has a value'],['missing','Missing value']],r.op)}</label><label>Value<input data-rule-value="${i}" value="${esc(r.value)}" ${['exists','missing'].includes(r.op)?'disabled':''}></label><button class="button" data-remove-rule="${i}" aria-label="Remove field filter ${i+1}">Remove</button></div>`).join('');
    $('[data-add-rule]').disabled=state.rules.length>=12;
    for(const [attr,key] of [['sort-field','field'],['sort-direction','direction'],['rule-field','field'],['rule-op','op'],['rule-value','value']])host.querySelectorAll(`[data-${attr}]`).forEach(el=>el.onchange=()=>{const list=attr.startsWith('sort')?state.sort:state.rules;list[Number(el.dataset[attr.replace(/-([a-z])/g,(_,c)=>c.toUpperCase())])][key]=el.value;page=0;if(attr==='rule-op')drawRules();render();});
    host.querySelectorAll('[data-remove-sort]').forEach(el=>el.onclick=()=>{state.sort.splice(Number(el.dataset.removeSort),1);page=0;drawRules();render();});
    host.querySelectorAll('[data-remove-rule]').forEach(el=>el.onclick=()=>{state.rules.splice(Number(el.dataset.removeRule),1);page=0;drawRules();render();});
  };
  const inspect=row=>{
    const panel=$('[data-inspector]');panel.hidden=false;
    panel.innerHTML=`<h3>${esc(row.gpu)}</h3><p>${esc(row.coverage)} · ${esc(row.attempt||'Catalogue candidate')} · ${esc(row.region||'No confirmed region')}</p>${row.coverage==='Completed'&&onApply?'<button class="button dark" data-use>Use this exact tested configuration</button>':''}<div class="evidence-table"><table><tbody>${fields.filter(f=>!missing(row[f.key])).map(f=>`<tr><th>${esc(f.label)}</th><td>${esc(formatted(row[f.key],f.key))}</td></tr>`).join('')}</tbody></table></div>`;
    if($('[data-use]'))$('[data-use]').onclick=()=>onApply({gpu:row.gpuId,resolution:row.resolution,encodingProfile:row.conditioning,executionMode:row.method,imageWorkers:String(row.method==='resident'?row.copies:row.slots),measurementAttempt:row.attempt});
  };
  function render(){
    const results=matches(),pages=Math.max(1,Math.ceil(results.length/state.pageSize));page=Math.min(page,pages-1);
    const columns=state.columns.map(k=>byKey.get(k)).filter(Boolean);
    $('[data-count]').textContent=`${results.length} ${state.view==='best'?'GPUs':'test configurations'} · ${fields.length} sortable fields · missing values last`;
    $('[data-project-scope]').textContent=projectConfig?`Project estimates: ${projectConfig.minutes}-minute video · ${projectConfig.chapters} chapters · ${projectConfig.policy==='serial'?'sequential':'proposed overlap'} · current narration, checks, intro and retry settings. Rental covers Boot → Stop, including idle/setup. Rendering is local 720p/24fps summed work, not elapsed finish time. Manual timeline moves are excluded; unmeasured configurations have no estimate.`:'Project estimates load with the planner settings.';
    $('[data-column-count]').textContent=fields.length;
    $('thead').innerHTML='<tr>'+columns.map(f=>{const rank=state.sort.findIndex(s=>s.field===f.key),dir=state.sort[rank]?.direction;return `<th scope="col" ${rank===0?`aria-sort="${dir==='asc'?'ascending':'descending'}"`:''}><button data-header="${esc(f.key)}">${esc(f.label)}${rank>=0?` ${dir==='asc'?'↑':'↓'}${rank+1}`:''}</button></th>`;}).join('')+'<th scope="col">Details</th></tr>';
    $('tbody').innerHTML=results.length?results.slice(page*state.pageSize,(page+1)*state.pageSize).map(row=>`<tr data-row="${esc(row.id)}">${columns.map(f=>`<td title="${esc(missing(row[f.key])?'Not measured / recorded':String(row[f.key]))}">${esc(formatted(row[f.key],f.key))}</td>`).join('')}<td><button class="button" data-inspect="${esc(row.id)}" aria-label="Inspect ${esc(row.gpu)} ${esc(row.attempt||'catalogue')}">Inspect</button></td></tr>`).join(''):`<tr><td colspan="${columns.length+1}">No matching GPU records. Broaden your filters; missing measurements are not substituted.</td></tr>`;
    $('[data-page]').textContent=`Page ${page+1} / ${pages}`;$('[data-prev]').disabled=page===0;$('[data-next]').disabled=page>=pages-1;
    host.querySelectorAll('[data-header]').forEach(b=>b.onclick=()=>{const field=b.dataset.header,old=state.sort.find(s=>s.field===field);state.sort=[{field,direction:old?.direction==='asc'?'desc':'asc'},...state.sort.filter(s=>s.field!==field)].slice(0,6);page=0;drawRules();render();});
    host.querySelectorAll('[data-inspect]').forEach(b=>b.onclick=()=>{const row=results.find(r=>r.id===b.dataset.inspect);inspect({...row,...metrics(row)});});
    $('[data-csv]').disabled=!results.length;
    save();
  }
  host.querySelectorAll('[data-filter]').forEach(el=>{const update=()=>{state[el.dataset.filter]=el.value;if(el.dataset.filter==='coverage'&&['incomplete','control'].includes(el.value)){state.includeControls=true;$('[data-controls]').checked=true;}page=0;render();};el[el.type==='search'?'oninput':'onchange']=update;});
  $('[data-controls]').onchange=e=>{state.includeControls=e.target.checked;page=0;render();};
  $('[data-add-sort]').onclick=()=>{state.sort.push({field:fields.find(f=>!state.sort.some(s=>s.field===f.key))?.key||'gpu',direction:'asc'});drawRules();render();};
  $('[data-add-rule]').onclick=()=>{state.rules.push({field:'vram',op:'gte',value:''});drawRules();render();};
  host.querySelectorAll('[data-column]').forEach(el=>el.onchange=()=>{state.columns=fields.filter(f=>$(`[data-column="${f.key}"]`).checked).map(f=>f.key);if(!state.columns.length){state.columns=['gpu'];$('[data-column="gpu"]').checked=true;}render();});
  const setColumns=columns=>{state.columns=columns.filter(k=>byKey.has(k));host.querySelectorAll('[data-column]').forEach(el=>el.checked=state.columns.includes(el.dataset.column));render();};
  host.querySelectorAll('[data-preset]').forEach(el=>el.onclick=()=>setColumns(presets[el.dataset.preset]));$('[data-all-columns]').onclick=()=>setColumns(fields.map(f=>f.key));
  $('[data-prev]').onclick=()=>{page--;render();};$('[data-next]').onclick=()=>{page++;render();};$('[data-size]').onchange=e=>{state.pageSize=Number(e.target.value);page=0;render();};
  $('[data-reset]').onclick=()=>{state=structuredClone(EXPLORER_DEFAULTS);page=0;host.querySelectorAll('[data-filter]').forEach(el=>el.value=state[el.dataset.filter]);$('[data-controls]').checked=false;$('[data-size]').value=state.pageSize;setColumns(state.columns);drawRules();$('[data-inspector]').hidden=true;render();};
  $('[data-csv]').onclick=()=>{const csv=explorerCSV(matches(),state.columns.map(k=>byKey.get(k)));const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'})),a=document.createElement('a');a.href=url;a.download='studio-gpu-explorer.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
  drawRules();render();
  return {getState:()=>structuredClone(state),getRows:matches,updateProject:(config,evidence)=>{const key=JSON.stringify(config);if(key===projectKey)return;projectKey=key;projectConfig={...config};projectEvidence=evidence;projectCache.clear();$('[data-inspector]').hidden=true;render();}};
}
