import test from 'node:test';
import assert from 'node:assert/strict';
import {createMediaLoader} from './studio-media-loader.mjs';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const node=(path,tagName='IMG')=>({dataset:{asset:path},tagName,isConnected:true});
const root=nodes=>({querySelectorAll:()=>nodes});
test('offscreen images request no links while audio remains available',async()=>{
  let observed;
  class Observer {constructor(callback){this.callback=callback;observed=this;}observe(){}unobserve(){}disconnect(){}}
  const requested=[],loader=createMediaLoader({resolve:async path=>{requested.push(path);return path;},Observer});
  const images=Array.from({length:80},(_,i)=>node('photo-'+i)),audio=node('chapter.wav','AUDIO');
  loader.load(root([...images,audio]),'project');await tick();
  assert.deepEqual(requested,['chapter.wav']);
  observed.callback([{target:images[3],isIntersecting:true}]);await tick();
  assert.deepEqual(requested,['chapter.wav','photo-3']);
  assert.equal(images[3].src,'photo-3');assert.equal(images[2].src,undefined);
});
test('project switches cannot exceed the limit or accept late URLs',async()=>{
  const releases=[],calls=[];
  const loader=createMediaLoader({limit:2,Observer:null,resolve:(path,project)=>new Promise(resolve=>{calls.push(project+'/'+path);releases.push(resolve);})});
  const old=[node('A'),node('B'),node('C')],fresh=node('D');
  loader.load(root(old),'old');await tick();assert.equal(calls.length,2);
  loader.load(root([fresh]),'fresh');await tick();assert.equal(calls.length,2);
  releases[0]('old-link');await tick();assert.deepEqual(calls,['old/A','old/B','fresh/D']);
  assert.equal(old[0].src,undefined);
  releases[2]('new-link');releases[1]('old-link');await tick();
  assert.equal(fresh.src,'new-link');assert.equal(old[1].src,undefined);
});
test('duplicate observer callbacks resolve each node once and failure is contained',async()=>{
  let observed,calls=0;
  class Observer {constructor(callback){this.callback=callback;observed=this;}observe(){}unobserve(){}disconnect(){}}
  const image=node('broken');const loader=createMediaLoader({Observer,resolve:async()=>{calls++;throw Error('unavailable');}});
  loader.load(root([image]),'project');
  observed.callback([{target:image,isIntersecting:true},{target:image,isIntersecting:true}]);await tick();
  assert.equal(calls,1);assert.match(image.alt,/unavailable/);
});
test('detached queued nodes and canceled views do not issue requests',async()=>{
  const calls=[],loader=createMediaLoader({Observer:null,resolve:async path=>{calls.push(path);return path;}});
  const detached=node('detached');detached.isConnected=false;
  loader.load(root([detached]),'project');loader.clear();await tick();assert.deepEqual(calls,[]);
});
test('reloading the same mounted preview resolves a fresh project capability',async()=>{
  const image=node('art.png');const calls=[];
  const loader=createMediaLoader({Observer:null,resolve:async(path,project)=>{calls.push(project);return project+'/'+path;}});
  loader.load(root([image]),'first');await tick();
  loader.load(root([image]),'second');await tick();
  assert.deepEqual(calls,['first','second']);assert.equal(image.src,'second/art.png');
});

test('late visibility callbacks cannot disconnect the current view observer',async()=>{
  const observers=[];
  class Observer {
    constructor(callback){this.callback=callback;this.removed=[];observers.push(this);}
    observe(){}unobserve(target){this.removed.push(target);}disconnect(){}
  }
  const calls=[],image=node('art.png');
  const loader=createMediaLoader({Observer,resolve:async(path,project)=>{calls.push(project);return project;}});
  loader.load(root([image]),'old');loader.load(root([image]),'fresh');
  observers[0].callback([{target:image,isIntersecting:true}]);await tick();
  assert.deepEqual(observers[1].removed,[]);assert.deepEqual(calls,[]);
  observers[1].callback([{target:image,isIntersecting:true}]);await tick();
  assert.deepEqual(calls,['fresh']);assert.equal(image.src,'fresh');
});
