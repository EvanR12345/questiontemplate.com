import test from 'node:test';
import assert from 'node:assert/strict';
import {createMediaLoader} from './studio-media-loader.mjs';
const tick=()=>new Promise(resolve=>setImmediate(resolve));
const node=(path,tagName='IMG')=>({dataset:{asset:path},tagName,isConnected:true});
const root=nodes=>({querySelectorAll:()=>nodes});
function player(path='story.mp4'){
  const events=new Map(),video={...node(path,'VIDEO'),currentTime:0,playbackRate:1,duration:86400,plays:0,loads:0,
    addEventListener(name,handler){events.set(name,handler);},removeEventListener(name,handler){if(events.get(name)===handler)events.delete(name);},
    emit(name){events.get(name)?.();},play(){this.plays++;return Promise.resolve();},load(){this.loads++;}};
  return video;
}

test('expired playback link renews once and restores position, speed and active playback',async()=>{
  let clock=0;const requests=[],video=player();
  const loader=createMediaLoader({Observer:null,now:()=>clock,resolve:async(path,pid,options)=>{requests.push(options);return options.refresh?'new-link':'old-link';}});
  loader.load(root([video]),'project');await tick();video.emit('playing');video.currentTime=7421;video.playbackRate=1.25;
  video.emit('error');await tick();assert.equal(requests.length,1,'a fresh decoder error does not cause a renewal loop');
  clock=3600000;video.emit('error');video.emit('error');await tick();
  assert.equal(requests.length,2);assert.equal(requests[1].refresh,true);assert.equal(video.src,'new-link');assert.equal(video.loads,1);
  video.currentTime=0;video.emit('loadedmetadata');await tick();
  assert.equal(video.currentTime,7421);assert.equal(video.playbackRate,1.25);assert.equal(video.plays,1);
  video.emit('error');await tick();assert.equal(requests.length,2);
});

test('paused playback stays paused and a late renewal cannot update a different project',async()=>{
  let clock=0,release;const video=player();
  const loader=createMediaLoader({Observer:null,now:()=>clock,resolve:async(path,pid,options)=>options.refresh?await new Promise(r=>{release=r;}):pid});
  loader.load(root([video]),'first');await tick();video.currentTime=52;
  clock=3600000;video.emit('error');await tick();video.emit('loadedmetadata');assert.equal(video.plays,0);
  loader.load(root([video]),'second');await tick();release('expired-project');await tick();
  assert.equal(video.src,'second');assert.equal(video.loads,0);assert.equal(video.plays,0);
});

test('renewal errors remain visible and do not restart or redownload the video',async()=>{
  let clock=0,calls=0;const video=player();
  const loader=createMediaLoader({Observer:null,now:()=>clock,resolve:async(path,pid,options)=>{calls++;if(options.refresh)throw Error('helper offline');return 'initial';}});
  loader.load(root([video]),'project');await tick();clock=3600000;video.emit('error');await tick();
  assert.match(video.title,/helper offline/);assert.equal(video.src,'initial');assert.equal(video.loads,0);assert.equal(calls,2);
});
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
