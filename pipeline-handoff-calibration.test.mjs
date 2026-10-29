import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule} from './pipeline-engine.mjs';
import {directorForecast} from './pipeline-director-calibration.mjs';
import {validateFastInputs} from './pipeline-fast-scheduler.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url),'utf8'));
function fixture(marker){
 const data=structuredClone(evidence);
 data.directorCalibration={sourceNarrationSeconds:1050.23,sourceWords:2604,profiles:[{
  id:'lean-overlap-standard',label:'Current · Standard',status:'COMPLETE',seconds:322.734,
  estimatedUSD:.047590007,calls:35,shots:103,slots:4,installed:true,
  sourceChapters:2,completedChapters:2,unknownRequests:0,...marker}]};
 return {data,config:{...DEFAULTS,minutes:17.503833333333333,chapters:2,intro:false,
  directorProfile:'lean-overlap-standard',directorCostMode:'measured',directorActualSlots:4}};
}
test('raw calibration handoff marker preserves dependency without adding commit time twice',()=>{
 const {data,config}=fixture({includesHandoffCommitTime:true});
 const plan=buildPlan(config,data),forecast=directorForecast(config,data),result=schedule(plan);
 assert.equal(forecast.includesHandoffCommitTime,true);
 const h=plan.tasks.find(t=>t.id==='c1-handoff'),chapter=plan.tasks.find(t=>t.id==='c1-chapter');
 assert.equal(h.duration,0);assert.deepEqual(h.deps,[chapter.id]);
 assert.deepEqual(h.resources,{});assert.equal(h.dependencyOnly,true);
 assert.ok(plan.tasks.find(t=>t.id==='c2-chapter').deps.includes(h.id));
 assert.match(h.basis,/already included/);assert.deepEqual(result.diagnostics,[]);
 assert.ok(Math.abs(plan.tasks.filter(t=>t.kind==='chapter').reduce((n,t)=>n+t.duration,0)-322.734)<1e-8);
});
test('zero-duration scheduler exception is restricted to included-time handoff dependencies',()=>{
 const {data,config}=fixture({includesHandoffCommitTime:true});
 const original=buildPlan(config,data);
 for(const change of [t=>delete t.dependencyOnly,t=>t.kind='chapter',t=>t.resources={disk:1},t=>t.duration=-1]){
  const plan=structuredClone(original),handoff=plan.tasks.find(t=>t.id==='c1-handoff');
  change(handoff);assert.throws(()=>validateFastInputs(plan),/duration/);
 }
});
test('legacy and nonboolean markers retain the separate 0.3-second per-chapter allowance',()=>{
 for(const marker of [{},{includesHandoffCommitTime:false},{includesHandoffCommitTime:'true'}]){
  const {data,config}=fixture(marker),plan=buildPlan(config,data);
  assert.equal(directorForecast(config,data).includesHandoffCommitTime,false);
  assert.deepEqual(plan.tasks.filter(t=>/^c\d+-handoff$/.test(t.id)).map(t=>t.duration),[.3,.3]);
 }
});
