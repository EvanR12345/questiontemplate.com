import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {directorForecast,migrateDirectorForecast} from './pipeline-director-calibration.mjs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {compareGPUPlans} from './pipeline-gpu-comparison.mjs';
import {startupSearch} from './pipeline-detail-ui.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
evidence.directorCalibration=JSON.parse(fs.readFileSync(new URL('./pipeline-director-calibration.json',import.meta.url)));
const c={...DEFAULTS,intro:false,directorProfile:'small-groups-hybrid',directorCostMode:'measured'};
test('schedule uses latest complete chapter wall time and actual receipts once, with tested concurrency',()=>{
  const before=structuredClone(evidence),p=buildPlan(c,evidence),r=schedule(p);
  assert.deepEqual(r.diagnostics,[]);
  assert.equal(p.capacities.api,8);
  assert.equal(p.tasks.filter(t=>t.kind==='chapter').length,c.chapters);
  assert.ok(!p.tasks.some(t=>['analyst','scene','layout','continuity','prompt'].includes(t.kind)));
  assert.ok(Math.abs(p.tasks.filter(t=>t.kind==='chapter').reduce((s,t)=>s+t.duration,0)-124.672*7200/1050.23)<1e-5);
  assert.ok(Math.abs(r.directorUSD-.028551009*7200/1050.23)<1e-8);
  assert.deepEqual(evidence,before);
  // Eight slots are included in measured latency, not eight more copies.
  assert.equal(schedule(buildPlan({...c,apiSlots:1},evidence)).end,r.end);
});
test('Flex changes eligible API dollars only and makes its latency assumption explicit',()=>{
  const standard=schedule(buildPlan(c,evidence)),p=buildPlan({...c,directorTier:'flex'},evidence),flex=schedule(p);
  assert.equal(flex.directorUSD,standard.directorUSD/2);
  assert.equal(flex.end,standard.end);assert.equal(flex.gpuUSD,standard.gpuUSD);
  assert.equal(p.director.timingMeasured,false);assert.match(p.director.scope,/unmeasured/);
  const slower=buildPlan({...c,directorTier:'flex',flexLatencyFactor:2},evidence);
  assert.equal(slower.tasks.find(t=>t.kind==='chapter').duration,p.tasks.find(t=>t.kind==='chapter').duration*2);
  const manual=schedule(buildPlan({...c,directorTier:'flex',directorCostMode:'manual',directorCost:.4},evidence));
  assert.equal(manual.directorUSD,.4); // User's effective tier estimate is not discounted twice.
});
test('research and incomplete measurements cannot masquerade as installed calibrated production',()=>{
  assert.throws(()=>buildPlan({...c,policy:'current'},evidence),/research prototype/);
  assert.throws(()=>buildPlan(c,{...evidence,directorCalibration:null}),/unavailable/);
  for(const patch of [{status:'FAILED'},{unknownRequests:1},{completedChapters:1}]){
    const bad=structuredClone(evidence);Object.assign(bad.directorCalibration.profiles.find(p=>p.id===c.directorProfile),patch);
    assert.throws(()=>buildPlan(c,bad),/completed/);
  }
});
test('clean matching Flex receipts override the assumption without discounting their already-discounted cost twice',()=>{
  const p=evidence.directorCalibration.profiles.find(p=>p.id==='whole-chapter-hybrid');
  assert.equal(p.tiers.flex.timingEligible,true);
  const config={...c,directorProfile:p.id,directorTier:'flex',flexLatencyFactor:9};
  const forecast=directorForecast(config,evidence);
  assert.equal(forecast.tierMeasured,true);assert.equal(forecast.measuredSeconds,p.tiers.flex.seconds);
  assert.equal(forecast.secondsPerStorySecond,p.tiers.flex.seconds/evidence.directorCalibration.sourceNarrationSeconds);
  assert.equal(forecast.costPerTwoHours,p.tiers.flex.estimatedUSD/evidence.directorCalibration.sourceNarrationSeconds*7200);
  const interrupted=structuredClone(evidence);
  interrupted.directorCalibration.profiles.find(p=>p.id===config.directorProfile).tiers.flex.timingEligible=false;
  assert.equal(directorForecast(config,interrupted).tierMeasured,false);
});
test('new director rates feed GPU comparisons and rental startup search, not only evidence cards',()=>{
  const old={...c,directorProfile:'historical',directorCostMode:'manual'},oldResult=schedule(buildPlan(old,evidence)),updated=schedule(buildPlan(c,evidence));
  assert.ok(updated.end<oldResult.end);assert.ok(updated.directorUSD<oldResult.directorUSD);
  const comparisons=compareGPUPlans(c,evidence);
  assert.ok(comparisons.comparisons.length);
  const gpu=comparisons.comparisons.find(v=>v.g.id===c.gpu);
  assert.equal(gpu.r.end,updated.end);assert.equal(gpu.r.totalUSD,updated.totalUSD);
  const search=startupSearch({...c,chapters:2},evidence,true);let step;do{step=search.next();}while(!step.done);
  assert.ok(step.value.every(v=>v.result.directorUSD===updated.directorUSD));
});
test('migration preserves explicit costs and settings, and exported plans retain current calibration selection',()=>{
  const legacy={minutes:720,directorCost:.38798,gpu:'5090'};
  const next=migrateDirectorForecast(legacy,true);assert.equal(next.directorProfile,'lean-standard');assert.equal(next.directorCostMode,'measured');assert.equal(next.minutes,720);
  assert.equal(migrateDirectorForecast({...legacy,directorCost:.9},true).directorCostMode,'manual');
  assert.equal(migrateDirectorForecast(legacy,true,true).directorProfile,'historical'); // Do not erase a manually arranged eight-pass timeline on refresh.
  assert.deepEqual(migrateDirectorForecast(c,true),c);
  const input={version:1,type:'studio-pipeline-plan',config:c,preferences:{'c1-chapter':{notBefore:100}}};
  const loaded=importSnapshot(input,evidence);assert.equal(loaded.config.directorProfile,c.directorProfile);assert.deepEqual(loaded.preferences,input.preferences);
});
test('long projections keep narration, image cadence, rendering and ordered handoffs intact',()=>{
  for(const minutes of [90,120,720,1440]){
    const p=buildPlan({...c,minutes,chapters:80},evidence),r=schedule(p);
    assert.deepEqual(r.diagnostics,[]);assert.equal(p.imageCount,Math.ceil(minutes*c.cadence));
    assert.ok(p.tasks.find(t=>t.id==='c2-chapter').deps.includes('c1-handoff'));
    assert.equal(p.tasks.filter(t=>t.kind==='voice').length,80);
  }
});
