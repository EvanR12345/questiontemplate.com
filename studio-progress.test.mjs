import test from 'node:test';
import assert from 'node:assert/strict';
import {createProgressReader} from './studio-progress.mjs';
test('80 chapter progress is computed once between revisions without losing counts',()=>{
 const project={revision:1,chapters:Array.from({length:80},()=>({scenes:[{shots:Array.from({length:100},(_,i)=>({imagePath:i<90?'image.png':'',generationError:i>=95?'failed':'',blocked:i===0}))}]}))};
 let checks=0;const reader=createProgressReader(s=>{checks++;return {blocking:s.blocked};});
 const first=reader.read(project);
 assert.deepEqual(first,{imageTotal:8000,imageDone:7200,blocking:80,failures:480});
 for(let i=0;i<100;i++)assert.equal(reader.read(project),first);
 assert.equal(checks,8000);
 project.revision++;project.chapters[0].scenes[0].shots[99].generationError='';
 assert.equal(reader.read(project).failures,479);assert.equal(checks,16000);
 reader.clear();reader.read(project);assert.equal(checks,24000);
});
