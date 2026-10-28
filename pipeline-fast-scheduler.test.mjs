// Differential regressions against the pre-integration allocation algorithm.
// The reference below is frozen intentionally; production uses a separate heap/calendar kernel.
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {DEFAULTS,buildPlan,schedule,validateSchedule,importSnapshot} from './pipeline-engine.mjs';
import {costParts} from './pipeline-pricing.mjs?v=pricing-20261009';
import {scheduleTasksFast} from './pipeline-fast-scheduler.mjs';
const evidence=JSON.parse(fs.readFileSync(new URL('./pipeline-evidence.json',import.meta.url),'utf8'));
function fits(task,start,allocations,capacities) {
  let next=start;
  for(const [pool,units] of Object.entries(task.resources)) {
    if(units>capacities[pool]+1e-8)throw Error(`Task ${task.id} exceeds ${pool} capacity.`);
    const relevant=(allocations[pool]||[]).filter(a=>a.end>start+1e-7&&a.start<start+task.duration-1e-7);
    const points=[start,...relevant.map(a=>Math.max(start,a.start))];
    for(const point of points) {
      const active=relevant.filter(a=>a.start<=point+1e-7&&a.end>point+1e-7);
      if(active.reduce((n,a)=>n+a.units,0)+units>capacities[pool]+1e-7)next=Math.max(next,Math.min(...active.map(a=>a.end)));
    }
  }
  return next;
}

function referenceSchedule(plan, preferences={}) {
  const pending=new Set(plan.tasks),completed=new Map(),allocations={},out=[];
  // A candidate's earliest slot remains valid until a newly allocated shared
  // resource overlaps it. Avoid rescheduling every ready chapter after unrelated work.
  const earliest=new Map();
  const dirtySlots=new Set();
  const ids=new Set(plan.tasks.map(t=>t.id));if(ids.size!==pending.size)throw Error('Duplicate task IDs.');
  const ready=new Set(),remaining=new Map(),followers=new Map();
  for(const task of plan.tasks){
    remaining.set(task.id,task.deps.length);if(!task.deps.length)ready.add(task);
    for(const dep of task.deps){if(!ids.has(dep))throw Error(`Missing dependency ${dep}.`);(followers.get(dep)||followers.set(dep,[]).get(dep)).push(task);}
  }
  while(pending.size) {
    let choices=[];
    for(const task of ready) {
      let start=earliest.get(task);
      if(start===undefined||dirtySlots.has(task)){
        if(start===undefined){
          const requested=Math.max(task.notBefore||0,preferences[task.id]?.notBefore||0);
          start=Math.max(requested,0,...task.deps.map(d=>completed.get(d).end));
        }
        // Allocations are only added: an invalidated earliest slot cannot move
        // earlier. Resume from that bound instead of replaying all past conflicts.
        for(let guard=0;;guard++){
          if(guard>plan.tasks.length*3+20)throw Error('Resource allocation failed.');
          const next=fits(task,start,allocations,plan.capacities);if(next<=start+1e-7)break;start=next;
        }
        earliest.set(task,start);
        dirtySlots.delete(task);
      }
      choices.push({task,start,priority:preferences[task.id]?.priority??task.priority});
    }
    if(!choices.length)throw Error('Cycle in task dependencies.');
    choices.sort((a,b)=>a.start-b.start||a.priority-b.priority||a.task.priority-b.task.priority);
    const {task,start}=choices[0],item={...task,start,end:start+task.duration,manual:Boolean(preferences[task.id])};
    out.push(item);completed.set(item.id,item);pending.delete(task);ready.delete(task);
    earliest.delete(task);
    dirtySlots.delete(task);
    for(const candidate of ready){
      const at=earliest.get(candidate);
      if(at!==undefined&&item.end>at+1e-7&&item.start<at+candidate.duration-1e-7&&
          Object.keys(task.resources).some(pool=>task.resources[pool]&&candidate.resources[pool]))dirtySlots.add(candidate);
    }
    for(const follower of followers.get(item.id)||[]){const left=remaining.get(follower.id)-1;remaining.set(follower.id,left);if(left===0)ready.add(follower);}
    for(const [pool,units] of Object.entries(task.resources))(allocations[pool]||=[]).push({id:item.id,start:item.start,end:item.end,units});
  }
  out.sort((a,b)=>a.start-b.start||a.priority-b.priority);
  const end=Math.max(...out.map(t=>t.end)),boot=completed.get('boot'),stop=completed.get('stop');
  const rentalSeconds=stop.end-boot.start,gpuWork=out.filter(t=>t.resources.remote).reduce((n,t)=>n+t.duration,0);
  const directorUSD=plan.director.costPerTwoHours*plan.storySeconds/7200;
  const qcCount=out.filter(t=>t.kind==='vision').reduce((n,t)=>n+(t.images||0),0);
  const qcUSD=qcCount*plan.config.qcCost;
  return {tasks:out,end,rentalSeconds,gpuWork,gpuIdleSeconds:Math.max(0,rentalSeconds-gpuWork),
    ...costParts(rentalSeconds,plan.gpu.hourly,directorUSD,qcUSD,plan.config.storageDaily,plan.config.storageDays),
    directorUSD,qcUSD,qcCount,diagnostics:validateSchedule(plan,out)};
}


