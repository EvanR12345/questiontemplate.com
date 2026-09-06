import assert from "node:assert/strict";
import test from "node:test";
import Cube from "cubejs";
import { FACE_KEYS, CENTER_GENERATORS, initialPictures, movePictures, applyPictureMoves, inverseMoves, centerDelta, findCenterCorrection, validateCubies } from "../app/supercube.ts";
const initial=initialPictures();
const stringify=p=>FACE_KEYS.flatMap(f=>p[f].map(t=>t.sourceFace)).join("");
test("all 18 face moves agree with the independent cubie solver and undo all picture rotations",()=>{
 for(const face of FACE_KEYS)for(const suffix of ["","'","2"]){
  const move=face+suffix;
  assert.equal(stringify(movePictures(initial,move)),new Cube().move(move).asString(),move);
  assert.deepEqual(applyPictureMoves(initial,[move,...inverseMoves([move])]),initial,move);
  assert.deepEqual(applyPictureMoves(initial,Array(4).fill(face)),initial);
 }
});
test("every center generator preserves all outside picture tiles, including their rotations",()=>{
 for(const algorithm of CENTER_GENERATORS){
  const moves=algorithm.split(" "),state=applyPictureMoves(initial,moves),delta=centerDelta(moves);
  assert.equal(new Cube().move(algorithm).isSolved(),true);
  for(const [i,f] of FACE_KEYS.entries())for(let j=0;j<9;j++){
   if(j===4)assert.equal(state[f][j].turns,delta[i]);else assert.deepEqual(state[f][j],initial[f][j]);
  }
 }
});
test("center generators span exactly the 2,048 legal rotation states",()=>{
 const vectors=CENTER_GENERATORS.map(a=>centerDelta(a.split(" ")));
 const seen=new Set(["000000"]),queue=[[0,0,0,0,0,0]];
 for(let i=0;i<queue.length;i++)for(const v of vectors){const state=queue[i].map((n,j)=>(n+v[j])%4),key=state.join("");if(!seen.has(key)){seen.add(key);queue.push(state);}}
 assert.equal(seen.size,2048);
 for(const key of seen)assert.equal([...key].reduce((s,n)=>s+Number(n),0)%2,0);
});
test("center-only solution handles all six 180° turns and both directions of quarter-turn pairs",()=>{
 for(let i=0;i<6;i++)for(let j=0;j<6;j++)for(const direction of [1,3]){
  const target=[0,0,0,0,0,0];target[i]=(target[i]+1)%4;target[j]=(target[j]+direction)%4;
  const moves=findCenterCorrection(target);assert.notEqual(moves,null);
  assert.deepEqual(centerDelta(moves),target);
  assert.equal(new Cube().move(moves.join(" ")).isSolved(),true);
  const pictures=applyPictureMoves(initial,moves);
  for(const f of FACE_KEYS)for(let tile=0;tile<9;tile++)if(tile!==4)assert.deepEqual(pictures[f][tile],initial[f][tile]);
 }
 assert.equal(findCenterCorrection([1,0,0,0,0,0]),null);
 assert.deepEqual(findCenterCorrection([0,0,0,0,0,0]),[]);
});
test("30 seeded full scrambles solve every picture fragment and all six center orientations",()=>{
 Cube.initSolver();let seed=20260906;
 const random=()=>{seed=(1664525*seed+1013904223)>>>0;return seed/2**32;};
 for(let sample=0;sample<30;sample++){
  const scramble=Array.from({length:22},()=>FACE_KEYS[Math.floor(random()*6)]+["","'","2"][Math.floor(random()*3)]);
  const cube=new Cube().move(scramble.join(" "));assert.equal(validateCubies(cube.toJSON()),null);
  const first=cube.solve().trim().split(/\s+/).filter(Boolean);
  const partlySolved=applyPictureMoves(initial,[...scramble,...first]);
  const remaining=FACE_KEYS.map(f=>(4-partlySolved[f][4].turns)%4);
  const centers=findCenterCorrection(remaining);assert.notEqual(centers,null);
  assert.deepEqual(applyPictureMoves(partlySolved,centers),initial);
 }
});
