import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {realisticPlanningConfig,selectScenarioGPUConfig,reviewPolicyNote} from './pipeline-realistic.mjs';
const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const evidence=read('./pipeline-evidence.json');
evidence.concurrency=read('./pipeline-concurrency.json');
evidence.directorCalibration=read('./pipeline-director-calibration.json');
const config={...DEFAULTS,resolution:'720p',encodingProfile:'fresh',directorProfile:'lean-current-standard',directorCostMode:'measured',executionMode:'hybrid',imageWorkers:'best',qc:'off'};
const voices=plan=>plan.tasks.filter(t=>t.kind==='voice').map(t=>({id:t.id,duration:t.duration,resources:t.resources}));

test('unconnected default is installed single-copy Practical without changing voice or creative inputs',()=>{
 const before=structuredClone(config),next=realisticPlanningConfig(config,evidence);
 assert.equal(next.policy,'current');assert.equal(next.qc,'practical');assert.equal(next.imageWorkers,'1');assert.equal(next.executionMode,'resident');
 assert.equal(next.productionModelCopies,1);assert.equal(next.productionRequestSlots,1);assert.equal(next.apiSlots,4);
 for(const key of ['minutes','chapters','cadence','intro','introSeconds','introImages','videoResolution','videoFps','videoRenderer'])assert.equal(next[key],config[key]);
 assert.deepEqual(voices(buildPlan(next,evidence)),voices(buildPlan(config,evidence)));assert.deepEqual(config,before);
});
test('changing measured profiles in installed mode cannot silently switch to experimental scheduling or best count',()=>{
 const single=selectScenarioGPUConfig(realisticPlanningConfig(config,evidence),evidence,'4090');
 assert.equal(single.policy,'current');assert.equal(single.imageWorkers,'1');assert.equal(single.executionMode,'resident');
 const dual=selectScenarioGPUConfig({...config,policy:'current',productionModelCopies:2,productionRequestSlots:4,imageWorkers:'4'},evidence,'5090');
 assert.equal(dual.policy,'current');assert.equal(dual.imageWorkers,'4');assert.equal(dual.productionRequestSlots,4);assert.equal(dual.executionMode,'hybrid');
});
test('unsupported installed topology stays explicit instead of falling back to another benchmark',()=>{
 const input={...config,policy:'current',productionModelCopies:2,productionRequestSlots:4,imageWorkers:'4'};
 const before=structuredClone(input);assert.throws(()=>selectScenarioGPUConfig(input,evidence,'a6000'),/completed|measurement|profile/i);assert.deepEqual(input,before);
});
test('missing optional director calibration retains a valid conservative historical fallback',()=>{
 const {directorCalibration,...withoutCalibration}=evidence;
 const next=realisticPlanningConfig({...DEFAULTS,resolution:'legacy',directorProfile:'historical'},withoutCalibration);
 assert.equal(next.apiSlots,3);assert.equal(next.directorActualSlots,3);assert.equal(next.qc,'practical');
 assert.deepEqual(schedule(buildPlan(next,withoutCalibration)).diagnostics,[]);
});
test('Practical and Strict retain every-image initial count, accounting and voice; later repair differences remain unknown',()=>{
 const c=realisticPlanningConfig(config,evidence),a=buildPlan(c,evidence),b=buildPlan({...c,qc:'strict'},evidence),ra=schedule(a),rb=schedule(b);
 assert.equal(ra.qcCount,a.attemptCount);assert.equal(rb.qcCount,ra.qcCount);assert.equal(rb.qcUSD,ra.qcUSD);assert.equal(rb.end,ra.end);
 assert.deepEqual(voices(a),voices(b));assert.match(reviewPolicyNote(c,ra,evidence),/both review every image/);assert.match(reviewPolicyNote(c,ra,evidence),/not measured/);
});
