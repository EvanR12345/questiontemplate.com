import test from 'node:test';
import assert from 'node:assert/strict';
import {createEditorSave} from './studio-editor-save.mjs';
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
test('autosave and navigation flush share one write instead of saving twice',async()=>{
  const pending=deferred();let writes=0;
  const save=createEditorSave(()=>{writes++;return pending.promise;});
  save.markDirty();const auto=save.flush(),navigation=save.flush();
  assert.equal(auto,navigation);assert.equal(writes,1);assert.equal(save.dirty,true);
  pending.resolve();await Promise.all([auto,navigation]);
  await save.flush();assert.equal(writes,1);assert.equal(save.dirty,false);
});
test('new text during an in-flight write is durably saved before flush ends',async()=>{
  const first=deferred(),writes=[];let text='Original';
  const save=createEditorSave(async()=>{writes.push(text);if(writes.length===1)await first.promise;});
  save.markDirty();const finished=save.flush();
  text='Newer manual edit';save.markDirty();
  assert.equal(save.flush(),finished);first.resolve();await finished;
  assert.deepEqual(writes,['Original','Newer manual edit']);assert.equal(save.dirty,false);
});
test('a failed write stays dirty and can be retried without losing later edits',async()=>{
  let fail=true,text='Story',saved;
  const save=createEditorSave(async()=>{if(fail)throw Error('offline storage failed');saved=text;});
  save.markDirty();await assert.rejects(save.flush(),/storage failed/);
  assert.equal(save.dirty,true);
  text='Recovered story';save.markDirty();fail=false;await save.flush();
  assert.equal(saved,'Recovered story');assert.equal(save.dirty,false);
});
test('a synchronous write error is retryable and a clean project sends no request',async()=>{
  let writes=0,fail=true;
  const save=createEditorSave(()=>{writes++;if(fail)throw Error('validation failed');});
  await save.flush();assert.equal(writes,0);
  save.markDirty();await assert.rejects(save.flush(),/validation/);
  fail=false;await save.flush();assert.equal(writes,2);
});
