import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,validateSchedule,importSnapshot,CATALOG} from './pipeline-engine.mjs';
import {measuredRows} from './pipeline-lab.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url),'utf8'));
const parallelEvidence=structuredClone(evidence);
parallelEvidence.concurrency={gpus:[{id:'testgpu',name:'Fixture GPU',vramGB:48,
  hardware:[{hourlyUSD:1.007,computeContainerHourlyUSD:1,startupSeconds:30,downloadHashSeconds:60,firstWarmupClientSeconds:10,region:'test'}],
  profiles:[1,2,4].map(workers=>({resolution:'720p',workers,postControl:false,rounds:2,images:24,
    imagesPerSecond:workers===4?1:workers*.3,meanClientLatencySeconds:3,meanServerLatencySeconds:2}))}]};

test('measured parallel groups use delivered throughput once, without multiplying by worker count',()=>{
  const p=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',imageWorkers:'4'},parallelEvidence),r=schedule(p);
  assert.equal(p.measurement.workers,4);
  const images=p.tasks.filter(t=>t.kind==='delivered');
  assert.equal(images.reduce((n,t)=>n+t.images,0),p.attemptCount);
  assert.equal(images.reduce((n,t)=>n+t.duration,0),p.attemptCount);
  assert.ok(!p.tasks.some(t=>['prepare','upload','infer','poll','download','save'].includes(t.kind)));
  assert.deepEqual(r.diagnostics,[]);
  assert.equal(r.gpuUSD,r.rentalSeconds/3600);
  assert.match(p.renderScope,/1080p.*not calibrated/);
});

