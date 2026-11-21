import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {createProjectSelection} from './studio-form-drafts.mjs';
import {selectedStrategy,strategyOptions} from './studio-generation-plan.mjs';
const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
const deferred=()=>{let resolve,reject;return {promise:new Promise((y,n)=>{resolve=y;reject=n;}),get resolve(){return resolve;},get reject(){return reject;}};};
const settle=()=>new Promise(resolve=>setImmediate(resolve));

test('repair control reflects automatic completion and restores manual choices without disabling checks',()=>{
 const controls={'#settingUnattended':{checked:true},'#settingCheckLevel':{value:'practical'},
 '#settingQCPolicy':{},'#settingSampleEvery':{},'#settingRetries':{value:'2'},'#settingRepair':{checked:false}};
 const context={$:name=>controls[name],project:{settings:{automaticRepair:false}}};
 vm.runInNewContext(source.slice(source.indexOf('  let manualRepairChoice='),source.indexOf('  const updateWatermark =')),context);
 assert.equal(controls['#settingRepair'].checked,true);assert.equal(controls['#settingRepair'].disabled,true);
 controls['#settingUnattended'].checked=false;controls['#settingUnattended'].onchange();
 assert.equal(controls['#settingRepair'].checked,false);assert.equal(controls['#settingRepair'].disabled,false);
 controls['#settingRepair'].checked=true;controls['#settingRepair'].onchange();
 controls['#settingUnattended'].checked=true;controls['#settingUnattended'].onchange();
 controls['#settingUnattended'].checked=false;controls['#settingUnattended'].onchange();
 assert.equal(controls['#settingRepair'].checked,true);
 controls['#settingRetries'].value='0';controls['#settingRetries'].oninput();
 assert.equal(controls['#settingRepair'].checked,false);assert.equal(controls['#settingCheckLevel'].value,'practical');
 controls['#settingRetries'].value='2';controls['#settingRetries'].oninput();
 assert.equal(controls['#settingRepair'].checked,true);
 controls['#settingCheckLevel'].value='off';controls['#settingCheckLevel'].onchange();
 assert.equal(controls['#settingRepair'].checked,false);assert.equal(controls['#settingRepair'].disabled,true);
});
function fixture(api){
  const controls=new Map(),calls=[],tasks=[],notes=[];
  const get=selector=>{if(!controls.has(selector))controls.set(selector,{value:selector==='#productionStrategy'?'align':'a',checked:false,innerHTML:'',addEventListener(){},click(){}});return controls.get(selector);};
  const context={project:{id:'a',revision:3,chapters:[{id:'ch-a'}],settings:{image:{provider:'comfyui'},director:{provider:'openai-luna'},budget:{openaiUSD:1}}},chapterId:'ch-a',connected:true,tab:'write',
    health:{cloudSchedulingAvailable:true,unattendedProductionAvailable:true},strategyChoices:new Map(),strategyDirty:new Set(),selectedStrategy,strategyOptions,
    projects:[],loadStudioProject:async()=>undefined,
    loadPerformance:async()=>({history:{},forecast:{}}),patch:async(scope,target,patch)=>Object.assign(context.project,patch),
    editorSave:{revision:1},projectSelection:createProjectSelection(),startingProduction:new Set(),
    root:{querySelectorAll:()=>[]},$:get,api:async(path,body)=>{calls.push({path,body});return api(path,body);},
    dirtyEditor:()=>{},flush:async()=>{},saveSettingsIfVisible:async()=>{},render:()=>{},renderQueue:()=>{},cache:async()=>{},
    note:message=>notes.push(message),escape:String};
  context.action=fn=>{const task=(async()=>{await context.flush();try{await fn();}catch(e){notes.push(e.message);}})();tasks.push(task);return task;};
  const shared=source.slice(source.indexOf('function productionOptions('),source.indexOf('async function submit('));
  vm.runInNewContext(shared+source.slice(source.indexOf('function wire()'),source.indexOf('async function shotAction('))+';wire();',context);
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
test('selecting a project with pending offline edits keeps its browser draft when paired',async()=>{
  const f=fixture(async()=>{throw Error('Unexpected helper project fetch');});
  const draft={id:'b',_unsynced:true,chapters:[{id:'ch-b',sourceText:'Local correction'}]};
  f.context.loadStudioProject=async id=>id==='b'?draft:undefined;
  await f.select('b');
  assert.equal(f.context.project,draft);
  assert.equal(f.calls.length,0);
});
test('typing during project retrieval retains the editor and requests an explicit new selection',async()=>{
  const response=deferred(),f=fixture(()=>response.promise);const pending=f.select('b');await settle();
  f.context.editorSave.revision++;response.resolve({id:'b',chapters:[{id:'ch-b'}]});await pending;
  assert.equal(f.context.project.id,'a');assert.equal(f.get('#productionProject').value,'a');assert.match(f.notes[0],/edits are retained/);
});
test('picker opens a clean browser-only recovery copy without importing or editing it',async()=>{
 const f=fixture(async()=>{throw Error('Unexpected helper request');});
 const stored={id:'b',revision:3,chapters:[{id:'ch-b',sourceText:'Recovered words'}]};
 f.context.projects=[{id:'b',browserSaved:true,browserOnly:true}];f.context.loadStudioProject=async()=>stored;
 await f.select('b');
 assert.equal(f.context.project.id,'b');assert.equal(f.context.project._unsynced,true);
 assert.equal(stored._unsynced,undefined);assert.equal(f.calls.length,0);
});
test('empty project selection preserves zero chapters and adds the first only on request',async()=>{
 for(const connected of [true,false]){
  const empty={id:'b',revision:5,chapters:[]};
  const f=fixture(async()=>empty);f.context.connected=connected;
  if(!connected)f.context.loadStudioProject=async()=>empty;
  await f.select('b');
  assert.equal(f.context.project,empty);assert.equal(empty.chapters.length,0);assert.equal(f.context.chapterId,undefined);
  f.context.offlineChapter=n=>({id:'ch-new',number:n});
  let writes=0;
  f.context.mutate=async(path,body)=>{assert.equal(path,'chapter');assert.equal(body.action,'add');writes++;empty.chapters.push({id:'ch-new',number:1});};
  await f.get('#productionAddChapter').onclick();
  assert.equal(writes,connected?1:0);assert.equal(empty.chapters.length,1);assert.equal(f.context.chapterId,'ch-new');
  assert.equal(f.context.tab,'write');if(!connected)assert.equal(empty._unsynced,true);
 }
});

test('archive history reads on open and restores only an explicitly selected edition without changing editors',async()=>{
 const calls=[],controls=new Map(),rows=[{id:'hidden',edition:'r3-abc',name:'Missing story',revision:3,chapters:2,updated:1711000000},
   {id:'partial',edition:'r10-xyz',name:'Writing only',revision:10,chapters:2,missingMedia:1,recoverable:false,writingCopyId:'new-id'}];
 const get=k=>{if(!controls.has(k))controls.set(k,{value:'',options:[],querySelector:get,add(o){this.options.push(o);}});return controls.get(k);};
 const modal={open:true,querySelector:get};
 let onSave;
 const project={id:'editing',text:'unpushed text'};
 const context={project,Option:class{constructor(text,value){Object.assign(this,{text,value});}},pickerPending:false,
   formDialog:(_title,_body,fn)=>{onSave=fn;return modal;},api:async(path,body)=>{
     calls.push([path,body]);return path==='archived-projects'?{projects:rows,unreadableEditions:1}:body.mode==='writing'?{project:{id:'new-id'},writingOnly:true}:{project:rows[0]};
   },refreshProjects:async()=>calls.push(['refresh']),note:()=>{},
   document:{activeElement:null},root:{querySelector:()=>({value:project.id})},projectOptions:()=>''};
 vm.runInNewContext(source.slice(source.indexOf('async function openArchivedProjects('),source.indexOf('function jsonDialog('))+';this.openHistory=openArchivedProjects;',context);
 await context.openHistory();
 assert.deepEqual(calls,[['archived-projects',undefined]]);assert.equal(get('.dialog-save').disabled,true);
 await assert.rejects(onSave(modal),/Select an archived edition/);
 assert.equal(calls.length,1);assert.match(get('.archive-status').textContent,/Unreadable editions: 1/);
 const select=get('.archived-edition');select.value='0';select.onchange();assert.equal(get('.dialog-save').disabled,false);
 await onSave(modal);assert.deepEqual(JSON.parse(JSON.stringify(calls.slice(1))),[['recover-archived',{project:'hidden',edition:'r3-abc'}],['refresh']]);
 assert.equal(context.project,project);assert.equal(project.text,'unpushed text');
 select.value='1';select.onchange();
 assert.equal(get('.recovery-type').value,'writing');assert.equal(get('[value="full"]').disabled,true);
 await onSave(modal);assert.deepEqual(JSON.parse(JSON.stringify(calls.at(-2))),['recover-archived',{project:'partial',edition:'r10-xyz',mode:'writing'}]);
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
  assert.equal(jobs[0].body.options.generationStrategy,'align');assert.equal(jobs[0].body.options.overlap,true);
  assert.equal(jobs[0].body.options.unattended,true);
});

test('automatic completion cannot silently run on a helper without repair/cloud-save support',async()=>{
  const f=fixture(async()=>({ready:true,revision:3,checks:[]}));f.context.health.unattendedProductionAvailable=false;
  await f.get('#productionFullVideo').onclick();
  assert.equal(f.calls.length,0);assert.match(f.notes.at(-1),/older helper cannot complete/);
});

test('selected fastest dispatch reaches the actual production job and is saved',async()=>{
  const f=fixture(async()=>({ready:true,revision:3,checks:[]}));
  f.get('#productionStrategy').value='fastest';f.get('#productionStrategy').onchange({target:f.get('#productionStrategy')});
  await f.tasks.at(-1);
  assert.equal(f.context.project.generationStrategy,'fastest');assert.equal(f.context.strategyDirty.size,0);
  await f.get('#productionFullVideo').onclick();
  assert.equal(f.calls.find(c=>c.path==='jobs').body.options.generationStrategy,'fastest');
});

test('Dense reaches the real job options only on the supported managed automatic setup',async()=>{
 const f=fixture(async()=>({ready:true,revision:3,checks:[]}));
 f.context.health.denseImagePhaseAvailable=true;f.context.health.managedGPU={configured:true};
 f.get('#productionStrategy').value='dense';await f.get('#productionFullVideo').onclick();
 assert.equal(f.calls.find(c=>c.path==='jobs').body.options.generationStrategy,'dense');
 assert.equal(f.calls.find(c=>c.path==='jobs').body.options.overlap,true);
});
test('older, unmanaged or manual helpers cannot silently execute Dense as another strategy',async()=>{
 for(const mode of ['old','unmanaged','manual']){
  const f=fixture(async()=>({ready:true,revision:3,checks:[]}));f.get('#productionStrategy').value='dense';
  f.context.health.denseImagePhaseAvailable=mode!=='old';f.context.health.managedGPU={configured:mode!=='unmanaged'};
  if(mode==='manual')f.context.project.settings.unattendedProduction=false;
  await f.get('#productionFullVideo').onclick();assert.equal(f.calls.filter(c=>c.path==='jobs').length,0);
  assert.match(f.notes.at(-1),/Dense generation requires/);
 }
});

test('an older helper cannot silently execute a timed strategy as the old workflow',async()=>{
  const f=fixture(async()=>({ready:true,revision:3,checks:[]}));f.context.health.cloudSchedulingAvailable=false;
  await f.get('#productionFullVideo').onclick();
  assert.equal(f.calls.filter(c=>c.path==='jobs').length,0);assert.match(f.notes.at(-1),/Timed generation requires/);
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
