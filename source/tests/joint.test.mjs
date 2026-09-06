import test from 'node:test';
import assert from 'node:assert/strict';
import Cube from 'cubejs';
import {solveJoint} from '../app/joint-solver.ts';
import {initialPictures,applyPictureMoves,centerDelta,mod4} from '../app/supercube.ts';
Cube.initSolver();
test('joint search restores pieces and center orientation and proves short solutions',()=>{
 for(const scramble of ["",'R',"R U F2 L'",'R U R\' U\' F2']){
  const moves=scramble?scramble.split(' '):[],cube=new Cube().move(scramble);
  const target=centerDelta(moves).map(n=>mod4(-n));
  const seed=cube.isSolved()?[]:cube.solve().trim().split(/\s+/);
  const result=solveJoint(cube.asString(),target,seed,{searchMs:10000,maxNodes:2000000,candidateLimit:0});
  assert.equal(result.optimal,true,scramble);
  assert.ok(result.moves.length<=moves.length);
  assert.deepEqual(applyPictureMoves(initialPictures(),[...moves,...result.moves]),initialPictures());
 }
});
test('bounded fallback validates every center and does not falsely claim optimality',()=>{
 const scramble="R U F2 L B D R2 F U2 B' D2 L2".split(' ');
 const cube=new Cube().move(scramble.join(' ')),target=centerDelta(scramble).map(n=>mod4(-n));
 const result=solveJoint(cube.asString(),target,cube.solve().trim().split(/\s+/),{maxNodes:1,candidateLimit:3});
 assert.equal(result.optimal,false);
 assert.deepEqual(applyPictureMoves(initialPictures(),[...scramble,...result.moves]),initialPictures());
 assert.throws(()=>solveJoint(new Cube().asString(),[1,0,0,0,0,0],[]),/not reachable/);
});
