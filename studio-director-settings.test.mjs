import {test} from 'node:test';
import assert from 'node:assert/strict';
import {directorLimits,directorSummary,directorSettings} from './studio-director-settings.mjs';
const values={executionMode:'staged-review',parallelism:8,processingTier:'default',provider:'openai-luna',imageProvider:'comfyui',economyPanels:false,apiBudget:1};
test('staged settings preserve cadence and manual configuration without enabling a paid premium',()=>{
  const d=directorSettings({cadencePerMinute:6,reasoning:'Balanced'},values,{stagedDirectorAvailable:true});
  assert.equal(d.cadencePerMinute,6);assert.equal(d.reasoning,'Balanced');assert.equal(d.processingTier,'default');assert.equal(d.factGroupSentences,128);
  assert.equal(directorLimits(d.executionMode),8);
});
test('classic caps parallelism and staged requires the installed helper and spending cap',()=>{
  assert.throws(()=>directorSettings({},values,{}),/helper/);
  assert.throws(()=>directorSettings({},{...values,apiBudget:0},{stagedDirectorAvailable:true}),/cap/);
  assert.throws(()=>directorSettings({},{...values,executionMode:'classic'},{}),/limit/);
  assert.throws(()=>directorSettings({},{...values,economyPanels:true},{stagedDirectorAvailable:true}),/individual/);
});
test('summary distinguishes reasoning from premium API processing and does not guarantee speed',()=>{
  const text=directorSummary({director:{...values,processingTier:'fast'}});
  assert.match(text,/2× Standard/);assert.match(text,/manual edits/);assert.doesNotMatch(text,/guaranteed|3× faster/);
});
test('lean workflow preserves cadence, requires updated helper and supports Standard or Flex without premium fallback',()=>{
  const lean={...values,executionMode:'staged-lean'};
  const health={stagedDirectorAvailable:true,leanDirectorAvailable:true};
  const d=directorSettings({cadencePerMinute:6,reasoning:'Fast'},lean,health);
  assert.equal(d.parallelism,8);assert.equal(d.cadencePerMinute,6);assert.equal(d.processingTier,'default');
  assert.match(directorSummary({director:d}),/multiple shots per request/);
  assert.throws(()=>directorSettings({},lean,{stagedDirectorAvailable:true}),/helper/);
  assert.throws(()=>directorSettings({},{...lean,processingTier:'fast'},health),/Standard/);
  const flex=directorSettings({cadencePerMinute:6},{...lean,processingTier:'flex'},health);
  assert.equal(flex.processingTier,'flex');assert.equal(flex.cadencePerMinute,6);
  assert.match(directorSummary({director:flex}),/variable latency/);
});
