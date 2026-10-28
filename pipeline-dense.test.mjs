import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {denseActive,denseCapability,imageRentalPercent} from './pipeline-dense.mjs';
import {plannerSettings,profileSummary} from './studio-planner-profile.mjs';
import {startupCandidates,startupApplyConfig,mountStartupTradeoffs} from './pipeline-detail-ui.mjs';
import {productionModeNote} from './pipeline-config.mjs';
import {selectedStrategy,strategyOptions,strategyDescription} from './studio-generation-plan.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
const capability={version:1,supported:true,eligible:true,generationTasks:5,qcTaskSlots:4,descriptorLimit:8192,maxRepairRounds:3,allPlansFirst:true};
const profile=()=>({version:1,name:'Synthetic',revision:1,generationStrategy:'dense',denseImagePhase:{...capability},
 settings:{unattendedProduction:true,automaticRepair:true,voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',
 video:{width:1280,height:720,fps:30,renderer:'cpu'},image:{width:1280,height:720,model:'flux2-klein-4b',steps:4,guidance:1},
 director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small'}},
 imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},
 phaseOverlap:{audioLookahead:true,chapterRender:true},intro:{enabled:false},estimates:{}});
const config=extra=>({...plannerSettings({...DEFAULTS,minutes:3,chapters:3,cadence:12,batch:6},profile(),evidence).config,...extra});
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-6,`${a} != ${b}`);
const voices=p=>p.tasks.filter(t=>t.kind==='voice').map(({id,duration,resources})=>({id,duration,resources}));
test('known Dense selection is explicit while unrelated fallback and saved choices remain unchanged',()=>{
 assert.equal(selectedStrategy({}),'align');assert.equal(selectedStrategy({generationStrategy:'dense'}),'dense');
 for(const generationStrategy of ['align','fastest','legacy'])assert.equal(selectedStrategy({generationStrategy}),generationStrategy);
 assert.deepEqual(strategyOptions('dense',true),{generationStrategy:'dense',overlap:true});
 assert.throws(()=>strategyOptions('dense',false),/requires cloud/);
 assert.match(strategyDescription('dense',true,2),/one initial image burst/);
 assert.match(strategyDescription('dense',true,2),/existing per-shot retry ceilings/);
});
test('saved Dense maps only a supported eligible capability to Installed all-plan startup',()=>{
 const p=profile(),before=structuredClone(p),r=plannerSettings({...DEFAULTS,readyChapters:1,earlyGpu:true},p,evidence);
 assert.ok(denseCapability(p));assert.ok(denseActive(r.config));assert.equal(r.config.readyChapters,r.config.chapters);
 assert.equal(r.config.earlyGpu,false);assert.equal(r.config.directorTier,'default');assert.equal(r.config.cadence,DEFAULTS.cadence);
 assert.equal(r.config.productionRenderOverlap,true);assert.equal(r.config.productionDenseMaxRepairRounds,3);
 assert.match(r.warnings.join(' '),/no matching complete-production timing/);assert.match(r.warnings.join(' '),/No repair rate is assumed/);
 assert.match(profileSummary(p),/5 generation tasks; 4 independent QC slots/);assert.deepEqual(p,before);
 for(const phase of [undefined,{...capability,version:0},{...capability,eligible:false},{...capability,allPlansFirst:false}]){
  const old={...profile(),denseImagePhase:phase};assert.throws(()=>plannerSettings(DEFAULTS,old,evidence),/updated helper/);
 }
});
test('Dense boot follows every plan and intro, with no cycle under either audio-lookahead setting',()=>{
 for(const productionAudioLookahead of [false,true]){
  const c=config({intro:true,introSeconds:20,introImages:3,productionAudioLookahead}),p=buildPlan(c,evidence),r=schedule(p);
  assert.deepEqual(r.diagnostics,[]);const ready=p.tasks.find(t=>t.id==='dense-ready');
  for(let i=1;i<=c.chapters;i++)assert.ok(ready.deps.includes(`c${i}-handoff`));
  assert.ok(ready.deps.includes('intro-plan'));assert.ok(ready.deps.includes('intro-voice'));
  assert.deepEqual(p.tasks.find(t=>t.id==='boot').deps,['dense-ready']);
  assert.ok(!p.tasks.find(t=>t.id==='c3-voice').deps.some(id=>/vision|infer|save/.test(id)));
 }
 assert.throws(()=>buildPlan(config({earlyGpu:true}),evidence),/every chapter plan/);
 assert.throws(()=>buildPlan(config({readyChapters:1}),evidence),/every chapter plan/);
});
test('slow API reviews never create a gap in the ready initial GPU burst or delay its shutdown',()=>{
 const p=buildPlan(config({qcSeconds:200}),evidence),r=schedule(p);
 const images=r.tasks.filter(t=>t.kind==='delivered').sort((a,b)=>a.start-b.start);
 assert.ok(images.length>2);for(let i=1;i<images.length;i++)near(images[i].start,images[i-1].end);
 const stop=r.tasks.find(t=>t.id==='stop'),lastImage=Math.max(...images.map(t=>t.end)),lastQC=Math.max(...r.tasks.filter(t=>t.kind==='vision').map(t=>t.end));
 near(stop.start,lastImage);assert.ok(stop.end<lastQC);assert.ok(r.end>lastQC);
 assert.ok(p.tasks.find(t=>t.id==='stop').deps.every(id=>!id.includes('vision')));
 assert.deepEqual(r.diagnostics,[]);assert.equal(r.qcCount,p.imageCount);
 assert.equal(r.gpuWork,images.reduce((n,t)=>n+t.duration,0));assert.ok(r.gpuWork<r.rentalSeconds);
 assert.notEqual(imageRentalPercent(r),'100.0%');assert.notEqual(imageRentalPercent({gpuWork:999999,rentalSeconds:1000000}),'100.0%');
});
test('dense generation keeps exact measured rate, image count, cadence and unchanged narration work',()=>{
 const c=config(),dense=buildPlan(c,evidence),old=buildPlan({...c,productionGenerationStrategy:'align'},evidence);
 near(dense.measurement.imagesPerSecond,old.measurement.imagesPerSecond);
 assert.equal(dense.imageCount,old.imageCount);assert.equal(dense.attemptCount,old.attemptCount);
 near(dense.tasks.filter(t=>t.kind==='delivered').reduce((n,t)=>n+t.duration,0),dense.attemptCount/dense.measurement.imagesPerSecond);
 assert.deepEqual(voices(dense),voices(old));assert.equal(dense.config.cadence,c.cadence);
 assert.match(productionModeNote(c),/reference/);
});
test('whole-burst descriptor capacity is a pre-start requirement, never a QC truncation rule',()=>{
 const c=config({minutes:707/20,cadence:20});
 assert.throws(()=>buildPlan({...c,productionDenseDescriptorLimit:256},evidence),/every initial image/);
 const p=buildPlan(c,evidence),r=schedule(p);assert.equal(p.imageCount,707);assert.equal(r.qcCount,707);
 assert.deepEqual(r.diagnostics,[]);
});
test('descriptor headroom covers the saved retry ceiling even with no forecast repairs',()=>{
 const c=config({minutes:250,cadence:20,retries:0,repairRounds:0,productionDenseMaxRepairRounds:2});
 assert.throws(()=>buildPlan(c,evidence),/saved retry ceiling/);
 const p=buildPlan({...c,productionDenseDescriptorLimit:15000},evidence);
 assert.equal(p.imageCount,5000);assert.equal(p.attemptCount,5000);
 assert.equal(p.rentalWindows.length,1);
 assert.throws(()=>buildPlan({...c,productionDenseDescriptorLimit:15000,intro:true,introSeconds:10,introImages:10},evidence),/saved retry ceiling/);
});
test('every assumed repair round has its own billed startup/stop and post-stop rechecks',()=>{
 const c=config({retries:50,repairRounds:2,qcSeconds:150,productionRenderOverlap:false}),p=buildPlan(c,evidence),r=schedule(p);
 assert.equal(p.attemptCount,p.imageCount+18);assert.equal(r.rentalWindows.length,3);
 assert.equal(r.qcCount,p.attemptCount);near(r.rentalSeconds,r.rentalWindows.reduce((n,w)=>n+w.end-w.start,0));
 const span=r.rentalWindows.at(-1).end-r.rentalWindows[0].start;assert.ok(r.rentalSeconds<span);
 near(r.gpuUSD,r.rentalSeconds/3600*p.gpu.hourly);
 for(const window of r.rentalWindows.slice(1)){
  const prefix=`repair${window.round}-`,tasks=r.tasks.filter(t=>t.id.startsWith(prefix));
  for(const kind of ['boot','models','health','cold','delivered','stop','vision'])assert.ok(tasks.some(t=>t.kind===kind),kind);
  const stop=tasks.find(t=>t.kind==='stop');assert.ok(tasks.filter(t=>t.kind==='vision').every(t=>t.start>=stop.end));
  assert.ok(window.end-window.start>tasks.find(t=>t.kind==='delivered').duration);
 }
 assert.deepEqual(r.diagnostics,[]);assert.ok(r.tasks.filter(t=>t.kind==='render').every(t=>t.start>=Math.max(...r.tasks.filter(x=>x.kind==='vision').map(x=>x.end))));
 assert.throws(()=>buildPlan(config({retries:20,repairRounds:4}),evidence),/retry ceiling/);
 const noRepairs=schedule(buildPlan(config({retries:0,repairRounds:3}),evidence));assert.equal(noRepairs.rentalWindows.length,1);
});
test('QC policy remains explicit; dense repair allowance never disables required rechecks',()=>{
 for(const qc of ['practical','strict']){
  const p=buildPlan(config({qc,retries:25,repairRounds:3}),evidence),r=schedule(p);
  assert.equal(r.qcCount,p.attemptCount);assert.deepEqual(r.diagnostics,[]);assert.equal(r.rentalWindows.length,4);
 }
 const p=buildPlan(config({qc:'off'}),evidence),r=schedule(p);assert.equal(r.qcCount,0);assert.equal(r.qcUSD,0);
});
test('the independent QC worker cap is enforced even when the shared API pool is larger',()=>{
 const p=buildPlan(config({productionDenseQCSlots:1,qcSeconds:100}),evidence),r=schedule(p);
 assert.equal(p.capacities.api,4);assert.equal(p.capacities.denseQC,1);
 const q=r.tasks.filter(t=>t.kind==='vision').sort((a,b)=>a.start-b.start);
 for(let i=1;i<q.length;i++)assert.ok(q[i].start>=q[i-1].end-1e-7);
 assert.equal(r.qcCount,p.imageCount);assert.deepEqual(r.diagnostics,[]);
});
test('configured early chapter rendering waits only for its own required QC and repairs',()=>{
 for(const retries of [0,50]){
  const c=config({qcSeconds:150,retries,repairRounds:2,productionRenderOverlap:true}),p=buildPlan(c,evidence),r=schedule(p);
  for(const render of r.tasks.filter(t=>t.kind==='render')){
   const own=r.tasks.filter(t=>t.kind==='vision'&&t.chapter===render.chapter);
   assert.ok(own.every(t=>t.end<=render.start+1e-7));
  }
  const first=r.tasks.find(t=>t.id==='c1-render'),otherQC=r.tasks.filter(t=>t.kind==='vision'&&t.chapter!==1);
  assert.ok(otherQC.some(t=>t.end>first.start));assert.deepEqual(r.diagnostics,[]);
  const disabled=schedule(buildPlan({...c,productionRenderOverlap:false},evidence));
  assert.ok(disabled.tasks.filter(t=>t.kind==='render').every(t=>t.start>=Math.max(...disabled.tasks.filter(x=>x.kind==='vision').map(x=>x.end))));
 }
});
function host(){const h={buttons:[],status:{textContent:''},querySelector(){return this.status;},querySelectorAll(){return this.buttons;}};
 Object.defineProperty(h,'innerHTML',{get(){return this.html;},set(value){this.html=value;this.buttons=[...value.matchAll(/data-start-index="(\d+)"/g)].map(m=>({dataset:{startIndex:m[1]}}));}});return h;}
