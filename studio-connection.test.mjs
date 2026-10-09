import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import {resolveConnectedProject,canAdoptConnection} from './studio-connection.mjs';
import {createEditorSave} from './studio-editor-save.mjs';
import {backupSnapshot} from './studio-project-safety.mjs';
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
test('only an explicit missing project can import a retained local snapshot',async()=>{
  const original={...fixture(),_unsynced:true},calls=[];
  const result=await resolveConnectedProject({project:original,projects:[],api:async(path,body)=>{
    calls.push(path);if(path.startsWith('project?'))throw Object.assign(Error('missing'),{status:404,code:'PROJECT_NOT_FOUND'});
    assert.equal(body.project._unsynced,undefined);assert.equal(body.project.chapters[0].scenes[0].shots[0].imagePath,'saved.png');
    return {...body.project,revision:4};
  }});
  assert.deepEqual(calls,['project?id='+original.id,'import']);assert.equal(result.preserveOffline,false);assert.equal(original._unsynced,true);
});
test('existing offline edits require explicit sync and retain remote generations',async()=>{
  const local={...fixture(),_unsynced:true};
  const result=await resolveConnectedProject({project:local,projects:[],api:async()=>({...fixture(),revision:4})});
  assert.equal(result.preserveOffline,true);assert.equal(result.project.revision,4);
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

function browserFixture(api){
  const editor={value:'Saved story'},chapterName={value:'Chapter'};
  const context={key:'fixture-not-a-key',connectionPromise:undefined,connected:true,dirty:false,cacheWarning:'',health:{},queue:{},projects:[],project:fixture(),chapterId:'chapter',tab:'write',
    pairingKey:()=> 'fixture-not-a-key',mediaCache:new Map(),api,resolveConnectedProject,canAdoptConnection,backupSnapshot,
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
