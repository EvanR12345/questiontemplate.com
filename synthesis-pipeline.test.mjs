import test from 'node:test';
import assert from 'node:assert/strict';
import { streamSynthesis } from './synthesis-pipeline.mjs';
const section = text => ({text, ids: {phonemes:text}});
const deferred = () => { let resolve; const promise = new Promise(r => {resolve=r;}); return {promise,resolve}; };
async function* source(items) { yield* items; }

test('prefetch starts the following inference before consumer encoding and retains every section', async () => {
  const calls=[],second=deferred();
  const input=streamSynthesis(source([section('first'),section('second'),section('third')]),batch=>{
    calls.push(batch.text);return batch.text==='second'?second.promise:Promise.resolve(batch.text);
  },{prefetch:true});
  const first=await input.next();assert.equal(first.value.audio,'first');assert.deepEqual(calls,['first','second']);
  second.resolve('second');const output=[first.value];for await(const item of input)output.push(item);
  assert.deepEqual(output.map(x=>x.batch.text),['first','second','third']);assert.deepEqual(output.map(x=>x.audio),['first','second','third']);
});
test('inference concurrency remains one and blank source sections are retained', async () => {
  let active=0,peak=0;const output=[];
  for await(const item of streamSynthesis(source([section('a'),{text:' ',ids:null},section('b'),section('c')]),async batch=>{
    active++;peak=Math.max(peak,active);await new Promise(r=>setTimeout(r,1));active--;return batch.text;
  },{prefetch:true}))output.push(item);
  assert.equal(peak,1);assert.equal(output.map(x=>x.batch.text).join(''),'a bc');assert.equal(output[1].audio,null);
});
test('a following preparation error preserves the completed current audio', async () => {
  async function* broken(){yield section('safe');throw Error('pronunciation failed');}
  const input=streamSynthesis(broken(),async batch=>batch.text,{prefetch:true});
  assert.equal((await input.next()).value.audio,'safe');await assert.rejects(input.next(),/pronunciation failed/);
});
test('a failed prefetched request is reported when its section is consumed', async () => {
  const input=streamSynthesis(source([section('safe'),section('bad')]),async batch=>{
    if(batch.text==='bad')throw Error('CUDA failed');return batch.text;
  },{prefetch:true});
  assert.equal((await input.next()).value.audio,'safe');await assert.rejects(input.next(),/CUDA failed/);
});
test('cancel drains outstanding inference without committing it or starting another section', async () => {
  const inFlight=deferred();let gates=0,closed=false;const calls=[];
  async function* items(){try{yield* [section('first'),section('second'),section('third')];}finally{closed=true;}}
  const input=streamSynthesis(items(),batch=>{calls.push(batch.text);return batch.text==='first'?Promise.resolve('first'):inFlight.promise;},{prefetch:true,gate:async()=>++gates===1});
  assert.equal((await input.next()).value.audio,'first');let complete=false;const done=input.next().then(value=>{complete=true;return value;});
  await new Promise(r=>setTimeout(r,0));assert.equal(complete,false);inFlight.resolve('second');assert.equal((await done).done,true);
  assert.deepEqual(calls,['first','second']);assert.equal(closed,true);
});
test('pause holds prefetched audio until resume and never starts a third request early', async () => {
  const resume=deferred();let gates=0;const calls=[];
  const input=streamSynthesis(source([section('first'),section('second'),section('third')]),async batch=>{calls.push(batch.text);return batch.text;},{prefetch:true,gate:async()=>{if(++gates===2)await resume.promise;return true;}});
  await input.next();let advanced=false;const second=input.next().then(x=>{advanced=true;return x;});
  await new Promise(r=>setTimeout(r,0));assert.equal(advanced,false);assert.deepEqual(calls,['first','second']);
  resume.resolve();assert.equal((await second).value.audio,'second');await input.return();
});
test('serial mode renders only the current section until the consumer advances', async () => {
  const calls=[];const input=streamSynthesis(source([section('first'),section('second')]),async batch=>{calls.push(batch.text);return batch.text;});
  await input.next();assert.deepEqual(calls,['first']);await input.next();assert.deepEqual(calls,['first','second']);await input.return();
});
