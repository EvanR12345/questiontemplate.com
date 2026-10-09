import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {buildPlan,schedule,DEFAULTS} from './pipeline-engine.mjs';
import {compactPreparation,preparationGroup,preparationMoves,PREPARATION_GROUP} from './pipeline-preparation-group.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url)));
const plan=buildPlan({...DEFAULTS,chapters:80},evidence),result=schedule(plan);
test('80 chapter state and clean steps become one display block without altering the real plan',()=>{
  const before=JSON.stringify(result),display=compactPreparation(result.tasks),group=display.find(t=>t.id===PREPARATION_GROUP);
  assert.equal(group.members.length,160);assert.equal(group.chapters.length,80);
  assert.equal(display.length,result.tasks.length-159);
  assert.ok(!display.some(t=>t.kind==='clean'||t.kind==='bible'));
  assert.ok(Math.abs(group.work-104)<1e-9);assert.equal(JSON.stringify(result),before);
  assert.deepEqual(compactPreparation(result.tasks,false),result.tasks);
});
test('chapter focus groups only the visible members and project-only view adds no phantom work',()=>{
  const group=preparationGroup(result.tasks.filter(t=>t.chapter===80));
  assert.equal(group.members.length,2);assert.deepEqual(group.chapters,[80]);
  assert.equal(preparationGroup(result.tasks.filter(t=>t.chapter===0)),null);
});
test('moving a compact group preserves member offsets and all original resource/dependency checks',()=>{
  const group=preparationGroup(result.tasks),preferences={'c1-voice':{priority:-8}};
  const moves=preparationMoves(group,group.start+12,preferences),after=schedule(plan,moves);
  assert.equal(Object.keys(moves).length,161);assert.deepEqual(moves['c1-voice'],preferences['c1-voice']);
  assert.equal(preferences['c1-clean'],undefined);assert.deepEqual(after.diagnostics,[]);
  for(const member of group.members)assert.equal(moves[member.id].notBefore,member.start+12);
  assert.throws(()=>preparationMoves(group,Infinity,{}));
});
test('external dependencies and true work are retained separately from a span containing gaps',()=>{
  const tasks=[{id:'clean',kind:'clean',chapter:1,start:2,end:3,duration:1,deps:['preflight'],lane:'prepare'},
    {id:'bible',kind:'bible',chapter:1,start:50,end:50.3,duration:.3,deps:['preflight'],lane:'prepare'}];
  const group=preparationGroup(tasks);assert.equal(group.work,1.3);assert.equal(group.duration,48.3);
  assert.deepEqual(group.deps,['preflight']);
});
