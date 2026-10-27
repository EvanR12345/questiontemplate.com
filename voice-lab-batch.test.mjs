import test from 'node:test';
import assert from 'node:assert/strict';
import {VOICE_BATCH, SUPERSEDED_REFERENCE_IDS, currentVoiceBatch} from './voice-lab-batch.mjs';

test('the fresh round hides previous voices everywhere without changing stored history', () => {
  const oldReference = {id:'old-ref',createdAt:VOICE_BATCH.startedAt-1};
  const newReference = {id:'new-ref',createdAt:VOICE_BATCH.startedAt};
  const oldJob = {id:'old-job',createdAt:VOICE_BATCH.startedAt-1};
  const newJob = {id:'new-job',createdAt:VOICE_BATCH.startedAt+1};
  const source = {references:[oldReference,newReference],jobs:[oldJob,newJob],selected:{jobId:'old-job'},worker:{ready:true},models:[{id:'voxcpm2'}]};
  const view = currentVoiceBatch(source);
  assert.deepEqual(view.references,[newReference]);
  assert.deepEqual(view.jobs,[newJob]);
  assert.equal(view.selected,null);
  assert.equal(view.models,source.models);
  assert.equal(view.worker,source.worker);
  assert.equal(source.references.length,2);
  assert.equal(source.jobs.length,2);
  assert.equal(source.selected.jobId,'old-job');
});

test('superseded related voices and their linked auditions stay hidden while the original and future takes remain', () => {
  const createdAt = VOICE_BATCH.startedAt+60;
  const original = {id:'ad2888d8405c40f4a383404fb50a5f0f',createdAt};
  const future = {id:'new-direct-original-take',createdAt:createdAt+3600};
  const retired = SUPERSEDED_REFERENCE_IDS.map(id => ({id,createdAt}));
  const linkedJobs = retired.map((ref,index) => ({id:'retired-'+index,referenceId:ref.id,createdAt}));
  const newJob = {id:'new-job',referenceId:original.id,createdAt};
  const source = {references:[original,...retired,future],jobs:[...linkedJobs,newJob],selected:{jobId:linkedJobs[0].id}};
  const view = currentVoiceBatch(source);
  assert.deepEqual(view.references.map(ref => ref.id),[original.id,future.id]);
  assert.deepEqual(view.jobs,[newJob]);
  assert.equal(view.selected,null);
  assert.equal(source.references.length,SUPERSEDED_REFERENCE_IDS.length+2);
  assert.equal(source.jobs.length,SUPERSEDED_REFERENCE_IDS.length+1);
  assert.deepEqual(currentVoiceBatch({...source,selected:{jobId:newJob.id}}).selected,{jobId:newJob.id});
});

test('the user shortlist shows A preferred, B backup, then original without renaming helper records', () => {
  const a={id:'5efd23e2303043869f4f03fb0f625c33',name:'Coach · Take A',createdAt:VOICE_BATCH.startedAt+1};
  const b={id:'5d7020c96f6244d4b47ed5b880bd40f0',name:'Coach · Take B',createdAt:VOICE_BATCH.startedAt+2};
  const original={id:'ad2888d8405c40f4a383404fb50a5f0f',name:'Original coach · Clean original',createdAt:VOICE_BATCH.startedAt};
  const c={id:'aa99a22088684a8591f15540ed1002a7',name:'Coach · Take C',createdAt:VOICE_BATCH.startedAt+3};
  const future={id:'future-take',name:'Future take',createdAt:VOICE_BATCH.startedAt+3600};
  const source={references:[c,future,original,b,a],jobs:[]};
  const view=currentVoiceBatch(source);
  assert.deepEqual(view.references.map(ref=>ref.id),[a.id,b.id,original.id,future.id]);
  assert.deepEqual(view.references.map(ref=>ref.name),['Coach · Take A · Preferred','Coach · Take B · Backup','Original coach · Clean reference','Future take']);
  assert.equal(a.name,'Coach · Take A');
  assert.equal(b.name,'Coach · Take B');
  assert.equal(original.name,'Original coach · Clean original');
  assert.equal(source.references.length,5);
});

test('current choices remain visible and undated old records cannot reappear', () => {
  const selected = {jobId:'new-job'};
  const view = currentVoiceBatch({references:[{id:'undated'},{id:'string-date',createdAt:String(VOICE_BATCH.startedAt)}],jobs:[{id:'new-job',createdAt:VOICE_BATCH.startedAt}],selected});
  assert.deepEqual(view.references,[]);
  assert.equal(view.selected,selected);
  assert.deepEqual(currentVoiceBatch({}).jobs,[]);
});
