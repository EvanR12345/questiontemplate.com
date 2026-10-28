import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {qualityConfig} from './pipeline-qc-forecast.mjs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const calibration=read('./pipeline-qc-calibration.json');
const evidence={...read('./pipeline-evidence.json'),concurrency:read('./pipeline-concurrency.json'),directorCalibration:read('./pipeline-director-calibration.json'),qcCalibration:calibration};
const config=()=>({...DEFAULTS,policy:'current',gpu:'5090',resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',imageWorkers:'4',productionModelCopies:2,productionRequestSlots:4,
 directorProfile:'lean-current-standard',directorActualSlots:4,apiSlots:4,qc:'practical',qcSeconds:10.220080783845333,qcCost:.000372101,
 qcHistorySeconds:10.220080783845333,qcHistoryCost:.000372101,qcHistoryLatest:1791875305.1467574,qcCompatibilityHash:calibration.cohorts[0].qcCompatibilityHash,qcProcessingTier:'default',qcProductionTasks:5,
 qcHistoryIdentity:{policy:'practical',apiSlots:4,processingTier:'default',compatibilityHash:calibration.cohorts[0].qcCompatibilityHash}});

test('newer compatible settled cohort updates review work and cost without mutating inputs',()=>{
 const c=config(),before=structuredClone(c),data=structuredClone(evidence);
 const out=qualityConfig(c,evidence),row=calibration.cohorts.find(r=>r.policy==='practical'&&r.apiSlots===4);
 assert.equal(out.qcSeconds,row.secondsPerCheck);assert.equal(out.qcCost,row.costUSDPerCheck);
 assert.equal(out.qcBenchmarkId,row.id);assert.match(out.qcBenchmarkNote,/24 checks of 12 unique images/);
 assert.deepEqual(c,before);assert.deepEqual(evidence,data);assert.deepEqual(qualityConfig(out,evidence),out);
});

test('policy and slots reselect cohorts; five production tasks cannot claim eight simultaneous reviews',()=>{
 const practical=qualityConfig(config(),evidence),strict=qualityConfig({...practical,qc:'strict'},evidence);
 assert.match(strict.qcBenchmarkId,/strict-4$/);assert.notEqual(strict.qcSeconds,practical.qcSeconds);
 const limited=qualityConfig({...strict,apiSlots:8},evidence);
 assert.equal(limited.qcBenchmarkId,'');assert.equal(limited.qcSeconds,config().qcHistorySeconds);
 const eight=qualityConfig({...strict,apiSlots:8,qcProductionTasks:8},evidence);
 assert.match(eight.qcBenchmarkId,/strict-8$/);assert.ok(eight.qcSeconds>strict.qcSeconds);
});

test('newer production history and mismatched model, tier or format do not inherit the old cohort',()=>{
 const measured=qualityConfig(config(),evidence);
 for(const patch of [{qcHistoryLatest:measured.qcBenchmarkObservedAt+1},{qcCompatibilityHash:'0'.repeat(64)},
  {directorTier:'flex'},{qcProcessingTier:'priority'},{resolution:'1080p'},{qc:'off'},{qc:'sampled'},{policy:'serial'}]){
  const out=qualityConfig({...measured,...patch},evidence);
  assert.equal(out.qcBenchmarkId,'');assert.equal(out.qcSeconds,config().qcHistorySeconds);
  assert.equal(out.qcCost,config().qcHistoryCost);
 }
});

test('schedule and accounting update together while narration, render work and image attempts stay identical',()=>{
 const c=config(),oldEvidence={...evidence,qcCalibration:undefined};
 const before=buildPlan(c,oldEvidence),after=buildPlan(c,evidence),a=schedule(before),b=schedule(after);
 const selected=k=>plan=>plan.tasks.filter(t=>t.kind===k).map(t=>({id:t.id,duration:t.duration,resources:t.resources,deps:t.deps}));
 assert.deepEqual(selected('voice')(before),selected('voice')(after));
 assert.deepEqual(selected('render')(before),selected('render')(after));
 assert.equal(before.imageCount,after.imageCount);assert.equal(before.attemptCount,after.attemptCount);
 assert.deepEqual(a.diagnostics,[]);assert.deepEqual(b.diagnostics,[]);assert.notEqual(a.end,b.end);
 assert.equal(b.qcUSD,b.qcCount*after.config.qcCost);
 assert.equal(after.tasks.filter(t=>t.kind==='vision').reduce((sum,t)=>sum+t.duration,0),b.qcCount*after.config.qcSeconds);
 assert.notEqual(a.qcUSD,b.qcUSD);
 assert.equal(after.config.retries,c.retries);
});

test('manual QC pricing stays explicit while newer timing remains selected',()=>{
 const out=qualityConfig({...config(),qcCostManual:true,qcCost:.001},evidence);
 assert.ok(out.qcBenchmarkId);assert.equal(out.qcCost,.001);assert.ok(out.qcSeconds>14);
 const restored=qualityConfig({...out,qc:'strict'},evidence);assert.equal(restored.qcCost,.001);
});

test('cohort concurrency matches actual reserved API capacity after a director-profile edit',()=>{
 const c=qualityConfig(config(),evidence);
 const changed=buildPlan({...c,directorActualSlots:0,directorProfile:'classic-standard'},evidence);
 assert.equal(changed.capacities.api,2);assert.equal(changed.config.qcBenchmarkId,'');
 assert.equal(changed.config.qcSeconds,c.qcHistorySeconds);
 const matching=buildPlan(c,evidence);
 assert.ok(matching.config.qcBenchmarkId);assert.equal(matching.config.qcEffectiveSlots,matching.capacities.api);
 const serial=buildPlan({...c,policy:'serial'},evidence);assert.equal(serial.config.qcBenchmarkId,'');
});

test('newer Practical history cannot suppress a compatible Strict cohort',()=>{
 const measured=qualityConfig({...config(),qcHistoryLatest:calibration.cohorts[0].observedAt+100},evidence);
 assert.equal(measured.qcBenchmarkId,'');
 const strict=qualityConfig({...measured,qc:'strict'},evidence);
 assert.match(strict.qcBenchmarkId,/strict-4$/);
 const matching=qualityConfig({...strict,qcHistoryIdentity:{...strict.qcHistoryIdentity,policy:'strict'}},evidence);
 assert.equal(matching.qcBenchmarkId,'');
});

test('incomplete, uncertain or malformed evidence cannot change the existing estimate',()=>{
 for(const qcCalibration of [{version:1,timingScope:'api-call'},
  {...calibration,cohorts:[null]},
  {...calibration,cohorts:calibration.cohorts.map(r=>({...r,unknownRequests:1}))},
  {...calibration,cohorts:calibration.cohorts.map(r=>({...r,status:'INCOMPLETE'}))}]){
  const out=qualityConfig(config(),{...evidence,qcCalibration});assert.equal(out.qcBenchmarkId,'');assert.equal(out.qcSeconds,config().qcSeconds);
 }
});