const summaryKeys=['end','rentalSeconds','gpuWork','gpuIdleSeconds','gpuUSD','apiUSD','productionUSD','storageUSD','totalUSD','directorUSD','qcUSD','qcCount'];
function compare(plan,preferences={},exact=false){
 const before=JSON.stringify({plan,preferences}),actual=schedule(plan,preferences),expected=referenceSchedule(plan,preferences);
 if(exact)assert.deepEqual(actual.tasks,expected.tasks);
 else{
  const index=new Map(expected.tasks.map(t=>[t.id,t]));assert.equal(actual.tasks.length,index.size);
  for(const {start,end,...task} of actual.tasks){const {start:oldStart,end:oldEnd,...oldTask}=index.get(task.id);assert.deepEqual(task,oldTask);assert.ok(Math.abs(start-oldStart)<=1e-7,task.id+' start');assert.ok(Math.abs(end-oldEnd)<=1e-7,task.id+' end');}
 }
 for(const key of summaryKeys)assert.equal(actual[key],expected[key],key);
 assert.deepEqual(actual.diagnostics,[]);
 assert.equal(JSON.stringify({plan,preferences}),before,'must not mutate graph or preferences');
 return actual;
}
test('fast scheduler preserves every task, narration duration and account across existing policies and manual moves',()=>{
 for(const policy of ['current','proposed','serial'])for(const qc of ['off','sampled','practical']){
  const plan=buildPlan({...DEFAULTS,minutes:120,chapters:3,policy,qc},evidence);
  const voice=plan.tasks.filter(t=>t.kind==='voice');
  const preferences={'c1-voice':{notBefore:600,priority:-10},boot:{notBefore:900},'removed-chart-group':{notBefore:100000}};
  const result=compare(plan,preferences);
  assert.deepEqual(result.tasks.filter(t=>t.kind==='voice').map(({start,end,manual,...t})=>t).sort((a,b)=>a.id.localeCompare(b.id)),voice.slice().sort((a,b)=>a.id.localeCompare(b.id)));
 }
});
test('existing exports and large-project manual moves keep their task identities',()=>{
 const imported=importSnapshot({type:'studio-pipeline-plan',version:1,config:{...DEFAULTS,minutes:720,chapters:84},preferences:{boot:{notBefore:200000},'c84-voice':{notBefore:100000}}},evidence);
 const p=buildPlan(imported.config,evidence),r=compare(p,imported.preferences);
 assert.equal(r.tasks.find(t=>t.id==='boot').start,200000);
 assert.ok(r.tasks.find(t=>t.id==='c84-voice').start>=100000);
});
test('fractional seeded DAGs retain exact reference choices including future releases and priority ties',()=>{
 let state=0x42710fac;const random=()=>{state^=state<<13;state^=state>>>17;state^=state<<5;return(state>>>0)/4294967296;},int=(a,b)=>a+Math.floor(random()*(b-a+1));
 for(let sample=0;sample<96;sample++){
  const core=[],preferences={};
  for(let i=0,n=int(4,45);i<n;i++){
   const deps=core.filter(()=>random()<(sample%2?.12:.03)).map(t=>t.id),resources={};
   if(random()<.8)resources.cpu=int(1,8)/4;if(random()<.55)resources.remote=int(1,4)/4;
   const id='j'+i;core.push({id,kind:i%5===0?'vision':'work',images:i%5===0?int(1,3):undefined,duration:int(1,40)/8,deps:deps.length?deps:['boot'],resources,priority:int(-3,5),notBefore:random()<.5?int(0,300)/4:0});
   if(random()<.25)preferences[id]={notBefore:int(0,400)/4,priority:int(-10,10)};
  }
  const plan={tasks:[{id:'boot',kind:'boot',duration:.25,deps:[],resources:{},priority:-1000},...core,{id:'stop',kind:'stop',duration:.25,deps:core.map(t=>t.id),resources:{},priority:-1}],capacities:{cpu:2,remote:1},director:{costPerTwoHours:.321},storySeconds:600,gpu:{hourly:1.23},config:{qcCost:.00037,storageDaily:.034,storageDays:1.5}};
  compare(plan,preferences,true);
 }
});
test('invalid capacities, resource overflow, dependencies, cycles and preferences reject promptly',()=>{
 const plan=buildPlan({...DEFAULTS,chapters:1},evidence);
 for(const change of [
  p=>p.capacities.cpu=0,p=>p.capacities.cpu=Infinity,p=>p.tasks[0].duration=NaN,
  p=>p.tasks[0].resources={unknown:1},p=>p.tasks[0].resources={cpu:p.capacities.cpu+1},
  p=>p.tasks[0].deps=['missing'],p=>p.tasks[0].deps=['stop'],p=>p.tasks[1].id=p.tasks[0].id,
  p=>p.tasks[0].duration=1e-8,p=>p.tasks[0].resources={cpu:p.capacities.cpu+5e-8},p=>p.tasks[0].notBefore=-1,p=>p.tasks[0].priority=Infinity,
 ]){const p=structuredClone(plan);change(p);assert.throws(()=>schedule(p));}
 assert.throws(()=>schedule(plan,{boot:{notBefore:Infinity}}));
 assert.throws(()=>schedule(plan,{boot:{priority:NaN}}));
});
test('calculation has no research-only task cap and does not use argument-spread limits',()=>{
 const tasks=Array.from({length:30001},(_,i)=>({id:'t'+i,duration:1,priority:i,deps:[],resources:{}}));
 const result=scheduleTasksFast({tasks,capacities:{cpu:1}});
 assert.equal(result.length,tasks.length);assert.equal(result.at(-1).end,1);
});
