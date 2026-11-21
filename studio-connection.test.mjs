import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {resolveConnectedProject,canAdoptConnection,connectionLoading,readProjectPicker,mergePickerProjects,saveBrowserOnlyDraft} from './studio-connection.mjs';

test('fast helper exposes available projects while a complete legacy list still works',async()=>{
  const local={id:'local'},cloud={id:'cloud'},draft={id:'draft',unsynced:true},snapshot={projects:[local],complete:false,refreshing:true};
  const calls=[];const api=async path=>{calls.push(path);return path==='projects'?[local,cloud]:snapshot;};
  assert.deepEqual(await readProjectPicker(api,{projectPickerAvailable:true}),snapshot);
  assert.deepEqual(await readProjectPicker(api,{}),{projects:[local,cloud],complete:true,refreshing:false});
  assert.deepEqual(calls,['projects?picker=true','projects']);
  assert.deepEqual(mergePickerProjects(snapshot,[local,cloud,draft]),[local,cloud,draft]);
  assert.deepEqual(mergePickerProjects({...snapshot,complete:true,refreshing:false},[local,cloud,draft]),[local,draft]);
});

test('complete picker keeps inactive browser backups and removes disappeared helper-only projects',()=>{
  const cloud={id:'cloud'},backup={id:'backup',browserSaved:true,unsynced:false};
  const shared={id:'shared',browserSaved:true};
  const merged=mergePickerProjects({projects:[{id:'shared'}],complete:true},[cloud,backup,shared]);
  assert.deepEqual(merged,[shared,{...backup,browserOnly:true}]);
  assert.deepEqual(mergePickerProjects({projects:[{id:'backup'}],complete:true},merged),[
    {id:'backup',browserSaved:true},{...shared,browserOnly:true}]);
});
test('browser-only sync creates exclusively after confirmed absence and checks again during import',async()=>{
 const draft={...fixture(),_unsynced:true};const calls=[];
 await saveBrowserOnlyDraft(async(path,body)=>{
   calls.push(path);
   if(!body)throw Object.assign(Error('missing'),{status:404,code:'PROJECT_NOT_FOUND'});
   assert.equal(body.onlyIfMissing,true);assert.equal(body.project._unsynced,undefined);
   assert.equal(body.project.id,draft.id);return body.project;
 },draft,{projectCheckpointAvailable:true});
 assert.deepEqual(calls,['project-checkpoint?id='+draft.id,'import']);
 for(const error of [null,{status:404,code:'WRONG_ENDPOINT'},{status:503}]){
   await assert.rejects(saveBrowserOnlyDraft(async(path,body)=>{
     assert.equal(body,undefined);
     if(error)throw Object.assign(Error('unavailable'),error);
     return fixture();
   },draft,{projectCheckpointAvailable:true}),/exists|unavailable/);
 }
});

test('remembered available project or cloud-only selection wins over the first local project',async()=>{
  for(const projects of [[{id:'first'},{id:'remembered'}],[{id:'first'}]]){
    const calls=[];const api=async path=>{calls.push(path);return {id:'remembered'};};
    const r=await resolveConnectedProject({project:{_connectionPlaceholder:true},projects,api,
      preferredId:'remembered',catalogueComplete:false});
    assert.equal(r.project.id,'remembered');assert.deepEqual(calls,['project?id=remembered']);
  }
});

