import test from 'node:test';
import assert from 'node:assert/strict';
import {updateHTML,queuePart,renderTimingHistory,timingPageHTML,timingSummaryHTML} from './studio-queue-view.mjs';

class Element {
  constructor(){this.children=[];this.dataset={};this.nodes=new Map();this.listeners={};this.writes=0;this.html='';this.ownerDocument={createElement:()=>new Element()};}
  set innerHTML(value){this.html=value;this.writes++;this.nodes.clear();}
  get innerHTML(){return this.html;}
  querySelector(selector){if(!this.html)return null;if(!this.nodes.has(selector))this.nodes.set(selector,new Element());return this.nodes.get(selector);}
  querySelectorAll(){return [];}
  addEventListener(name,fn){this.listeners[name]=fn;}
  append(node){this.children.push(node);}
}

test('unchanged polling leaves control nodes alone, while changed sections update independently',()=>{
  const container=new Element(),controls=queuePart(container,'controls'),message=queuePart(container,'message');
  updateHTML(controls,'Pause / Resume');updateHTML(message,'Preparing shot 1');
  for(let i=0;i<100;i++){updateHTML(controls,'Pause / Resume');updateHTML(message,'Preparing shot '+i);}
  assert.equal(controls.writes,1);assert.equal(message.writes,101);
  assert.equal(queuePart(container,'controls'),controls);assert.equal(container.children.length,2);
});

test('closed timing history reads no entries; open history paginates and avoids repeat formatting',()=>{
  let reads=0;
  const rows=Array.from({length:10000},(_,i)=>({get stage(){reads++;return 'Pass '+i;},seconds:1,detail:true}));
  const target=new Element(),run={timings:rows};
  renderTimingHistory(target,'project-a',run);
  const outer=target.querySelector('[data-ui="production-times"]'),inner=target.querySelector('[data-ui="production-passes"]');
  const entries=target.querySelector('[data-timing-entries]');
  for(let i=0;i<100;i++)renderTimingHistory(target,'project-a',run);
  assert.equal(reads,0);assert.equal(target.writes,1);
  outer.open=true;outer.listeners.toggle();assert.equal(reads,0,'detail rows are not formatted in the summary');
  inner.open=true;inner.listeners.toggle();assert.equal(reads,100);assert.match(entries.innerHTML,/1–100 of 10000/);
  for(let i=0;i<100;i++)renderTimingHistory(target,'project-a',run);
  assert.equal(reads,100);assert.equal(entries.writes,1);
  entries.listeners.click({target:{closest:()=>({dataset:{timingPage:'next'}})}});
  assert.match(entries.innerHTML,/101–200 of 10000/);assert.match(entries.innerHTML,/Pass 199/);assert.equal(reads,200);
  rows.push({stage:'New live result',seconds:2,detail:true});renderTimingHistory(target,'project-a',run);
  assert.match(entries.innerHTML,/of 10001/);
  renderTimingHistory(target,'project-b',{timings:[{stage:'Other project',seconds:1}]});
  assert.equal(target.writes,2);assert.equal(target.querySelector('[data-timing-entries]').innerHTML,'');
});

test('replacement timing rows refresh summaries, preserve unknown costs and escape labels',()=>{
  const run={costs:{gpuWindowEstimatedUSD:0},timings:[{stage:'<script>',chapter:1,seconds:5},{stage:'<script>',chapter:2,seconds:10},{stage:'Nested',seconds:50,detail:true}]};
  const html=timingSummaryHTML(run);
  assert.match(html,/OpenAI not recorded/);assert.match(html,/GPU window \$0.0000/);
  assert.match(html,/&lt;script&gt;/);assert.doesNotMatch(html,/<script>|Nested/);assert.match(html,/00:15/);
  const target=new Element();renderTimingHistory(target,'p',run);
  const outer=target.querySelector('[data-ui="production-times"]');outer.open=true;outer.listeners.toggle();
  renderTimingHistory(target,'p',{timings:[{stage:'Latest pass',seconds:9}]});
  assert.match(target.querySelector('[data-timing-summary]').innerHTML,/Latest pass/);
});

test('every timing page remains reachable, including the final partial page',()=>{
  const rows=Array.from({length:251},(_,i)=>({stage:'stage-'+i,seconds:i}));
  assert.match(timingPageHTML(rows,2).html,/201–251 of 251/);
  assert.match(timingPageHTML(rows,999).html,/stage-250/);
  assert.equal(timingPageHTML(rows,-2).page,0);
  assert.match(timingPageHTML([],0).html,/0–0 of 0/);
});
