import test from 'node:test';
import assert from 'node:assert/strict';
import {VOICE_BATCH, currentVoiceBatch} from './voice-lab-batch.mjs';

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

test('current choices remain visible and undated old records cannot reappear', () => {
  const selected = {jobId:'new-job'};
  const view = currentVoiceBatch({references:[{id:'undated'},{id:'string-date',createdAt:String(VOICE_BATCH.startedAt)}],jobs:[{id:'new-job',createdAt:VOICE_BATCH.startedAt}],selected});
  assert.deepEqual(view.references,[]);
  assert.equal(view.selected,selected);
  assert.deepEqual(currentVoiceBatch({}).jobs,[]);
});
