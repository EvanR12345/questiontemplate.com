import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {gpuChoices,selectGPUConfig,executionLabel} from './pipeline-config.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
evidence.concurrency=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url)));
const config={...DEFAULTS,resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',measurementAttempt:'5090-attempt-8'};

test('switching from 5090 hybrid to A6000 selects measured resident workers',()=>{
  const chosen=selectGPUConfig(config,evidence,'a6000'),p=buildPlan(chosen,evidence);
  assert.equal(chosen.executionMode,'resident');
  assert.equal(chosen.measurementAttempt,'latest');
  assert.equal(p.measurement.workers,3);
  assert.deepEqual(schedule(p).diagnostics,[]);
});
test('4090 switches to its measured pipeline without changing resolution or checks',()=>{
  const chosen=selectGPUConfig({...config,qc:'sampled',retries:7},evidence,'4090'),p=buildPlan(chosen,evidence);
  assert.equal(chosen.executionMode,'pipeline');assert.equal(p.measurement.clientSlots,2);
  for(const key of ['resolution','encodingProfile','minutes','cadence','intro','directorCost'])assert.equal(chosen[key],config[key]);
  assert.equal(chosen.qc,'sampled');assert.equal(chosen.retries,7);
});
test('every completed fresh 720p GPU can be selected from the hybrid default',()=>{
  let checked=0;
  for(const g of gpuChoices(evidence)) {
    const m=evidence.concurrency.gpus.find(x=>x.id===g.id);
    if(!['productionProfiles','pipelineProfiles','hybridProfiles'].some(k=>m?.[k]?.some(p=>p.resolution==='720p'&&p.encodingProfile==='fresh'&&p.rounds>=2&&!p.postControl)))continue;
    const p=buildPlan(selectGPUConfig(config,evidence,g.id),evidence);
    assert.ok(p.measurement.rounds>=2);assert.equal(p.measurement.resolution,'720p');
    checked++;
  }
  assert.ok(checked>=18);
});
test('A5000 cannot silently substitute legacy images for missing 720p evidence',()=>{
  assert.throws(()=>selectGPUConfig(config,evidence,'a5000'),/No completed 720p/);
  assert.equal(config.gpu,'5090');assert.equal(config.executionMode,'hybrid');
});
test('explicit legacy profiles remain available',()=>{
  const chosen=selectGPUConfig({...DEFAULTS},evidence,'a5000');
  assert.equal(buildPlan(chosen,evidence).gpu.id,'a5000');
  assert.equal(chosen.resolution,'legacy');
});
test('1080p selection uses completed 1080p measurements instead of pixel scaling',()=>{
  const chosen=selectGPUConfig({...config,resolution:'1080p'},evidence,'4090');
  const p=buildPlan(chosen,evidence);
  assert.equal(p.measurement.resolution,'1080p');assert.match(executionLabel(p),/images\/min/);
});
test('incomplete and control rounds cannot win selection',()=>{
  const modified=structuredClone(evidence),g=modified.concurrency.gpus.find(g=>g.id==='a6000');
  const source=g.productionProfiles.find(p=>p.resolution==='720p'&&p.encodingProfile==='fresh'&&p.rounds===2);
  g.hybridProfiles=[{...source,executionMode:'hybrid',workers:6,clientSlots:6,imagesPerSecond:999,rounds:1},
    {...source,executionMode:'hybrid',workers:6,clientSlots:6,imagesPerSecond:999,postControl:true}];
  assert.equal(selectGPUConfig(config,modified,'a6000').executionMode,'resident');
});
