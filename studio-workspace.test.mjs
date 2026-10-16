import test from 'node:test';
import assert from 'node:assert/strict';
import {chapterStage,problemList,nextAction,impact,fileLocation,versionsOf,filterChapters,createWorkspaceTools} from './studio-workspace.mjs';
const chapter=(n,extra={})=>({id:'c'+n,number:n,name:'Chapter '+n,sourceText:'The sword remains.',scenes:[],audio:{},render:{},...extra});
const project=(chapters)=>({id:'p1',chapters,settings:{},warnings:[],render:{}});
test('next action follows actual assets, save state and the project-specific queue',()=>{
  const p=project([chapter(1)]);
  assert.ok(nextAction(p,{},false).connect);
  assert.match(nextAction(p,{jobs:[{project:'other',status:'RUNNING'}]},true).label,/generate/);
  assert.match(nextAction(p,{paused:true,jobs:[{project:'p1',status:'RUNNING'}]},true).label,/Resume/);
  p.render={path:'final.mp4'};assert.equal(nextAction(p,{},true).tab,'timeline');
  p.renderStale=true;assert.match(nextAction(p,{},true).label,/generate/);
});
test('problems distinguish practical warnings from blocking findings and ignore obsolete failed attempts',()=>{
  const p=project([chapter(1,{scenes:[{shots:[{id:'minor',qc:{pass:false},qcDecision:{blocking:false}},
    {id:'major',qc:{issues:['Wrong person']},qcDecision:{blocking:true}}]}]})]);
  const q={jobs:[{project:'p1',chapter:'c1',id:'new',status:'COMPLETE'},{project:'p1',chapter:'c1',id:'old',status:'FAILED'}]};
  assert.deepEqual(problemList(p,q).map(x=>x.key),['major']);
  assert.equal(problemList(p,{jobs:[{project:'other',chapter:'c1',status:'FAILED'}]}).length,1);
});
test('chapter search, range paging and phase derive from media rather than optimistic status labels',()=>{
  const chapters=Array.from({length:180},(_,n)=>chapter(n+1));chapters[10].render={path:'v.mp4'};
  assert.equal(filterChapters(chapters,'','rendered').length,1);
  assert.equal(filterChapters(chapters,'','unfinished').length,179);
  assert.equal(filterChapters(chapters,'sword').length,180);
  assert.equal(chapterStage(chapter(1,{status:'COMPLETE'})),'Needs analysis');
  const p=project(chapters),tools=createWorkspaceTools({get:()=>({project:p,chapterId:'c1'})});
  const html=tools.railHTML();assert.equal((html.match(/data-ws-chapter=/g)||[]).length,20);
  assert.match(html,/180/);assert.match(html,/Full video still includes every chapter/);
});
test('impact never invents cost or treats unknown work as zero; only explicit estimates get numbers',()=>{
  assert.match(impact('narration').detail,/entire chapter/);
  assert.match(impact('image').cost,/not calibrated/);
  assert.match(impact('reminders').detail,/reused/);
  assert.match(impact('settings').detail,/Spending caps/);
  assert.equal(impact('image',{seconds:90,cost:.04}).time,'2 min');
});
test('file evidence separates missing, pending, helper and cloud-only without promising safe deletion',()=>{
  assert.equal(fileLocation({storageStatus:'MISSING'})[0],'Missing');
  assert.match(fileLocation({storageStatus:'PENDING_UPLOAD'})[1],/Keep/);
  assert.equal(fileLocation({cloud:true,local:false})[0],'Cloud only');
  assert.equal(fileLocation({cloud:true,local:true})[0],'Cloud + helper');
  assert.match(fileLocation({cloud:true,local:true})[1],/reverify/);
  assert.match(fileLocation({cloud:false,local:true})[1],/Do not remove/);
});
test('comparison indexes exclude unrelated plan history and retain image metadata formats',()=>{
  const shot={history:[{plan:{}},{imagePath:'a.png',metadata:{seed:1}},{imagePath:'b.png',imageMetadata:{seed:2}}]};
  assert.equal(versionsOf({},shot).length,2);
  assert.equal(versionsOf({audioHistory:[{path:'a.wav'},{error:'old'}]}).length,1);
});