test('incomplete cloud discovery never creates a blank story or hides a retrieval failure',async()=>{
  const calls=[];const api=async path=>{calls.push(path);throw Object.assign(new Error('missing'),{status:404,code:'PROJECT_NOT_FOUND'});};
  await assert.rejects(resolveConnectedProject({projects:[],api,catalogueComplete:false}),/Loading saved cloud projects/);
  assert.deepEqual(calls,[]);
  await assert.rejects(resolveConnectedProject({projects:[],api,catalogueComplete:false,preferredId:'remembered',catalogueError:'Cloud catalogue offline.'}),/Cloud catalogue offline/);
  assert.deepEqual(calls,['project?id=remembered']);
  await assert.rejects(resolveConnectedProject({projects:[{id:'fallback'}],api:async()=>{throw new Error('access unavailable');},
    catalogueComplete:false,preferredId:'remembered'}),/access unavailable/);
});
test('paired empty startup placeholders show loading while real offline drafts remain editable',()=>{
  const placeholder={...fixture(),_connectionPlaceholder:true};
  assert.equal(connectionLoading(placeholder,true,true),true);
  assert.equal(connectionLoading(placeholder,true,false),false);
  assert.equal(connectionLoading(placeholder,false,true),false);
  assert.equal(connectionLoading({...fixture(),_unsynced:true},true,true),false);
});
import {createEditorSave} from './studio-editor-save.mjs';
import {backupSnapshot,rememberOfflineEdit} from './studio-project-safety.mjs';
const deferred=()=>{let resolve,reject;return {promise:new Promise((yes,no)=>{resolve=yes;reject=no;}),get resolve(){return resolve;},get reject(){return reject;}};};
const fixture=()=>({id:'pr-0123456789abcdef',revision:3,name:'Fixture',characters:[],settings:{image:{model:'preserve-selected'}},chapters:[{id:'chapter',name:'Chapter',sourceText:'Saved story',scenes:[{shots:[{imagePath:'saved.png'}]}]}]});

test('a network/server/permission error never triggers project import or creation',async()=>{
  for(const status of [undefined,401,403,404,500]){
    const calls=[];
    await assert.rejects(resolveConnectedProject({project:fixture(),projects:[],api:async(path)=>{
      calls.push(path);throw Object.assign(Error('unavailable'),{status});
    }}),/unavailable/);
    assert.equal(calls.length,1);
  }
});
test('reconnect never republishes an offline edit of a missing revision-zero private project',async()=>{
  const original={...fixture(),revision:0,_unsynced:true},calls=[];
  const result=await resolveConnectedProject({project:original,projects:[],api:async(path)=>{
    calls.push(path);if(path.startsWith('project?'))throw Object.assign(Error('missing'),{status:404,code:'PROJECT_NOT_FOUND'});
    assert.fail('Reconnect attempted to publish a revision-zero browser copy');
  }});
  assert.deepEqual(calls,['project?id='+original.id]);assert.equal(result.browserOnly,true);
  assert.equal(result.project.chapters[0].scenes[0].shots[0].imagePath,'saved.png');
});
test('existing offline edits require explicit sync and retain remote generations',async()=>{
  const local={...fixture(),_unsynced:true};
  const result=await resolveConnectedProject({project:local,projects:[],api:async()=>({...fixture(),revision:4})});
  assert.equal(result.preserveOffline,true);assert.equal(result.project.revision,4);
});
test('a previously mirrored project missing from the helper remains browser-only pending explicit publication',async()=>{
 for(const unsynced of [true,false]){
  const local={...fixture(),_unsynced:unsynced},calls=[];
  const result=await resolveConnectedProject({project:local,projects:[],api:async path=>{
   calls.push(path);throw Object.assign(Error('missing'),{status:404,code:'PROJECT_NOT_FOUND'});
  }});
  assert.deepEqual(calls,['project?id='+local.id]);assert.equal(result.project._unsynced,true);
  assert.equal(result.browserOnly,true);assert.equal(local._unsynced,unsynced);
 }
});
test('only an unchanged selected editor can adopt the returned snapshot',()=>{
  const current=fixture(),remote={...fixture(),revision:4};
  const opts={requestedProject:current,editorRevision:3,startEditorRevision:3,preserveOffline:false};
  assert.equal(canAdoptConnection(current,remote,opts),true);
  assert.equal(canAdoptConnection(current,remote,{...opts,editorRevision:4}),false);
  assert.equal(canAdoptConnection({...current},remote,opts),false);
  assert.equal(canAdoptConnection(current,remote,{...opts,preserveOffline:true}),false);
  assert.equal(canAdoptConnection(current,{...remote,revision:2},opts),false);
});
test('an empty saved remote project can replace the startup placeholder',()=>{
 const current={...fixture(),_connectionPlaceholder:true},empty={...fixture(),chapters:[]};
 assert.equal(canAdoptConnection(current,empty,{requestedProject:current,editorRevision:0,startEditorRevision:0}),true);
 assert.deepEqual(empty.chapters,[]);
});

