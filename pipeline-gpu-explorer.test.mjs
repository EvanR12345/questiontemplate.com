import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {explorerRows,explorerFields,queryExplorer,compareValues,explorerCSV,EXPLORER_DEFAULTS,projectMetrics} from './pipeline-gpu-explorer.mjs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
const data=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url))),rows=explorerRows(data);
const evidence={...JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url))),concurrency:data};
const config=overrides=>({...structuredClone(EXPLORER_DEFAULTS),...overrides});

test('best-results view keeps all 49 GPUs, with missing performance clearly absent',()=>{
  const result=queryExplorer(rows,config());
  assert.equal(result.length,49);assert.equal(result.filter(r=>r.coverage==='Completed').length,20);
  for(const row of result.filter(r=>r.coverage==='Unmeasured'))assert.equal(row.rate,undefined);
  assert.equal(new Set(result.map(r=>r.gpuId)).size,49);
});
test('the top speed uses a completed fresh 720p test rather than a cached control',()=>{
  const result=queryExplorer(rows,config());
  assert.equal(result[0].gpuId,'pro96');assert.equal(result[0].copies,4);
  assert.equal(result[0].rate.toFixed(1),'40.9');assert.equal(result[0].conditioning,'fresh');
  assert.ok(result.filter(r=>r.coverage==='Completed').every(r=>r.profile.rounds>=2&&!r.profile.postControl));
});
test('numeric sorting works in both directions and keeps missing values last',()=>{
  const result=queryExplorer(rows,config({sort:[{field:'warmCost',direction:'asc'}]}));
  assert.equal(result[0].gpuId,'5090');assert.equal(result.at(-1).coverage,'Unmeasured');
  assert.equal(compareValues(null,5,'asc'),1);assert.equal(compareValues(null,5,'desc'),1);
  assert.ok(compareValues(9,100,'asc')<0);assert.ok(compareValues('GPU 2','GPU 10','asc')<0);
});
test('sort ties are resolved by subsequent rules without changing which test is best',()=>{
  const fixture=[{id:'b',gpuId:'b',gpu:'B',coverage:'Completed',resolution:'720p',conditioning:'fresh',method:'resident',rate:2,vram:48},
    {id:'a',gpuId:'a',gpu:'A',coverage:'Completed',resolution:'720p',conditioning:'fresh',method:'resident',rate:2,vram:96},
    {id:'c',gpuId:'b',gpu:'B',coverage:'Completed',resolution:'720p',conditioning:'fresh',method:'resident',rate:1,vram:96}];
  const result=queryExplorer(fixture,config({sort:[{field:'rate',direction:'desc'},{field:'vram',direction:'desc'}]}));
  assert.deepEqual(result.map(r=>r.id),['a','b']);
});
test('any-field numeric and missing filters distinguish zero from absence',()=>{
  const result=queryExplorer(rows,config({coverage:'completed',rules:[{field:'vram',op:'gte',value:'48'},{field:'hardware.cudaVariant',op:'exists',value:''}]}));
  assert.ok(result.length>0);assert.ok(result.every(r=>r.vram>=48&&r['hardware.cudaVariant']));
  assert.deepEqual(queryExplorer(rows,config({rules:[{field:'rate',op:'gte',value:''}]})),[]);
  const absent=queryExplorer(rows,config({rules:[{field:'rate',op:'missing',value:''}]}));assert.equal(absent.length,29);
});
test('region, VRAM, price and string filters preserve the exact host settings',()=>{
  const result=queryExplorer(rows,config({view:'tests',region:'EU-RO-1',minVRAM:'32',maxHourly:'1.10',search:'5090',rules:[{field:'method',op:'equals',value:'hybrid'}]}));
  assert.ok(result.length>0);assert.ok(result.every(r=>r.region==='EU-RO-1'&&r.gpuId==='5090'&&r.hourly<=1.10&&r.method==='hybrid'));
  for(const r of result)assert.equal(r.hardware.attempt,r.attempt);
});
test('test view exposes archived hosts without averaging or pooling them',()=>{
  const result=queryExplorer(rows,config({view:'tests',search:'5090',coverage:'completed'}));
  assert.ok(result.length>1);assert.ok(new Set(result.map(r=>r.attempt)).size>1);
  assert.ok(result.every(r=>r.rate===r.profile.imagesPerSecond*60));
});
test('every public scalar settings and telemetry field is selectable without expanding per-image traces',()=>{
  const fields=explorerFields(rows).map(f=>f.key);
  assert.ok(fields.length>=160);assert.ok(fields.includes('hardware.cudaVariant'));assert.ok(fields.includes('test.deviceTelemetry.metrics.utilizationPercent.mean'));
  assert.ok(fields.includes('test.meanProviderStages.retrievalAndValidationSeconds'));
  assert.ok(fields.includes('model'));assert.ok(fields.includes('generatedHeight'));
  assert.ok(!fields.some(k=>k.includes('traces')));assert.ok(!fields.includes('profile'));
});
test('CSV preserves decimals, quotes and missing cells and neutralizes spreadsheet formulas',()=>{
  const csv=explorerCSV([{gpu:'=HYPERLINK("bad")',rate:38.04,missing:null}], [{key:'gpu',label:'GPU'},{key:'rate',label:'Rate'},{key:'missing',label:'Missing'}]);
  assert.equal(csv,'"GPU","Rate","Missing"\r\n"\'=HYPERLINK(""bad"")","38.04",""');
});
test('project rental cost includes the full billed interval once and excludes API and storage',()=>{
  const row=queryExplorer(rows,config({search:'5090'}))[0],cfg={...DEFAULTS,policy:'proposed'},metrics=projectMetrics(row,cfg,evidence);
  const plan=buildPlan({...cfg,gpu:row.gpuId,resolution:row.resolution,encodingProfile:row.conditioning,executionMode:row.method,imageWorkers:String(row.slots),measurementAttempt:row.attempt},evidence),result=schedule(plan);
  assert.equal(metrics.projectRental,result.rentalSeconds/60);
  assert.equal(metrics.projectGPUCost,result.gpuUSD);
  assert.equal(metrics.projectIdle,(result.rentalSeconds-result.gpuWork)/60);
  assert.ok(metrics.projectGPUCost<result.totalUSD);
  assert.equal(metrics.projectRendering,result.tasks.filter(t=>t.lane==='render').reduce((s,t)=>s+t.duration,0)/60);
});
test('project estimates respond to video length and serial rental excludes cached downloads',()=>{
  const row=queryExplorer(rows,config({search:'5090'}))[0];
  const cfg={...DEFAULTS,policy:'serial'},cold=projectMetrics(row,cfg,evidence),warm=projectMetrics(row,{...cfg,warmCache:true},evidence),short=projectMetrics(row,{...cfg,minutes:60},evidence);
  assert.ok(warm.projectRental<cold.projectRental);assert.ok(warm.projectGPUCost<cold.projectGPUCost);
  assert.ok(short.projectRendering<cold.projectRendering);assert.ok(short.projectRental<cold.projectRental);
});
test('unmeasured and incomplete configurations have no invented project estimates',()=>{
  for(const row of rows.filter(r=>r.coverage!=='Completed'))assert.deepEqual(projectMetrics(row,DEFAULTS,evidence),{});
});
test('project cost sorting and field filters use per-configuration estimates',()=>{
  const metrics=row=>projectMetrics(row,DEFAULTS,evidence);
  const result=queryExplorer(rows,config({sort:[{field:'projectGPUCost',direction:'asc'}]}),metrics);
  const measured=result.filter(r=>r.coverage==='Completed');assert.ok(measured.every((r,i)=>!i||r.projectGPUCost>=measured[i-1].projectGPUCost));
  assert.equal(result.at(-1).projectGPUCost,undefined);
  const limited=queryExplorer(rows,config({rules:[{field:'projectRental',op:'lte',value:'60'}]}),metrics);
  assert.ok(limited.length);assert.ok(limited.length<measured.length);assert.ok(limited.every(r=>r.projectRental<=60));
});
