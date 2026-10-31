import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {denseCapability,denseLeaseRetained,heldQualityIdleSeconds} from './pipeline-dense.mjs';
import {plannerSettings,profileSummary} from './studio-planner-profile.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');
evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
evidence.qcCalibration=read('./pipeline-qc-calibration.json');
const profile=()=>({version:1,name:'Lease contract fixture',revision:1,generationStrategy:'dense',
 denseImagePhase:{version:1,supported:true,eligible:true,generationTasks:5,qcTaskSlots:4,descriptorLimit:8192,maxRepairRounds:3,allPlansFirst:true,
  leasePolicy:'retained-qc-v1',holdsGPUForQC:true},
 settings:{unattendedProduction:true,automaticRepair:true,voice:'am_michael',voiceEngine:'kokoro-82m',qcCheckLevel:'practical',
  video:{width:1280,height:720,fps:30,renderer:'cpu'},image:{width:1280,height:720,model:'flux2-klein-4b',steps:4,guidance:1},
  director:{provider:'openai-luna',executionMode:'staged-lean',parallelism:4,processingTier:'default',reasoning:'Balanced',visualPacking:'small'}},
 imageExecution:{backend:'comfyui-api',gpuLanes:2,modelCopies:2,requestSlots:4,requiredGPU:'NVIDIA GeForce RTX 5090'},
 phaseOverlap:{audioLookahead:true,chapterRender:true},intro:{enabled:false},estimates:{}});
