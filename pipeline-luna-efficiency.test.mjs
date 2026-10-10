import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {standardLunaHTML,lunaAuditHTML,lunaRequestPage,chapterRequestHTML,flexLunaHTML} from './pipeline-luna-efficiency.mjs';
test('Flex evidence retains failed and standby-contaminated results without promising their latency',()=>{
  const data=JSON.parse(fs.readFileSync(new URL('./pipeline-luna-flex.json',import.meta.url)));
  const html=flexLunaHTML(data);
  for(const text of ['Excluded · laptop standby','Excluded · failed','Limited history','remains reserved','no automatic Standard fallback'])assert.ok(html.includes(text));
  assert.ok(data.combinedSettledUSD+data.combinedReservedUSD<=data.totalCapUSD);
  assert.equal(data.conditions.filter(r=>r.timingEligible).length,1);
});

test('chapter request evidence distinguishes failures, grouping, active time and preserved production',()=>{
  const data={conditions:[{condition:'whole <probe>',status:'FAILED',chapters:[],shots:0,
    seconds:100,requests:4,inputTokens:20000,outputTokens:5000,estimatedUSD:.01,
    stages:[{name:'Visual storyboard director',calls:1,inputTokens:9000,outputTokens:4000,
      activeSeconds:60,elapsedSpanSeconds:80,estimatedUSD:.003}]}]};
  const before=structuredClone(data),html=chapterRequestHTML(data);
  assert.ok(html.includes('whole &lt;probe&gt;'));assert.ok(html.includes('FAILED'));
  assert.ok(html.includes('0 / 2'));assert.ok(html.includes('time until rejection'));
  assert.ok(html.includes('1m 0.0s'));assert.ok(!html.includes('1m 20.0s'));
  assert.ok(html.includes('response format constant'));assert.ok(html.includes('No production workflow'));
  assert.deepEqual(data,before);assert.equal(chapterRequestHTML(null),'');
});

test('chapter study keeps unknown API usage reserved and all completed comparisons distinct',()=>{
  const data=JSON.parse(fs.readFileSync(new URL('./pipeline-luna-standard.json',import.meta.url)));
  const study=data.chapterRequestStudy;
  assert.ok(study.conditions.length>=5);
  assert.equal(data.pendingUsage,true);assert.equal(study.unknownRequests,1);
  assert.ok(data.spentUSD+data.reservedUSD<=data.capUSD);
  assert.equal(study.conditions.reduce((n,r)=>n+r.reservedUSD,0),data.reservedUSD);
  for(const name of ['whole-chapter-hybrid','small-groups-hybrid']){
    const r=study.conditions.find(r=>r.condition===name);
    assert.equal(r.status,'COMPLETE');assert.equal(r.chapters.length,2);
    assert.equal(r.unknownRequests,0);assert.ok(r.sourcePreserved);
  }
  const html=standardLunaHTML(data);
  assert.ok(html.includes('Usage unresolved'));assert.ok(html.includes('reserved unknown cost')||html.includes('Reserved unknown cost'));
  assert.ok(html.includes(data.reservedUSD.toFixed(4)));
  const text=JSON.stringify(study);
  for(const key of ['apiKey','requestId','responseId','privateRun','sourceText','referenceImages'])assert.ok(!text.includes(`"${key}"`));
});

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