function browserFixture(api){
  const editor={value:'Saved story'},chapterName={value:'Chapter'};
  const context={key:'fixture-not-a-key',connectionPromise:undefined,connected:true,dirty:false,cacheWarning:'',health:{},queue:{},projects:[],project:fixture(),chapterId:'chapter',tab:'write',
    pairingKey:()=> 'fixture-not-a-key',mediaCache:new Map(),api,resolveConnectedProject,canAdoptConnection,backupSnapshot,
    copyAvailableProjects:()=>{},pickerGeneration:0,pickerPending:false,savedId:undefined,readProjectPicker,mergePickerProjects,
    cache:async()=>{},note:()=>{},render:()=>{editor.value=context.project.chapters[0].sourceText;},
    ch:()=>context.project.chapters[0],$:selector=>selector==='#chapterStory'?editor:selector==='#chapterName'?chapterName:undefined};
  context.editorSave=createEditorSave(async()=>{
    context.project.chapters[0].sourceText=editor.value;context.project._unsynced=true;
  });
  context.flush=async()=>{await context.editorSave.flush();context.dirty=context.editorSave.dirty;};
  const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
  const section=source.slice(source.indexOf('function connect()'),source.indexOf('async function mutate('));
  vm.runInNewContext(section+';this.startConnection=connect;',context);
  return {context,editor,type(text){editor.value=text;context.dirty=true;context.editorSave.markDirty();}};
}
test('actual reconnect preserves text typed during a delayed project response',async()=>{
  const response=deferred();const f=browserFixture(async path=>path==='health'?{queue:{},providers:{}}:path==='projects'?[{id:fixture().id}]:response.promise);
  const finished=f.context.startConnection();await new Promise(resolve=>setImmediate(resolve));
  f.type('Manual addition during reconnect');response.resolve({...fixture(),revision:4});await finished;
  assert.equal(f.editor.value,'Manual addition during reconnect');assert.equal(f.context.project._unsynced,true);assert.equal(f.context.connected,true);
  assert.equal(f.context.project.chapters[0].scenes[0].shots[0].imagePath,'saved.png');
});
test('text typed before helper health returns is retained without automatic sync',async()=>{
  const health=deferred(),calls=[];const f=browserFixture(async(path)=>{
    calls.push(path);return path==='health'?health.promise:path==='projects'?[{id:fixture().id}]:fixture();
  });
  const finished=f.context.startConnection();f.type('Typed during startup');health.resolve({queue:{},providers:{}});await finished;
  assert.equal(f.editor.value,'Typed during startup');assert.equal(f.context.project._unsynced,true);
  assert.ok(!calls.includes('import'));
});
test('concurrent reconnect requests share one health/project transaction',async()=>{
  const health=deferred();let calls=0;const f=browserFixture(async path=>{
    if(path==='health'){calls++;return health.promise;}return path==='projects'?[{id:fixture().id}]:fixture();
  });
  const first=f.context.startConnection(),second=f.context.startConnection();assert.equal(first,second);
  health.resolve({queue:{},providers:{}});await first;assert.equal(calls,1);assert.equal(f.context.connectionPromise,undefined);
});
test('failed connection and browser checkpoint retain the current editor for emergency export',async()=>{
  const response=deferred();const f=browserFixture(async()=>response.promise);
  const finished=f.context.startConnection();f.type('Latest unsaved words');
  response.reject(Error('helper not reachable'));await finished;
  assert.equal(f.editor.value,'Latest unsaved words');assert.equal(f.context.project._unsynced,true);assert.equal(f.context.connected,false);
});