const config=extra=>({...plannerSettings({...DEFAULTS,minutes:3,chapters:3,cadence:12,batch:6},profile(),evidence).config,...extra});
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-7,`${a} != ${b}`);
const voice=p=>p.tasks.filter(t=>t.lane==='audio').map(({id,kind,duration,deps,resources})=>({id,kind,duration,deps,resources}));
test('Follow requires a consistent lease pair and preserves explicit legacy or old-helper policies',()=>{
 const p=profile(),before=structuredClone(p),follow=plannerSettings(DEFAULTS,p,evidence);
 assert.ok(denseCapability(p));assert.ok(denseLeaseRetained(follow.config));assert.deepEqual(p,before);
 assert.match(follow.warnings.join(' '),/one paid GPU lease/);assert.match(profileSummary(p),/one paid lease/);
 p.denseImagePhase.leasePolicy='stopped-qc-v1';p.denseImagePhase.holdsGPUForQC=false;
 const stopped=plannerSettings(DEFAULTS,p,evidence).config;assert.equal(denseLeaseRetained(stopped),false);
 delete p.denseImagePhase.leasePolicy;delete p.denseImagePhase.holdsGPUForQC;
 const old=plannerSettings(DEFAULTS,p,evidence).config;assert.equal(old.productionDenseLeasePolicy,'stopped-qc-v1');
 assert.equal(old.productionDenseHoldsGPUForQC,false);
 for(const phase of [{leasePolicy:'retained-qc-v1',holdsGPUForQC:false},{leasePolicy:'stopped-qc-v1',holdsGPUForQC:true},
  {leasePolicy:'unknown',holdsGPUForQC:true},{leasePolicy:'retained-qc-v1'}, {holdsGPUForQC:true}]){
  const bad=profile();delete bad.denseImagePhase.leasePolicy;delete bad.denseImagePhase.holdsGPUForQC;
  Object.assign(bad.denseImagePhase,phase);assert.equal(denseCapability(bad),false);
  assert.throws(()=>plannerSettings(DEFAULTS,bad,evidence),/updated helper/);
 }
 assert.throws(()=>buildPlan(config({productionDenseHoldsGPUForQC:false}),evidence),/lease policy/);
});
test('retained repairs keep exactly the same image and QC work without extra boot, loading or stop',()=>{
 for(const qc of ['practical','strict','off'])for(const productionAudioLookahead of [false,true]){
  const c=config({qc,productionAudioLookahead,intro:true,introSeconds:20,introImages:3,retries:50,repairRounds:2,qcSeconds:150});
  const retained=buildPlan(c,evidence),legacy=buildPlan({...c,productionDenseLeasePolicy:'stopped-qc-v1',productionDenseHoldsGPUForQC:false},evidence);
  const r=schedule(retained),old=schedule(legacy);
  assert.deepEqual(r.diagnostics,[]);assert.deepEqual(old.diagnostics,[]);assert.equal(retained.repairRoundCount,2);
  assert.equal(r.rentalWindows.length,1);assert.equal(old.rentalWindows.length,3);
  for(const kind of ['boot','models','health','cold','stop'])assert.equal(retained.tasks.filter(t=>t.kind===kind).length,1,kind);
  assert.equal(retained.imageCount,legacy.imageCount);assert.equal(retained.attemptCount,legacy.attemptCount);
  assert.equal(r.qcCount,old.qcCount);assert.equal(r.qcUSD,old.qcUSD);near(r.gpuWork,old.gpuWork);
  assert.deepEqual(voice(retained),voice(legacy));
  assert.deepEqual(retained.tasks.filter(t=>t.kind==='vision').map(({id,images,duration,resources})=>({id,images,duration,resources})),
   legacy.tasks.filter(t=>t.kind==='vision').map(({id,images,duration,resources})=>({id,images,duration,resources})));
  const first=r.tasks.find(t=>t.id==='repair1-images'),second=r.tasks.find(t=>t.id==='repair2-images');
  const initial=r.tasks.filter(t=>t.kind==='vision'&&!t.id.startsWith('repair'));
  assert.ok(initial.every(t=>t.end<=first.start+1e-7));
  assert.ok(r.tasks.filter(t=>t.kind==='vision'&&t.id.startsWith('repair1-')).every(t=>t.end<=second.start+1e-7));
  const stop=r.tasks.find(t=>t.id==='stop');assert.ok(r.tasks.filter(t=>['vision','delivered'].includes(t.kind)).every(t=>t.end<=stop.start+1e-7));
  near(r.rentalSeconds,stop.end-r.tasks.find(t=>t.id==='boot').start);near(r.gpuUSD,r.rentalSeconds/3600*retained.gpu.hourly);
 }
});
test('slow checks increase paid waiting and rental cost while image work and narration stay unchanged',()=>{
 const quick=buildPlan(config({qcSeconds:1,retries:25,repairRounds:2}),evidence),slow=buildPlan(config({qcSeconds:200,retries:25,repairRounds:2}),evidence);
 const a=schedule(quick),b=schedule(slow);
 near(a.gpuWork,b.gpuWork);assert.deepEqual(voice(quick),voice(slow));assert.ok(b.rentalSeconds>a.rentalSeconds);assert.ok(b.gpuUSD>a.gpuUSD);
 assert.ok(b.heldQCIdleSeconds>a.heldQCIdleSeconds);assert.ok(b.heldQCIdleSeconds<=b.gpuIdleSeconds+1e-7);
 near(b.heldQCIdleUSD,b.heldQCIdleSeconds/3600*slow.gpu.hourly);assert.deepEqual(b.diagnostics,[]);
});
test('held QC waiting uses union wall time clipped to the lease, excluding simultaneous image work',()=>{
 const tasks=[{kind:'vision',start:10,end:30},{kind:'vision',start:20,end:40},{kind:'delivered',start:0,end:20},
  {kind:'infer',start:30,end:35},{kind:'render',start:0,end:100},{kind:'vision',start:60,end:90}];
 near(heldQualityIdleSeconds(tasks,[{start:0,end:50}]),15);
 near(heldQualityIdleSeconds(tasks,[{start:25,end:38}]),8);
 near(heldQualityIdleSeconds(tasks,[{start:0,end:50},{start:70,end:80}]),25);
 assert.equal(heldQualityIdleSeconds([],[]),0);
});
test('no-repair retained run still waits for initial QC, and old exported scenarios keep stopped leases',()=>{
 const p=buildPlan(config({retries:0,qcSeconds:200}),evidence),r=schedule(p);
 assert.equal(p.repairRoundCount,0);assert.equal(r.rentalWindows.length,1);
 assert.ok(r.tasks.filter(t=>t.kind==='vision').every(t=>t.end<=r.tasks.find(t=>t.id==='stop').start));
 const input=config();delete input.productionDenseLeasePolicy;delete input.productionDenseHoldsGPUForQC;
 const imported=importSnapshot({type:'studio-pipeline-plan',version:1,config:input},evidence);
 assert.equal(imported.config.productionDenseLeasePolicy,'stopped-qc-v1');assert.equal(imported.config.productionDenseHoldsGPUForQC,false);
 const old=schedule(buildPlan({...imported.config,qcSeconds:200},evidence));
 assert.ok(old.tasks.find(t=>t.id==='stop').end<Math.max(...old.tasks.filter(t=>t.kind==='vision').map(t=>t.end)));
});
