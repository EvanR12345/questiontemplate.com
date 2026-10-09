import test from 'node:test';
import assert from 'node:assert/strict';
import {checkpointProject,backupSnapshot,canApplyBackgroundProject} from './studio-project-safety.mjs';
const project=()=>({id:'one',revision:3,chapters:[{id:'ch1',name:'Chapter',sourceText:'saved',cleanNarrationText:'saved narration',manual:{camera:true},scenes:[{shots:[{imagePath:'art.png'}]}]}]});
test('optional browser backup failure cannot pretend a successful helper save failed',async()=>{
  const warnings=[];
  await checkpointProject(project(),{connected:true,cloudEnabled:false,save:async()=>{throw Error('quota');},remember:()=>{},warn:message=>warnings.push(message)});
  assert.match(warnings[0],/saved to the helper/);
});
test('offline or unsynced edits remain a real save error',async()=>{
  for(const connected of [true,false])await assert.rejects(checkpointProject({...project(),_unsynced:true},
    {connected,cloudEnabled:true,save:async()=>{throw Error('quota');},remember:()=>{}}),/Export project/);
});
test('unavailable last-selection storage cannot prevent saving actual project data',async()=>{
  let saved=0;const warnings=[];
  await checkpointProject(project(),{connected:false,save:async()=>{saved++;},remember:()=>{throw Error('blocked');},warn:message=>warnings.push(message)});
  assert.equal(saved,1);assert.match(warnings[0],/data is saved/);
});
test('cloud-connected project skips full browser data copies',async()=>{
  let saved=0;
  await checkpointProject(project(),{connected:true,cloudEnabled:true,save:async()=>{saved++;},remember:()=>{}});
  assert.equal(saved,0);
});
test('emergency export includes latest editor text without touching canonical assets',()=>{
  const original=project(),before=structuredClone(original);
  const backup=backupSnapshot(original,'ch1','write',{sourceText:'unsaved latest text',name:'Updated'});
  assert.equal(backup.chapters[0].sourceText,'unsaved latest text');assert.equal(backup.chapters[0].name,'Updated');
  assert.deepEqual(backup.chapters[0].scenes,original.chapters[0].scenes);assert.deepEqual(original,before);
});
test('manual narration and intentional heading choices survive emergency export',()=>{
  const backup=backupSnapshot(project(),'ch1','narration',{cleanNarrationText:'manual words',narrationMode:'manual',includeChapterLabel:false});
  assert.equal(backup.chapters[0].cleanNarrationText,'manual words');assert.equal(backup.chapters[0].narrationMode,'manual');
  assert.equal(backup.chapters[0].includeChapterLabel,false);assert.equal(backup.chapters[0].manual.camera,true);
});
test('late replies cannot replace another selected project or newer edits',()=>{
  const current=project(),latest={...project(),revision:4};
  assert.equal(canApplyBackgroundProject(current,latest,{requestedId:'other'}),false);
  assert.equal(canApplyBackgroundProject(current,latest,{requestedId:'one',dirty:true}),false);
  assert.equal(canApplyBackgroundProject(current,latest,{requestedId:'one',editing:true}),false);
  assert.equal(canApplyBackgroundProject({...current,_unsynced:true},latest,{requestedId:'one'}),false);
  assert.equal(canApplyBackgroundProject(current,{...latest,revision:2},{requestedId:'one'}),false);
  assert.equal(canApplyBackgroundProject(current,latest,{requestedId:'one'}),true);
});