test('startup worker, fallback and apply offer only the valid Dense all-plan boundary',async()=>{
 const c=config(),values=startupCandidates(c,evidence);assert.equal(values.length,1);assert.equal(values[0].readyChapters,c.chapters);
 const previous=globalThis.Worker,workers=[];
 globalThis.Worker=class{constructor(){workers.push(this);}postMessage(data){this.data=data;}terminate(){}};
 try{
  const h=host();let chosen;mountStartupTradeoffs(h,c,evidence,value=>{chosen=value;});
  assert.equal(workers[0].data.searchPolicy,'current');workers[0].onmessage({data:{type:'complete',values}});
  assert.match(h.innerHTML,/Installed Dense/);h.buttons[0].onclick();const applied=startupApplyConfig(c,chosen);
  assert.ok(denseActive(applied));assert.equal(applied.readyChapters,c.chapters);near(schedule(buildPlan(applied,evidence)).end,values[0].result.end);
  delete globalThis.Worker;const fallback=host();mountStartupTradeoffs(fallback,c,evidence,()=>{});await fallback._startupTask;
  assert.equal(fallback._startupValues.length,1);assert.equal(fallback._startupValues[0].readyChapters,c.chapters);
 }finally{if(previous===undefined)delete globalThis.Worker;else globalThis.Worker=previous;}
});
