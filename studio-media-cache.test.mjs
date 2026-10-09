import test from 'node:test';
import assert from 'node:assert/strict';
import {cachedMediaLink} from './studio-cloud-ui.mjs';
test('concurrent identical previews request one signed capability',async()=>{
  const cache=new Map();let release,calls=0;
  const api=()=>{calls++;return new Promise(resolve=>{release=resolve;});};
  const a=cachedMediaLink(cache,'story','art.png',api,()=>1000);
  const b=cachedMediaLink(cache,'story','art.png',api,()=>1000);
  assert.equal(calls,1);
  release({url:'https://storage.example/art',expires:100});
  assert.deepEqual(await Promise.all([a,b]),['https://storage.example/art','https://storage.example/art']);
  assert.equal(await cachedMediaLink(cache,'story','art.png',api,()=>2000),'https://storage.example/art');
  assert.equal(calls,1);
});
test('reconnection clearing cannot be undone by a late old helper reply',async()=>{
  const cache=new Map();let release;
  const old=cachedMediaLink(cache,'story','art.png',()=>new Promise(resolve=>{release=resolve;}),()=>1000);
  cache.clear();release({url:'old',expires:100});assert.equal(await old,'old');assert.equal(cache.size,0);
  const fresh=await cachedMediaLink(cache,'story','art.png',async()=>({url:'fresh',expires:100}),()=>1000);
  assert.equal(fresh,'fresh');assert.equal(cache.get('story/art.png').url,'fresh');
});
test('failed link lookups remove the in-flight entry and retry normally',async()=>{
  const cache=new Map();let calls=0;
  const api=async()=>{if(++calls===1)throw Error('connection lost');return {url:'recovered',expires:100};};
  await assert.rejects(cachedMediaLink(cache,'story','art',api,()=>1000),/connection lost/);
  assert.equal(cache.size,0);
  assert.equal(await cachedMediaLink(cache,'story','art',api,()=>1000),'recovered');assert.equal(calls,2);
});