test('editing after reconnect checkpoints locally until the complete offline draft is synced',async()=>{
  const project={...fixture(),_unsynced:true};
  project.chapters.push({id:'second',sourceText:'Offline correction in chapter two'});
  const checkpoints=[], status={};
  const context={project,connected:true,rememberOfflineEdit,health:{storage:{enabled:true}},ch:()=>project.chapters[0],
    mutate:async()=>assert.fail('Partial draft edit reached the helper before explicit sync'),
    cache:async()=>checkpoints.push(structuredClone(project)), $:()=>status};
  const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
  vm.runInNewContext(source.slice(source.indexOf('async function patch('),source.indexOf('async function action('))+';this.edit=patch;',context);
  await context.edit('chapter','chapter',{sourceText:'New chapter one'});
  assert.equal(checkpoints.length,1);
  assert.equal(checkpoints[0].chapters[0].sourceText,'New chapter one');
  assert.equal(checkpoints[0].chapters[1].sourceText,'Offline correction in chapter two');
  assert.equal(checkpoints[0]._unsynced,true);
  assert.match(status.textContent,/Saved in browser/);
});

test('startup keeps a cloud-only remembered project ahead of unrelated browser backups',async()=>{
 const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
 const bootstrap=source.slice(source.indexOf('try{savedId=localStorage'),source.indexOf('setInterval(() => void poll()'));
 const remembered='pr-fedcba9876543210',other=fixture(),placeholder={...other,id:'pr-0000000000000000'};
 const context={localStorage:{getItem:()=>remembered},key:'paired',loadStudioProject:async id=>id===other.id?other:undefined,
   listStudioProjectSummaries:async()=>[{id:other.id}],offlineProject:()=>placeholder,
   cache:async()=>{},render:()=>{}, connect:async()=>{}};
 vm.runInNewContext('let project,projects,savedId,chapterId,cacheWarning; this.start=async()=>{'+bootstrap+';return {project,savedId,projects};};',context);
 const initial=await context.start();
 assert.equal(initial.project._connectionPlaceholder,true);assert.equal(initial.projects[0].id,other.id);
 const remote=await resolveConnectedProject({...initial,preferredId:initial.savedId,catalogueComplete:false,
   api:async path=>{assert.equal(path,'project?id='+remembered);return {...other,id:remembered};}});
 assert.equal(remote.project.id,remembered);
});

test('an old edit that loses its connection cannot overwrite the newly selected story',async()=>{
 const original=fixture(),other={...fixture(),id:'pr-ffffffffffffffff'};
 const pending=deferred();
 const context={project:original,connected:true,health:{},chapterId:'chapter',ch:()=>context.project.chapters[0],
   mutate:async()=>pending.promise,cache:async()=>{},rememberOfflineEdit,$:()=>({})};
 const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
 vm.runInNewContext(source.slice(source.indexOf('async function patch('),source.indexOf('async function action('))+';this.edit=patch;',context);
 const operation=context.edit('chapter','chapter',{sourceText:'Previous project change'});
 context.project=other;pending.reject(Error('Cannot reach the NVIDIA helper'));
 await assert.rejects(operation,/Cannot reach the NVIDIA helper/);
 assert.equal(other.chapters[0].sourceText,'Saved story');assert.equal(other._unsynced,undefined);
});

test('a delayed browser checkpoint cannot replace the remembered project after switching',async()=>{
 const source=fs.readFileSync(new URL('./story-studio.mjs',import.meta.url),'utf8');
 const pending=deferred(),original=fixture(),other={...fixture(),id:'pr-fedcba9876543210'},remembered=[];
 const context={project:original,health:{},connected:false,cacheWarning:'',saveStudioProject:async p=>{if(p.id===original.id)await pending.promise;},
   checkpointProject:(await import('./studio-project-safety.mjs')).checkpointProject,localStorage:{setItem:(_key,value)=>remembered.push(value)},note:()=>{}};
 vm.runInNewContext(source.slice(source.indexOf('async function cache('),source.indexOf('async function mediaCapability('))+';this.save=cache;',context);
 const first=context.save();context.project=other;await context.save();pending.resolve();await first;
 assert.deepEqual(remembered,[other.id]);
});