test('absent resolution and worker measurements are rejected instead of pixel scaling',()=>{
  for(const settings of [{resolution:'1080p'},{resolution:'720p',imageWorkers:'3'},{resolution:'legacy'}])
    assert.throws(()=>buildPlan({...DEFAULTS,gpu:'testgpu',...settings},parallelEvidence));
  assert.throws(()=>buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',policy:'current',qc:'strict'},parallelEvidence),/experiment/);
});

test('measured best worker count is selected from actual rates',()=>{
  const p=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p'},parallelEvidence);
  assert.equal(p.measurement.workers,4);
  const slower=structuredClone(parallelEvidence);slower.concurrency.gpus[0].profiles[2].imagesPerSecond=.4;
  assert.equal(buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p'},slower).measurement.workers,2);
});

test('GPU chart excludes incomplete rounds and post-controls',()=>{
  const d=structuredClone(parallelEvidence.concurrency);
  d.gpus[0].profiles.push({...d.gpus[0].profiles[0],workers:6,imagesPerSecond:10,rounds:1});
  d.gpus[0].profiles.push({...d.gpus[0].profiles[0],workers:6,imagesPerSecond:20,postControl:true});
  assert.equal(measuredRows(d,'720p')[0].best.workers,4);
  assert.deepEqual(measuredRows(d,'1080p'),[]);
});

test('single-server pipeline capacity counts request slots without multiplying measured throughput',()=>{
  const d=structuredClone(parallelEvidence);
  d.concurrency.gpus[0].hardware[0].attempt='one-host';
  d.concurrency.gpus[0].pipelineProfiles=[2,6].map(clientSlots=>({
    ...d.concurrency.gpus[0].profiles[0],executionMode:'pipeline',encodingProfile:'fresh',
    workers:1,clientSlots,imagesPerSecond:clientSlots===6?.8:.6,attempt:'one-host',runStartedAt:20
  }));
  const plan=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',executionMode:'pipeline',encodingProfile:'fresh'},d);
  assert.equal(plan.measurement.workers,1);
  assert.equal(plan.measurement.clientSlots,6);
  assert.equal(plan.tasks.filter(t=>t.kind==='delivered').reduce((n,t)=>n+t.duration,0),plan.attemptCount/.8);
  assert.equal(measuredRows(d.concurrency,'720p','fresh','pipeline')[0].profiles.length,2);
  assert.match(plan.gpu.scope,/6 request slots/);
  assert.deepEqual(measuredRows(d.concurrency,'1080p','fresh','pipeline'),[]);
});

test('unmeasured pipeline mode does not substitute independent worker measurements',()=>{
  assert.throws(()=>buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',executionMode:'pipeline'},parallelEvidence),/No completed/);
});

test('an explicit older rental host remains selectable without borrowing newer counts',()=>{
  const d=structuredClone(parallelEvidence);
  const g=d.concurrency.gpus[0];
  for(const p of g.profiles){p.attempt='old-host';p.runStartedAt=1;}
  g.hardware[0].attempt='old-host';
  g.profiles.push({...g.profiles[0],attempt:'new-host',runStartedAt:20,imagesPerSecond:.1});
  g.hardware.push({...g.hardware[0],attempt:'new-host'});
  const old=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',measurementAttempt:'old-host'},d);
  const latest=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p'},d);
  assert.equal(old.measurement.attempt,'old-host');
  assert.equal(old.measurement.workers,4);
  assert.equal(latest.measurement.attempt,'new-host');
  assert.equal(latest.measurement.workers,1);
  g.hardware=g.hardware.filter(h=>h.attempt==='old-host');
  assert.throws(()=>buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p'},d),/No other rental host is substituted/);
});

test('fresh conditioning never falls back to a cached speed measurement',()=>{
  assert.deepEqual(measuredRows(parallelEvidence.concurrency,'720p','fresh'),[]);
  assert.throws(()=>buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',encodingProfile:'fresh'},parallelEvidence),/fresh conditioning/);
  assert.throws(()=>buildPlan({...DEFAULTS,encodingProfile:'unknown'},parallelEvidence),/conditioning/);
});

test('fresh measured worker groups retain their own slower rate and cost',()=>{
  const d=structuredClone(parallelEvidence);
  d.concurrency.gpus[0].productionProfiles=d.concurrency.gpus[0].profiles.map(p=>({...p,encodingProfile:'fresh',imagesPerSecond:p.imagesPerSecond/2}));
  const fresh=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',encodingProfile:'fresh'},d);
  const cached=buildPlan({...DEFAULTS,gpu:'testgpu',resolution:'720p',encodingProfile:'cached'},d);
  assert.equal(fresh.measurement.imagesPerSecond,.5);
  assert.equal(fresh.tasks.filter(t=>t.kind==='delivered').reduce((n,t)=>n+t.duration,0),cached.attemptCount*2);
  assert.match(fresh.gpu.scope,/Fresh text and reference encoding/);
  assert.equal(measuredRows(d.concurrency,'720p','fresh')[0].best.imagesPerSecond,.5);
});

test('different rental attempts remain separate and latest complete rounds drive comparisons',()=>{
  const d=structuredClone(parallelEvidence.concurrency);
  d.gpus[0].profiles.push({...d.gpus[0].profiles[2],runStartedAt:100,imagesPerSecond:.4});
  assert.equal(measuredRows(d,'720p')[0].best.workers,2);
});

test('a scaling curve cannot combine worker counts from different rental hosts',()=>{
  const d=structuredClone(parallelEvidence.concurrency);
  for(const p of d.gpus[0].profiles){p.attempt='old-host';p.runStartedAt=1;}
  d.gpus[0].profiles.push({...d.gpus[0].profiles[0],attempt:'new-host',runStartedAt:100,imagesPerSecond:.1});
  const row=measuredRows(d,'720p')[0];
  assert.equal(row.best.workers,1);
  assert.equal(row.profiles.length,1);
  assert.equal(row.profiles[0].attempt,'new-host');
});

test('published concurrency data contains aggregates without private execution data',()=>{
  const source=fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url),'utf8');
  assert.ok(!/rpa_|sk-[A-Za-z0-9]|@gmail|podId|remotePromptId|imagePath|referenceImages|promptSHA256|studio-secrets|ssh-route/i.test(source));
});
test('all six hardware profiles have feasible streaming schedules',()=>{
  for(const gpu of evidence.gpus){const p=buildPlan({...DEFAULTS,gpu:gpu.id},evidence),r=schedule(p);assert.deepEqual(r.diagnostics,[]);assert.equal(p.imageCount,709);assert.equal(p.attemptCount,709);assert.ok(r.end>0);assert.equal(r.tasks.length,p.tasks.length);}
});
test('chapter handoff remains a dependency of next analysis',()=>{
  const p=buildPlan(DEFAULTS,evidence),r=schedule(p),a=r.tasks.find(t=>t.id==='c2-analyst'),h=r.tasks.find(t=>t.id==='c1-handoff');assert.ok(a.start>=h.end);assert.ok(a.deps.includes(h.id));
});
test('fully serial baseline does not overlap any work and stops compute before render',()=>{
  const p=buildPlan({...DEFAULTS,policy:'serial'},evidence),r=schedule(p);for(let i=1;i<r.tasks.length;i++)assert.ok(r.tasks[i].start>=r.tasks[i-1].end-1e-7);
  assert.ok(r.tasks.find(t=>t.id==='stop').end<=Math.min(...r.tasks.filter(t=>t.kind==='render').map(t=>t.start)));
});
test('same work can overlap Luna and cloud inference in proposed plan',()=>{
  const p=buildPlan(DEFAULTS,evidence),r=schedule(p);assert.ok(r.tasks.some(a=>a.lane==='director'&&r.tasks.some(b=>b.kind==='infer'&&a.start<b.end&&b.start<a.end)));
  assert.ok(r.end<schedule(buildPlan({...DEFAULTS,policy:'serial'},evidence)).end);
});
test('installed overlap refuses unchecked or sampled jobs and supports full QC',()=>{
  for(const qc of ['off','sampled'])assert.throws(()=>buildPlan({...DEFAULTS,policy:'current',qc},evidence),/Practical or Strict/);
  const r=schedule(buildPlan({...DEFAULTS,policy:'current',qc:'practical',chapters:3},evidence));assert.deepEqual(r.diagnostics,[]);
});
test('timing metadata, image counts and intro are consistent',()=>{
  const p=buildPlan({...DEFAULTS,intro:false,chapters:2},evidence),r=schedule(p);assert.equal(p.imageCount,707);assert.ok(!p.tasks.some(t=>t.id.startsWith('intro-')));
  assert.equal(p.tasks.filter(t=>t.kind==='infer').reduce((n,t)=>n+t.images,0),707);
  assert.equal(p.tasks.filter(t=>t.kind==='render').reduce((n,t)=>n+t.videoSeconds,0),7200);
  assert.equal(r.qcCount,0);
});
test('extra attempts appear exactly once in the image work',()=>{
  const p=buildPlan({...DEFAULTS,retries:10},evidence);assert.equal(p.attemptCount,780);assert.equal(p.tasks.filter(t=>t.kind==='infer').reduce((n,t)=>n+t.images,0),780);
});
test('moves cannot override dependencies or one-slot GPU limit',()=>{
  const p=buildPlan({...DEFAULTS,chapters:2},evidence),r=schedule(p,{'c1-b1-infer':{notBefore:0},'c1-voice':{notBefore:3600}});assert.deepEqual(r.diagnostics,[]);assert.ok(r.tasks.find(t=>t.id==='c1-voice').start>=3600);assert.ok(r.tasks.find(t=>t.id==='c1-b1-infer').start>=r.tasks.find(t=>t.id==='cold').end);
});
test('validator rejects deliberately overlapping GPU work',()=>{
  const p=buildPlan({...DEFAULTS,chapters:2},evidence),r=schedule(p),clone=structuredClone(r.tasks),gpu=clone.filter(t=>t.kind==='infer');gpu[1].start=gpu[0].start;gpu[1].end=gpu[1].start+gpu[1].duration;assert.ok(validateSchedule(p,clone).some(x=>x.includes('remote')));
});
test('delay inside rental increases billed interval and price',()=>{
  const p=buildPlan({...DEFAULTS,chapters:2},evidence),r=schedule(p),last=r.tasks.filter(t=>t.kind==='infer').at(-1),later=schedule(p,{[last.id]:{notBefore:r.tasks.find(t=>t.id==='stop').end+1800}});assert.ok(later.gpuUSD>r.gpuUSD);assert.equal(later.gpuUSD,later.rentalSeconds/3600*p.gpu.hourly);
});
test('cache saves download time but does not remove model loading',()=>{
  const p=buildPlan({...DEFAULTS,warmCache:true},evidence);assert.equal(p.tasks.find(t=>t.id==='models').duration,1);assert.ok(p.tasks.find(t=>t.id==='cold').duration>0);
});
test('optional full video decode adds an explicitly measured stage',()=>{
  const p=buildPlan({...DEFAULTS,exhaustive:true},evidence),r=schedule(p),base=schedule(buildPlan(DEFAULTS,evidence));assert.equal(r.tasks.find(t=>t.id==='decode').duration,evidence.calibration.exhaustiveDecodeSeconds);assert.ok(r.end>base.end);
});
test('public evidence and export inputs contain no credentials and reject invalid times',()=>{
  assert.ok(!/sk-[a-zA-Z0-9]|rpa_|@gmail|promptSHA256|secretKey/i.test(JSON.stringify(evidence)));
  const input={type:'studio-pipeline-plan',version:1,config:DEFAULTS,preferences:{'c1-voice':{notBefore:50}}};assert.equal(importSnapshot(input,evidence).preferences['c1-voice'].notBefore,50);
  assert.throws(()=>importSnapshot({...input,preferences:{'c1-voice':{notBefore:-1}}},evidence));assert.throws(()=>buildPlan({...DEFAULTS,chapters:1.5},evidence));assert.throws(()=>buildPlan({...DEFAULTS,gpu:'unavailable'},evidence));
});
test('nested processes are documented without extra timing tasks',()=>{
  const p=buildPlan(DEFAULTS,evidence);for(const kind of ['encoder','denoise','vae','audioEncode','motion']){assert.ok(CATALOG.some(m=>m.id===kind));assert.ok(!p.tasks.some(t=>t.kind===kind));}
});
test('all built-in modes have no graph cycles across small and large projects',()=>{
  for(const policy of ['serial','current','proposed'])for(const chapters of [1,2,14]){const r=schedule(buildPlan({...DEFAULTS,policy,chapters,qc:policy==='current'?'strict':'off'},evidence));assert.deepEqual(r.diagnostics,[]);}
});
