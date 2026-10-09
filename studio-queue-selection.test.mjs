import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
test('queue cancellation remains attached to the displayed project during autosave',async()=>{
  let queued,request;
  const button={dataset:{control:'cancel-all'}};
  const context={project:{id:'first'},displayedProject:'first',current:null,
    el:{querySelectorAll:()=>[button]},action:fn=>{queued=fn;},
    api:async(path,body)=>{request={path,body};return {};},renderQueue:()=>{}};
  const begin=source.indexOf('  el.querySelectorAll("[data-control]")');
  const end=source.indexOf('  el.querySelectorAll("[data-job-priority]")',begin);
  vm.runInNewContext(source.slice(begin,end),context);
  button.onclick();context.project={id:'second'};await queued();
  assert.equal(request.body.project,'first');assert.equal(request.body.action,'cancel-all');
});
