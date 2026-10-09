import test from 'node:test';
import assert from 'node:assert/strict';
import {createWorkerRequest} from './pipeline-worker-request.mjs';
function fixture(){const workers=[];const request=createWorkerRequest(()=>{const w={terminated:0,terminate(){this.terminated++;},postMessage(data){this.data=data;}};workers.push(w);return w;});return {request,workers};}
test('replacing an in-flight plan settles its caller and terminates its worker',async()=>{
  const {request,workers}=fixture();const first=request.run({chapters:80});const second=request.run({chapters:90});
  assert.equal(await first,null);assert.equal(workers[0].terminated,1);assert.equal(workers[0].onmessage,null);
  workers[1].onmessage({data:{plan:'new'}});assert.deepEqual(await second,{plan:'new'});assert.equal(workers[1].terminated,1);
});
test('a stale queued callback cannot complete or cancel the replacement plan',async()=>{
  const {request,workers}=fixture();const first=request.run({});const stale=workers[0].onmessage;const second=request.run({});
  stale({data:{plan:'old'}});assert.equal(await first,null);assert.equal(workers[1].terminated,0);
  workers[1].onmessage({data:{plan:'new'}});assert.deepEqual(await second,{plan:'new'});
});
test('calculation errors and explicit cancellation release worker handlers',async()=>{
  const {request,workers}=fixture();const failed=request.run({});workers[0].onmessage({data:{type:'error',message:'Invalid duration'}});
  await assert.rejects(failed,/Invalid duration/);assert.equal(workers[0].onerror,null);
  const pending=request.run({});request.cancel();assert.equal(await pending,null);request.cancel();assert.equal(workers[1].terminated,1);
});
test('worker startup and synchronous send failures reject without hanging',async()=>{
  await assert.rejects(createWorkerRequest(()=>{throw Error('Disabled worker');}).run({}),/Disabled worker/);
  let terminated=0;await assert.rejects(createWorkerRequest(()=>({terminate(){terminated++;},postMessage(){throw Error('Not cloneable');}})).run({}),/Not cloneable/);assert.equal(terminated,1);
});
