import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,importSnapshot} from './pipeline-engine.mjs';
import {latestProfiles} from './pipeline-measurements.mjs';
import {startupCandidates,imageAccountingHTML,ALIGNMENT_BUFFER_SECONDS} from './pipeline-detail-ui.mjs';
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
 assert.equal(candidates.filter(v=>!v.balanced).length,4);
 for(const v of candidates){assert.deepEqual(v.result.diagnostics,[]);assert.equal(v.plan.attemptCount,candidates[0].plan.attemptCount);assert.ok(Number.isFinite(v.result.totalUSD));assert.ok(v.plan.tasks.find(t=>t.id==='boot').deps.includes('c'+v.readyChapters+'-handoff'));}
 assert.ok(candidates.some(v=>v.pareto));
 assert.throws(()=>buildPlan({...config,chapters:2,readyChapters:3},evidence),/cannot exceed/);
});
test('timed startup reduces waiting without delaying earliest image delivery or video finish',()=>{
 const candidates=startupCandidates(config,evidence),baseline=candidates[0],timed=candidates.find(v=>v.balanced);
 assert.ok(timed);assert.ok(timed.bootStart>baseline.bootStart+1);
 assert.ok(timed.imageEnd<=baseline.imageEnd+.5);assert.ok(timed.result.end<=baseline.result.end+.5);
 assert.ok(timed.result.rentalSeconds<baseline.result.rentalSeconds);assert.ok(timed.result.gpuUSD<baseline.result.gpuUSD);
 assert.deepEqual(timed.result.diagnostics,[]);assert.equal(timed.plan.attemptCount,baseline.plan.attemptCount);
 const restored=importSnapshot({type:'studio-pipeline-plan',version:1,config:timed.plan.config,preferences:{}},evidence);
 const r=schedule(buildPlan(restored.config,evidence));assert.equal(r.tasks.find(t=>t.id==='boot').start,timed.bootStart);
});
test('timed boot cannot bypass chapter readiness, and rendering is outside GPU stop dependencies',()=>{
 const p=buildPlan({...config,gpuStartSeconds:10},evidence),r=schedule(p),boot=r.tasks.find(t=>t.id==='boot');
 assert.ok(boot.start>=r.tasks.find(t=>t.id==='c1-handoff').end);assert.ok(boot.start>=10);
 assert.ok(!p.tasks.find(t=>t.id==='stop').deps.some(id=>id.endsWith('-render')));
 assert.throws(()=>buildPlan({...config,gpuStartSeconds:-1},evidence),/GPU boot time/);
 assert.throws(()=>buildPlan({...config,gpuStartSeconds:Infinity},evidence),/GPU boot time/);
});
test('aligned completion offers the later rental start and shows its rendering tradeoff',()=>{
 const candidates=startupCandidates(config,evidence),baseline=candidates[0],fast=candidates.find(v=>v.balanced&&v.keepFastestVideo),aligned=candidates.find(v=>v.balanced&&!v.keepFastestVideo);
 assert.ok(aligned);assert.ok(aligned.bootStart>fast.bootStart);
 assert.ok(aligned.imageEnd<=baseline.imageEnd+.5);assert.ok(aligned.gap<=baseline.gap+.5);
 assert.ok(aligned.result.gpuUSD<fast.result.gpuUSD);assert.ok(aligned.result.gpuIdleSeconds<fast.result.gpuIdleSeconds);
 assert.ok(aligned.result.end>fast.result.end);assert.deepEqual(aligned.result.diagnostics,[]);
});
test('aligned rental matches the last-chapter billed interval with at most a 15 second buffer',()=>{
 const values=startupCandidates(config,evidence),aligned=values.find(v=>v.balanced&&!v.keepFastestVideo),last=values.find(v=>!v.balanced&&v.readyChapters===config.chapters);
 assert.equal(aligned.bufferSeconds,15);assert.equal(ALIGNMENT_BUFFER_SECONDS,15);
 assert.ok(aligned.result.rentalSeconds<=last.result.rentalSeconds+15.001);
 assert.ok(aligned.result.gpuUSD<=last.result.gpuUSD+15.001/3600*aligned.plan.gpu.hourly);
 assert.ok(aligned.imageEnd<last.imageEnd);assert.ok(aligned.bootStart<last.bootStart);
 assert.equal(aligned.plan.attemptCount,last.plan.attemptCount);assert.deepEqual(aligned.result.diagnostics,[]);
 // Regression: the old binary deadline stopped near 1679s; a later start is feasible.
 assert.ok(aligned.bootStart>2000&&aligned.bootStart<2120);
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
