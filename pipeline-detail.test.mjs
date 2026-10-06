import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {latestProfiles} from './pipeline-measurements.mjs';
import {startupCandidates,imageAccountingHTML} from './pipeline-detail-ui.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url),'utf8'));
evidence.concurrency=JSON.parse(fs.readFileSync(new URL('./pipeline-concurrency.json',import.meta.url),'utf8'));
const details=JSON.parse(fs.readFileSync(new URL('./pipeline-details.json',import.meta.url),'utf8'));
const config={...DEFAULTS,gpu:'5090',resolution:'720p',encodingProfile:'fresh',executionMode:'hybrid',measurementAttempt:'5090-attempt-8'};
test('hybrid clients use the measured rate once, with two copies and four slots',()=>{
 const p=buildPlan(config,evidence),r=schedule(p);
 assert.equal(p.measurement.workers,2);assert.equal(p.measurement.clientSlots,4);
 assert.equal(p.measurement.executionMode,'hybrid');
 assert.ok(p.measurement.freshEncodingVerified);
 const groups=p.tasks.filter(t=>t.kind==='delivered');
 assert.ok(Math.abs(groups.reduce((n,t)=>n+t.duration,0)-p.attemptCount/p.measurement.imagesPerSecond)<1e-8);
 assert.deepEqual(r.diagnostics,[]);
});
test('startup alternatives preserve work and dependencies and report finite estimates',()=>{
 const candidates=startupCandidates({...config,chapters:4},evidence);
 assert.equal(candidates.length,3);
 for(const v of candidates){assert.deepEqual(v.result.diagnostics,[]);assert.equal(v.plan.attemptCount,candidates[0].plan.attemptCount);assert.ok(Number.isFinite(v.result.totalUSD));assert.ok(v.plan.tasks.find(t=>t.id==='boot').deps.includes('c'+v.readyChapters+'-handoff'));}
 assert.ok(candidates.some(v=>v.pareto));
 assert.throws(()=>buildPlan({...config,chapters:2,readyChapters:3},evidence),/cannot exceed/);
});
test('priority survives export/import while enforced resource dependencies remain intact',()=>{
 const input={type:'studio-pipeline-plan',version:1,config,preferences:{'c1-voice':{notBefore:10,priority:-3}}};
 const state=importSnapshot(input,evidence);assert.equal(state.preferences['c1-voice'].priority,-3);
 assert.deepEqual(schedule(buildPlan(state.config,evidence),state.preferences).diagnostics,[]);
 assert.throws(()=>importSnapshot({...input,preferences:{'c1-voice':{notBefore:0,priority:Infinity}}},evidence),/priority/);
});
test('absent hybrid profiles do not borrow serial or pipeline measurements',()=>{
 assert.deepEqual(latestProfiles(evidence.concurrency.gpus.find(g=>g.id==='a40'),'720p','fresh','hybrid'),[]);
 assert.throws(()=>buildPlan({...config,gpu:'a40',measurementAttempt:'latest'},evidence),/No completed/);
});
test('accounting renders real missing timings without pretending nested server duration is additive',()=>{
 const g=evidence.concurrency.gpus.find(g=>g.id==='5090'),p=latestProfiles(g,'720p','fresh','hybrid')[0];
 const html=imageAccountingHTML(p,g);assert.match(html,/nested within Wait/);assert.match(html,/Unassigned/);
 for(const t of p.traces)for(const j of t.jobs){assert.ok(Math.abs(j.accounting.closureErrorSeconds)<1e-9);assert.ok(j.serverWorker>=0&&j.serverWorker<2);}
 assert.equal(imageAccountingHTML(null,g),'');
});
test('public function inventory and observed traces have no story, references or credentials',()=>{
 assert.equal(details.modules.length,25);assert.ok(details.functions.length>=400);
 assert.equal(details.observed[0].images,12);assert.ok(details.observed[0].wallSeconds>details.observed[1].wallSeconds);
 assert.ok(!/rpa_|sk-[A-Za-z0-9]|@gmail|C:\\\\Users|referenceImages|cleanNarrationText/i.test(JSON.stringify(details)));
 const ids=new Set(details.functions.map(f=>f.id));assert.equal(ids.size,details.functions.length);
});
test('display task explosion is refused rather than freezing the planner',()=>{
 assert.throws(()=>buildPlan({...config,minutes:360,cadence:20,batch:1},evidence),/Too many display tasks/);
});
