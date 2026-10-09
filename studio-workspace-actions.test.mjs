import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createProjectSelection} from './studio-form-drafts.mjs';
const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
const deferred=()=>{let resolve,reject;return {promise:new Promise((y,n)=>{resolve=y;reject=n;}),get resolve(){return resolve;},get reject(){return reject;}};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(api){
  const controls=new Map(),calls=[],tasks=[],notes=[];
  const get=selector=>{if(!controls.has(selector))controls.set(selector,{value:'a',checked:false,innerHTML:'',addEventListener(){},click(){}});return controls.get(selector);};
  const context={project:{id:'a',revision:3,chapters:[{id:'ch-a'}]},chapterId:'ch-a',connected:true,tab:'write',
    editorSave:{revision:1},projectSelection:createProjectSelection(),startingProduction:new Set(),
    root:{querySelectorAll:()=>[]},$:get,api:async(path,body)=>{calls.push({path,body});return api(path,body);},
    dirtyEditor:()=>{},flush:async()=>{},saveSettingsIfVisible:async()=>{},render:()=>{},renderQueue:()=>{},cache:async()=>{},
    note:message=>notes.push(message),escape:String};
  context.action=fn=>{const task=(async()=>{await context.flush();try{await fn();}catch(e){notes.push(e.message);}})();tasks.push(task);return task;};
  vm.runInNewContext(source.slice(source.indexOf('function wire()'),source.indexOf('async function shotAction('))+';wire();',context);
  return {context,controls,calls,tasks,notes,get,select(value){const target=get('#productionProject');target.value=value;target.onchange({target});return tasks.at(-1);}};
}
test('actual project selector adopts only the latest requested project',async()=>{
  const b=deferred(),c=deferred(),f=fixture(path=>path.includes('=b')?b.promise:c.promise);
  const first=f.select('b');await settle();const second=f.select('c');await settle();
  c.resolve({id:'c',chapters:[{id:'ch-c'}]});await second;
  b.resolve({id:'b',chapters:[{id:'ch-b'}]});await first;
  assert.equal(f.context.project.id,'c');assert.equal(f.context.chapterId,'ch-c');assert.deepEqual(f.notes,[]);
});
test('autosaving before project selection may replace the snapshot without losing the navigation',async()=>{
  const f=fixture(async()=>({id:'b',chapters:[{id:'ch-b'}]}));
  f.context.flush=async()=>{f.context.project={...f.context.project,revision:4};f.context.editorSave.revision++;};
  await f.select('b');assert.equal(f.context.project.id,'b');assert.equal(f.calls.length,1);
});
test('typing during project retrieval retains the editor and requests an explicit new selection',async()=>{
  const response=deferred(),f=fixture(()=>response.promise);const pending=f.select('b');await settle();
  f.context.editorSave.revision++;response.resolve({id:'b',chapters:[{id:'ch-b'}]});await pending;
  assert.equal(f.context.project.id,'a');assert.equal(f.get('#productionProject').value,'a');assert.match(f.notes[0],/edits are retained/);
});
test('superseded responses cannot reset the selector or display a stale error after typing',async()=>{
  const b=deferred(),c=deferred(),f=fixture(path=>path.includes('=b')?b.promise:c.promise);
  const first=f.select('b');await settle();const second=f.select('c');await settle();f.context.editorSave.revision++;
  b.resolve({id:'b',chapters:[{id:'ch-b'}]});await first;
  assert.equal(f.get('#productionProject').value,'c');assert.deepEqual(f.notes,[]);
  c.reject(Error('failed'));await second;assert.equal(f.context.project.id,'a');
});
test('double-clicking full production issues one readiness request and one job',async()=>{
  const ready=deferred(),f=fixture(path=>path.startsWith('production-readiness')?ready.promise:Promise.resolve({jobs:[]}));
  const first=f.get('#productionFullVideo').onclick(),second=f.get('#productionFullVideo').onclick();await settle();
  assert.equal(f.calls.filter(c=>c.path.startsWith('production-readiness')).length,1);
  assert.equal(f.context.startingProduction.has('a'),true);
  ready.resolve({ready:true,revision:3,checks:[]});await Promise.all([first,second]);
  const jobs=f.calls.filter(c=>c.path==='jobs');assert.equal(jobs.length,1);assert.equal(jobs[0].body.project,'a');
  assert.equal(jobs[0].body.options.preflightRevision,3);assert.equal(f.context.startingProduction.size,0);
});
test('changing project during readiness never queues expensive work for either project',async()=>{
  const ready=deferred(),f=fixture(()=>ready.promise);const pending=f.get('#productionFullVideo').onclick();await settle();
  f.context.project={id:'b',chapters:[{id:'ch-b'}]};ready.resolve({ready:true,revision:3,checks:[]});await pending;
  assert.equal(f.calls.filter(c=>c.path==='jobs').length,0);assert.match(f.notes.at(-1),/selection changed/);
});
test('failed production preflight clears the pending button and reports its reason',async()=>{
  const f=fixture(async()=>({ready:false,revision:3,checks:[{blocking:true,ready:false,message:'Missing character reference'}]}));
  await f.get('#productionFullVideo').onclick();assert.equal(f.calls.filter(c=>c.path==='jobs').length,0);
  assert.equal(f.context.startingProduction.size,0);assert.match(f.notes.at(-1),/Missing character reference/);
});
test('a slow mutation response cannot select its original project again',async()=>{
  const response=deferred();const context={project:{id:'a'},api:()=>response.promise,cache:async()=>{},render:()=>{throw Error('unexpected render');}};
  vm.runInNewContext(source.slice(source.indexOf('async function mutate('),source.indexOf('async function patch('))+';this.change=mutate;',context);
  const pending=context.change('edit',{});context.project={id:'b'};
  response.resolve({id:'a',chapters:[{}],revision:4});await pending;assert.equal(context.project.id,'b');
});

test('a stale same-project mutation cannot replace a later committed revision',async()=>{
  const response=deferred();let cached=0,rendered=0;
  const context={project:{id:'a',revision:7},api:()=>response.promise,cache:async()=>{cached++;},render:()=>{rendered++;}};
  vm.runInNewContext(source.slice(source.indexOf('async function mutate('),source.indexOf('async function patch('))+';this.change=mutate;',context);
  const pending=context.change('edit',{});context.project={id:'a',revision:9};
  response.resolve({id:'a',chapters:[{}],revision:8});await pending;
  assert.equal(context.project.revision,9);assert.equal(cached,0);assert.equal(rendered,0);
});
test('the actual character editor reads identity and appearance from its mounted modal',async()=>{
  const fields={'#profileName':{value:'Sarah'},'#profileDescription':{value:'Story character'},'#profileType':{value:'main'},
    '#profileAdvanced':{value:JSON.stringify({id:'sarah',permanentIdentity:{hair:'old'},defaultAppearance:{outfit:'old'}})}};
  let save,patch;
  const context={escape:String,options:()=>'',JSON,$:id=>fields[id],
    formDialog:(title,body,onSave)=>{save=onSave;},patch:async(...args)=>{patch=args;}};
  vm.runInNewContext(source.slice(source.indexOf('function editPerson('),source.indexOf('function editShot('))+';this.edit=editPerson;',context);
  context.edit({id:'sarah',name:'Old',type:'main',description:'',permanentIdentity:{hair:'old'},defaultAppearance:{outfit:'old'}},true);
  await save({querySelectorAll:selector=>selector.includes('identity')?[{dataset:{identityField:'hair'},value:'brown'}]:[{dataset:{appearanceField:'outfit'},value:'school uniform'}]});
  assert.equal(patch[0],'character');assert.equal(patch[1],'sarah');assert.equal(patch[2].permanentIdentity.hair,'brown');assert.equal(patch[2].defaultAppearance.outfit,'school uniform');assert.equal(patch[2].id,undefined);
});
