import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {buildPlan,describePlan,schedule,DEFAULTS} from './pipeline-engine.mjs';
import {selectGPUConfig} from './pipeline-config.mjs';
import {compareGPUPlans} from './pipeline-gpu-comparison.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
evidence.concurrency=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url)));
const input={...DEFAULTS,minutes:1440,chapters:80,gpu:'5090',resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',measurementAttempt:'5090-attempt-8'};
test('metadata-only selection preserves the exact measurement without building timeline arrays',()=>{
  const full=buildPlan(input,evidence),description=describePlan(input,evidence);
  assert.deepEqual(description.config,full.config);assert.deepEqual(description.gpu,full.gpu);
  assert.deepEqual(description.measurement,full.measurement);assert.equal(description.tasks,undefined);
});
test('worker chart summaries retain exact simulated time and cost for the full long scenario',()=>{
  const summary=compareGPUPlans(input,evidence),chosen=summary.comparisons.find(v=>v.g.id==='5090');
  const plan=buildPlan(selectGPUConfig(input,evidence,'5090'),evidence),result=schedule(plan);
  assert.equal(chosen.r.end,result.end);assert.equal(chosen.r.totalUSD,result.totalUSD);
  assert.equal(chosen.p.measurement.imagesPerSecond,plan.measurement.imagesPerSecond);
  assert.ok(summary.comparisons.length>=10);assert.ok(!JSON.stringify(summary).includes('"tasks"'));
  assert.ok(JSON.stringify(summary).length<100_000);
});
test('unavailable measurements stay unavailable rather than receiving substituted speed',()=>{
  const summary=compareGPUPlans({...input,resolution:'1080p'},evidence);
  for(const unavailable of summary.unavailable)assert.ok(!summary.comparisons.some(v=>v.g.id===unavailable.id));
  assert.throws(()=>describePlan({...input,videoFps:120},evidence),/calibrated/);
});
