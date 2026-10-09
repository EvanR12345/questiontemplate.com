import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {priceGPU,costParts,POD_RATES,PRICING_URL,PRICING_DATE} from './pipeline-pricing.mjs';
import {buildPlan,schedule,DEFAULTS,validateConfig} from './pipeline-engine.mjs';
const config={...DEFAULTS},gpu={id:'5090',hourly:1.018,date:'2026-10-05'};
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
test('published compute adds configured running disk once and preserves receipt',()=>{
  const priced=priceGPU(gpu,config);
  assert.equal(priced.hourly,1.19+20*.10/720);
  assert.equal(priced.historicalHourly,1.018);assert.equal(gpu.hourly,1.018);
  assert.equal(priced.pricing.basis,'published');assert.equal(PRICING_DATE,'2026-10-09');
  assert.equal(PRICING_URL,'https://www.runpod.io/pricing');assert.equal(POD_RATES.pro96,2.49);
});
test('historical mode preserves the combined receipt and does not add disk again',()=>{
  const priced=priceGPU(gpu,{...config,pricingBasis:'receipt',containerGB:200});
  assert.equal(priced.hourly,1.018);assert.equal(priced.pricing.basis,'receipt');
});
test('unlisted partitions stay explicitly historical rather than inheriting another GPU price',()=>{
  const priced=priceGPU({...gpu,id:'gpu-b300-sxm6-ac-mig-1g-34gb'},config);
  assert.equal(priced.hourly,1.018);assert.match(priced.pricing.note,/No unambiguous current/);
});
test('a custom quote applies only to its exact GPU and excludes running disk',()=>{
  const quoted={...config,pricingBasis:'quote',quotedGPU:'5090',quotedGPUHourly:.80};
  assert.equal(priceGPU(gpu,quoted).hourly,.8+20*.1/720);
  const other=priceGPU({...gpu,id:'4090'},quoted);
  assert.equal(other.hourly,.89+20*.1/720);assert.equal(other.pricing.basis,'published');
});
test('run and shared storage costs are distinct and allocation is explicit',()=>{
  assert.deepEqual(costParts(3600,1.2,.25,.05,.046666,2),{
    gpuUSD:1.2,apiUSD:.3,productionUSD:1.5,storageUSD:.093332,totalUSD:1.593332});
  assert.equal(costParts(3600,1.2,.25,.05,.046666,0).totalUSD,1.5);
});
test('changing price basis changes cost, never measured timing or task work',()=>{
  const input={...DEFAULTS,minutes:120,chapters:80};
  const current=buildPlan(input,evidence),historical=buildPlan({...input,pricingBasis:'receipt'},evidence);
  const a=schedule(current),b=schedule(historical);
  assert.deepEqual(current.tasks,historical.tasks);assert.equal(a.end,b.end);
  assert.notEqual(a.gpuUSD,b.gpuUSD);assert.equal(a.totalUSD,a.productionUSD+a.storageUSD);
});
test('invalid financial settings do not produce negative or infinite totals',()=>{
  for(const key of ['containerGB','storageDays','quotedGPUHourly'])for(const value of [-1,Infinity,NaN])
    assert.throws(()=>validateConfig({...DEFAULTS,[key]:value},evidence));
  assert.throws(()=>validateConfig({...DEFAULTS,pricingBasis:'random'},evidence));
});
