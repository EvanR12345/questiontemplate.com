import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {standardLunaHTML,lunaAuditHTML,lunaRequestPage} from './pipeline-luna-efficiency.mjs';

test('complete historical audit reconciles every anonymous request, feature and experiment',()=>{
  const audit=JSON.parse(fs.readFileSync(new URL('./pipeline-luna-token-audit.json',import.meta.url)));
  assert.equal(audit.inputTokens,3354362);assert.equal(audit.requests,618);
  assert.equal(audit.requestRows.length,618);
  for(const [field,total] of [['inputTokens',audit.inputTokens],['outputTokens',audit.outputTokens],['cachedInputTokens',0],['estimatedUSD',audit.estimatedUSD]]){
    assert.ok(Math.abs(audit.requestRows.reduce((n,r)=>n+r[field],0)-total)<1e-8);
    assert.ok(Math.abs(audit.stages.reduce((n,r)=>n+r[field],0)-total)<1e-8);
  }
  assert.equal(new Set(audit.requestRows.map(r=>r.number)).size,618);
  const serialized=JSON.stringify(audit);
  for(const privateField of ['requestId','privateRun','openaiApiKey','sourceQuote','referenceImages','sourceText'])assert.equal(serialized.includes(privateField),false);
});

test('receipt viewer filters and pages without rendering hundreds of rows on startup',()=>{
  const data={requestRows:Array.from({length:61},(_,n)=>({number:n+1,experiment:n%2+1,
    feature:n%2?'Review':'Planning',tier:'default',inputTokens:100,outputTokens:30,cachedInputTokens:0,
    streamSeconds:null,estimatedUSD:.001}))};
  let result=lunaRequestPage(data);
  assert.equal(result.count,61);assert.equal(result.page,0);
  assert.equal((result.html.match(/<tr>/g)||[]).length,26);
  result=lunaRequestPage(data,{stage:'Review',experiment:2,page:99});
  assert.equal(result.count,30);assert.equal(result.page,1);assert.ok(result.html.includes('Unknown'));
  assert.equal(lunaRequestPage(data,{stage:'missing',page:-1}).count,0);
  assert.deepEqual(data.requestRows.map(r=>r.number),Array.from({length:61},(_,n)=>n+1));
});

test('audit explains exact accounting boundary, overlapping work and removal risks',()=>{
  const audit=JSON.parse(fs.readFileSync(new URL('./pipeline-luna-token-audit.json',import.meta.url)));
  const html=lunaAuditHTML(audit);
  for(const phrase of ['618','21 experiments','not one video','0 remain unassigned','cannot be recovered','characters are not tokens','does not necessarily save','filterable audit'])assert.ok(html.includes(phrase));
  assert.ok(html.length<25000);
  for(const stage of audit.stages)assert.ok(stage.removal.length>20);
});

test('Standard projections retain isolated costs without failed-run projections or universal speed claims',()=>{
  const baseline={label:'Classic',seconds:346,calls:50,inputTokens:200000,outputTokens:40000,shots:112,estimatedUSD:.0458};
  const data={sourceNarrationSeconds:1050.23,baseline,runs:[{...baseline,label:'Lean',seconds:200}],
    attempts:[{label:'Rejected <prototype>',status:'FAILED',seconds:20,completedChapters:0,estimatedUSD:.004}],
    spentUSD:.4,capUSD:1,experiments:2,pendingUsage:false};
  const before=structuredClone(data);const html=standardLunaHTML(data,120);
  assert.ok(html.includes((.0458*7200/1050.23).toFixed(4)));
  assert.ok(html.includes('not prove statistical significance'));
  assert.ok(html.includes('&lt;prototype&gt;'));
  assert.ok(html.includes('changed shots and immediate neighbors'));
  assert.ok(html.includes('extrapolations'));assert.deepEqual(data,before);
  for(const duration of [0,-1,NaN,Infinity])assert.throws(()=>standardLunaHTML(data,duration));
});
