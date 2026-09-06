import test from 'node:test';
import assert from 'node:assert/strict';
import Cube from 'cubejs';
import {FACE_KEYS,initialPictures,applyPictureMoves,centerAlgorithms,centerDelta,findCenterCorrection,legacyCenterCorrection} from '../app/supercube.ts';

test('every rotated algorithm preserves all outside picture tiles',()=>{
 const initial=initialPictures();
 for(const moves of centerAlgorithms()) {
  const state=applyPictureMoves(initial,moves);
  for(const f of FACE_KEYS)for(let i=0;i<9;i++)if(i!==4)assert.deepEqual(state[f][i],initial[f][i]);
 }
});
test('all 2048 center states remain valid and never exceed previous move counts',()=>{
 let count=0,before=0,after=0,improved=0;
 for(let key=0;key<4096;key++) {
  const target=FACE_KEYS.map((_,i)=>(key>>(i*2))&3);
  const next=findCenterCorrection(target);
  if(target.reduce((a,b)=>a+b,0)%2){assert.equal(next,null);continue;}
  const old=legacyCenterCorrection(target);
  assert.ok(next);assert.ok(old);
  assert.ok(next.length<=old.length,`${key}: ${next.length} > ${old.length}`);
  assert.deepEqual(centerDelta(next),target);
  assert.ok(new Cube().move(next.join(' ')).isSolved());
  count++;before+=old.length;after+=next.length;if(next.length<old.length)improved++;
 }
 assert.equal(count,2048);
 console.log(JSON.stringify({states:count,improved,oldAverage:before/count,newAverage:after/count,reduction:1-after/before}));
 for(let i=0;i<6;i++)assert.ok(findCenterCorrection(FACE_KEYS.map((_,j)=>i===j?2:0)).length<=12);
});
