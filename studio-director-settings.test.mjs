import {test} from 'node:test';
import assert from 'node:assert/strict';
import {directorLimits,directorSummary,directorSettings,directorRequestControls} from './studio-director-settings.mjs';
const values={executionMode:'staged-review',parallelism:8,processingTier:'default',provider:'openai-luna',imageProvider:'comfyui',economyPanels:false,apiBudget:1};
test('staged settings preserve cadence and manual configuration without enabling a paid premium',()=>{
  const d=directorSettings({cadencePerMinute:6,reasoning:'Balanced'},values,{stagedDirectorAvailable:true});
  assert.equal(d.cadencePerMinute,6);assert.equal(d.reasoning,'Balanced');assert.equal(d.processingTier,'default');assert.equal(d.factGroupSentences,48);
  assert.equal(directorLimits(d.executionMode),8);
});
test('classic caps parallelism and staged requires the installed helper and spending cap',()=>{
  assert.throws(()=>directorSettings({},values,{}),/helper/);
  assert.throws(()=>directorSettings({},{...values,apiBudget:0},{stagedDirectorAvailable:true}),/cap/);
  assert.throws(()=>directorSettings({},{...values,executionMode:'classic'},{}),/limit/);
  assert.throws(()=>directorSettings({},{...values,economyPanels:true},{stagedDirectorAvailable:true}),/individual/);
});
test('bounded packing is explicit, requires compatible helper and preserves saved group sizes',()=>{
  const v={...values,executionMode:'staged-lean',factGroupSentences:256,visualPacking:'large'};
  const h={stagedDirectorAvailable:true,leanDirectorAvailable:true,leanPackingAvailable:true};
  const d=directorSettings({cadencePerMinute:6},v,h);
  assert.equal(d.factGroupSentences,256);assert.equal(d.visualPacking,'large');assert.equal(d.cadencePerMinute,6);
  assert.throws(()=>directorSettings({},v,{...h,leanPackingAvailable:false}),/helper/);
  for(const f of [0,257,true])assert.throws(()=>directorSettings({},{...v,factGroupSentences:f},h),/size/);
  assert.throws(()=>directorSettings({},{...v,visualPacking:'unbounded'},h),/size/);
  assert.throws(()=>directorSettings({},{...v,executionMode:'staged-review'},h),/Lean/);
  assert.equal(directorSettings({factGroupSentences:96},values,h).factGroupSentences,96);
});
test('experimental grouping controls reflect saved settings and helper compatibility',()=>{
  const html=directorRequestControls({executionMode:'staged-lean',factGroupSentences:96,visualPacking:'small'},{leanPackingAvailable:true});
  assert.match(html,/value="96" selected/);assert.match(html,/value="small" selected/);
  assert.match(html,/group API work, not the number of pictures/);assert.match(html,/failed speaker/);
  assert.doesNotMatch(html,/id="settingVisualPacking" disabled/);
  assert.match(directorRequestControls({executionMode:'classic'},{}),/value="256"[^>]*disabled/);
  assert.match(directorRequestControls({executionMode:'staged-lean'},{}),/id="settingVisualPacking" disabled/);
});
test('source/visual overlap remains explicit and requires the new safe helper capability',()=>{
  const v={...values,executionMode:'staged-lean',sourceVisualOverlap:true};
  const h={stagedDirectorAvailable:true,leanDirectorAvailable:true,sourceVisualOverlapAvailable:true};
  assert.equal(directorSettings({},v,h).sourceVisualOverlap,true);
  assert.throws(()=>directorSettings({},v,{...h,sourceVisualOverlapAvailable:false}),/helper/);
  assert.throws(()=>directorSettings({},{...v,sourceVisualOverlap:1},h),/enabled or disabled/);
  assert.throws(()=>directorSettings({},{...v,executionMode:'staged-review'},h),/Lean/);
  assert.equal(directorSettings({},values,h).sourceVisualOverlap,false);
});
test('single-owner emotional beats are explicit, helper guarded, and shown before generation',()=>{
  const v={...values,executionMode:'staged-lean',factBeatsMode:'storyboard'};
  const h={stagedDirectorAvailable:true,leanDirectorAvailable:true,leanFactBeatsAvailable:true};
  const d=directorSettings({cadencePerMinute:6},v,h);
  assert.equal(d.factBeatsMode,'storyboard');assert.equal(d.cadencePerMinute,6);
  assert.match(directorSummary({director:d}),/Storyboard owns emotional beats once/);
  assert.match(directorRequestControls(d,h),/value="storyboard" selected/);
  assert.throws(()=>directorSettings({},v,{...h,leanFactBeatsAvailable:false}),/helper/);
  assert.throws(()=>directorSettings({},{...v,executionMode:'classic',parallelism:1},h),/Lean/);
  assert.throws(()=>directorSettings({},{...v,factBeatsMode:'off'},h),/ownership/);
  assert.equal(directorSettings({},values,h).factBeatsMode,'analyst');
});
test('summary distinguishes reasoning from premium API processing and does not guarantee speed',()=>{
  const text=directorSummary({director:{...values,processingTier:'fast'}});
  assert.match(text,/2× Standard/);assert.match(text,/manual edits/);assert.doesNotMatch(text,/guaranteed|3× faster/);
});
test('missing historical request size displays the actual helper default before generation',()=>{
  assert.match(directorSummary({director:{executionMode:'staged-lean'}}),/up to 48 sentences/);
  assert.match(directorRequestControls({executionMode:'staged-lean'},{}),/value="48" selected/);
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
  assert.equal(directorSettings({},{...lean,compactCuts:true},health).compactCuts,true);
  assert.throws(()=>directorSettings({},{...values,compactCuts:true},health),/Lean Luna/);
});
