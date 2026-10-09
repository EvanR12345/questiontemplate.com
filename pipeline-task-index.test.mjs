import test from 'node:test';
import assert from 'node:assert/strict';
import {indexTasks,dependsOnTask} from './pipeline-task-index.mjs';
import {explainMove} from './pipeline-engine.mjs';
test('deep chapter dependencies remain exact without recursive stack limits',()=>{
  const tasks=Array.from({length:15000},(_,i)=>({id:'task-'+i,deps:i?['task-'+(i-1)]:[],end:i+1}));
  const index=indexTasks(tasks);assert.equal(dependsOnTask(index,'task-14999','task-0'),true);
  assert.equal(dependsOnTask(index,'task-0','task-14999'),false);
  assert.equal(index.get('task-10000'),tasks[10000]);
});
test('shared dependencies and cyclic invalid data cannot loop forever',()=>{
  const index=indexTasks([{id:'a',deps:['b','c']},{id:'b',deps:['c']},{id:'c',deps:['a']}]);
  assert.equal(dependsOnTask(index,'a','missing'),false);assert.equal(dependsOnTask(index,'a','c'),true);
  assert.throws(()=>dependsOnTask(index,'lost','c'),/Missing dependency/);
});
test('drag explanation checks every dependency while keeping its feedback readable',()=>{
  const tasks=Array.from({length:80},(_,i)=>({id:'d'+i,name:'Dependency '+i,end:i+1,deps:[]}));
  tasks.push({id:'final',name:'Final',deps:tasks.map(t=>t.id),end:100});const result={tasks};
  const explanation=explainMove({},result,'final',0,indexTasks(tasks));
  assert.match(explanation,/1m 20s/);assert.match(explanation,/74 more/);assert.ok(explanation.length<300);
  assert.match(explainMove({},result,'final',80,indexTasks(tasks)),/safely/);
});
