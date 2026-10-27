import test from 'node:test';
import assert from 'node:assert/strict';
import {VOICE_BATCH,currentVoiceBatch} from './voice-lab-batch.mjs';
import {validateComparisons,comparisonDeliveryLabel} from './voice-lab-comparisons.mjs';
const original='ad2888d8405c40f4a383404fb50a5f0f',clone='11111111111111111111111111111111';
const manifest=[{referenceId:clone,sourceReferenceId:original,model:'qwen3',delivery:'quote-volume-adjusted',gainDB:-8}];

test('imported clone tests group under a visible source without becoming primary voices or invented jobs',()=>{
  const reference={id:original,name:'Original',createdAt:VOICE_BATCH.startedAt};
  const cloneReference={id:clone,name:'Qwen clone test',createdAt:VOICE_BATCH.startedAt+1,durationSeconds:28};
  const source={references:[cloneReference,reference],jobs:[]};
  const view=currentVoiceBatch(source,VOICE_BATCH,manifest);
  assert.deepEqual(view.references.map(ref=>ref.id),[original]);
  assert.equal(view.comparisons.length,1);
  assert.equal(view.comparisons[0].reference,cloneReference);
  assert.equal(view.comparisons[0].sourceReferenceId,original);
  assert.deepEqual(view.jobs,[]);
  assert.equal(source.references.length,2);
  assert.equal(source.references[1].name,'Original');
});

test('missing or hidden source recordings never create empty or orphan comparison cards',()=>{
  assert.deepEqual(currentVoiceBatch({references:[{id:original,createdAt:VOICE_BATCH.startedAt}]},VOICE_BATCH,manifest).comparisons,[]);
  const orphan=currentVoiceBatch({references:[{id:clone,createdAt:VOICE_BATCH.startedAt}]},VOICE_BATCH,manifest);
  assert.deepEqual(orphan.references,[]);
  assert.deepEqual(orphan.comparisons,[]);
  const retired=currentVoiceBatch({references:[{id:original,createdAt:VOICE_BATCH.startedAt-1},{id:clone,createdAt:VOICE_BATCH.startedAt}]},VOICE_BATCH,manifest);
  assert.deepEqual(retired.comparisons,[]);
});

test('labels distinguish native delivery from measured quote gain and reject ambiguous comparison metadata',()=>{
  assert.equal(comparisonDeliveryLabel(manifest[0]),'Quoted dialogue lowered by 8 dB after generation');
  assert.match(comparisonDeliveryLabel({delivery:'native'}),/^Native model delivery/);
  assert.equal(comparisonDeliveryLabel({delivery:'native-volume-delivery',gainDB:0}),'Native quieter quote volume · no quote gain applied');
  assert.doesNotThrow(()=>validateComparisons([{...manifest[0],delivery:'native-volume-delivery',gainDB:0}]));
  assert.throws(()=>validateComparisons([{...manifest[0],delivery:'native-volume-delivery'}]),/cannot include quote gain/);
  assert.throws(()=>validateComparisons([...manifest,manifest[0]]),/reference/);
  assert.throws(()=>validateComparisons([{...manifest[0],gainDB:undefined}]),/actual gain/);
  assert.throws(()=>validateComparisons([{...manifest[0],delivery:'perfect-whisper'}]),/delivery/);
});

test('quota notes list only missing model tests and do not invent playable comparisons',()=>{
  const pending=[{sourceReferenceId:original,models:['qwen3','drama3'],note:'The free demo quota was exhausted.'}];
  const references=[{id:original,createdAt:VOICE_BATCH.startedAt}];
  const missing=currentVoiceBatch({references},VOICE_BATCH,manifest,pending);
  assert.deepEqual(missing.comparisons,[]);
  assert.deepEqual(missing.pendingComparisons,pending);
  const partial=currentVoiceBatch({references:[...references,{id:clone,createdAt:VOICE_BATCH.startedAt}]},VOICE_BATCH,manifest,pending);
  assert.equal(partial.comparisons.length,1);
  assert.deepEqual(partial.pendingComparisons,[{...pending[0],models:['drama3']}]);
  assert.deepEqual(currentVoiceBatch({references:[]},VOICE_BATCH,manifest,pending).pendingComparisons,[]);
});
