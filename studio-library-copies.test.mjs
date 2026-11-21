import test from 'node:test';
import assert from 'node:assert/strict';
import {createLibraryCopies} from './studio-library-copies.mjs';
const project=(id,revision)=>({id,revision,chapters:[{id:'chapter'}]});

test('library skips current copies and offline edits and picks up new rows during a save',async()=>{
  let release; const gate=new Promise(r=>release=r);
  const reads=[],saved=[],status=[];
  const sync=createLibraryCopies({
    list:async()=>[{id:'dirty',revision:1,unsynced:true},{id:'current',revision:3}],
    read:async(id,revision)=>{reads.push(id);await gate;return project(id,revision);},
    save:async p=>saved.push(p),report:t=>status.push(t),
  });
  const run=sync([project('dirty',6),project('current',3),project('new',2)]);
  await Promise.resolve(); sync([project('later',1)]);release();await run;
  assert.deepEqual(reads,['new','later']);assert.equal(saved.length,2);
  assert.match(status.at(-1),/4 projects/);
});

test('a failed or wrong project never replaces a stored copy or blocks the next project',async()=>{
  const saved=[],reads=[];let time=0,last='';
  const sync=createLibraryCopies({list:async()=>[],read:async(id,r)=>{reads.push(id);return project(id==='bad'?'wrong':id,r);},
    save:async p=>saved.push(p.id),report:s=>{last=s;},now:()=>time});
  await sync([project('bad',5),project('good',1)]);assert.deepEqual(saved,['good']);assert.match(last,/1 of 2/);
  await sync([]);assert.equal(reads.filter(x=>x==='bad').length,1);
  time=61000; await sync([]);assert.equal(reads.filter(x=>x==='bad').length,2);
});

test('a saved project with no chapters is included in the browser library',async()=>{
  const saved=[];
  const sync=createLibraryCopies({list:async()=>[],read:async()=>({id:'empty',revision:0,chapters:[]}),save:async p=>saved.push(p),report:()=>{}});
  await sync([{id:'empty',revision:0}]);assert.equal(saved.length,1);
});
