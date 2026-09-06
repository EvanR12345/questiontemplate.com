import Cube from 'cubejs';
import {FACE_KEYS,centerDelta,findCenterCorrection,mod4,permutationParity,simplifyMoves,validateCubies} from './supercube.ts';

export type JointResult={moves:string[];optimal:boolean;searchedDepth:number;candidates:number};
const MOVES=FACE_KEYS.flatMap(f=>[f,f+'2',f+"'"]);
const OPPOSITE=[3,4,5,0,1,2];
const inverse=(m:string)=>m.endsWith('2')?m:m.endsWith("'")?m[0]:m+"'";
const unpack=(table:number[],i:number)=>(table[i>>3]>>>((i%8)*4))&15;

// The goal includes BOTH cubie state and all six center rotations. Iterative
// deepening proves optimality only when it completes all shorter depths.
export function solveJoint(facelets:string,target:number[],seed:string[],options:{maxNodes?:number;searchMs?:number;candidateLimit?:number;onProgress?:(message:string)=>void}={}):JointResult{
 const start=Cube.fromString(facelets);
 if(start.asString()!==facelets)throw new Error('Some tiles do not form real cube pieces.');
 const error=validateCubies(start.toJSON());if(error)throw new Error(error);
 if(target.length!==6||target.some(n=>!Number.isInteger(n)||n<0||n>3))throw new Error('Check all six center rotations.');
 if(target.reduce((a,b)=>a+b,0)%2!==permutationParity(start.toJSON().cp))throw new Error('These middle rotations are not reachable for these pieces. Recheck the six previews.');
 Cube.initSolver();
 let best:string[]=Array(999).fill(''),candidates=0;
 function consider(pieceMoves:string[]){
  if(!Cube.fromString(facelets).move(pieceMoves.join(' ')).isSolved())return;
  const delta=centerDelta(pieceMoves),fix=findCenterCorrection(target.map((n,i)=>mod4(n-delta[i])));
  if(!fix)return;
  const moves=simplifyMoves([...pieceMoves,...fix]);candidates++;
  if(!best||moves.length<best.length)best=moves;
 }
 consider(seed);
 if(!candidates)throw new Error('The initial picture solution could not be verified.');
 const working=Cube.fromString(facelets),remaining=[...target],path:string[]=[];
 let nodes=0,aborted=false,searchedDepth=-1;
 const deadline=Date.now()+(options.searchMs??4500),maxNodes=options.maxNodes??800000;
 function lowerBound(){
  const state=working.toJSON();
  const corners=state.cp.filter((v,i)=>v!==i||state.co[i]!==0).length;
  const edges=state.ep.filter((v,i)=>v!==i||state.eo[i]!==0).length;
  const slice=Math.floor(working.FRtoBR()/24);
  return Math.max(Math.ceil(corners/4),Math.ceil(edges/4),remaining.filter(Boolean).length,
   unpack(Cube.pruningTables.sliceTwist,working.twist()*495+slice),
   unpack(Cube.pruningTables.sliceFlip,working.flip()*495+slice));
 }
 function search(left:number,last:number):boolean{
  if(++nodes>maxNodes||((nodes&1023)===0&&Date.now()>deadline)){aborted=true;return false;}
  const bound=lowerBound();if(bound>left)return false;
  if(bound===0)return true;
  for(let face=0;face<6;face++){
   if(face===last||(last>=0&&face===OPPOSITE[last]&&face<last))continue;
   for(let power=1;power<=3;power++){
    const move=MOVES[face*3+power-1];working.move(move);remaining[face]=mod4(remaining[face]-power);path.push(move);
    if(search(left-1,face))return true;
    path.pop();remaining[face]=mod4(remaining[face]+power);working.move(inverse(move));
    if(aborted)return false;
   }
  }return false;
 }
 options.onProgress?.('Searching pieces and middle rotations together…');
 for(let depth=lowerBound();depth<=Math.min(best.length,12);depth++){
  if(search(depth,-1))return {moves:[...path],optimal:true,searchedDepth:depth,candidates};
  if(aborted)break;searchedDepth=depth;
 }
 // Evaluate alternate piece paths by the COMPLETE supercube move count, not
 // by piece count alone. This is a bounded fallback, never an optimality claim.
 const candidateDeadline=Date.now()+10000;
 for(const prefix of MOVES.slice(0,options.candidateLimit??12)){
  if(Date.now()>candidateDeadline)break;
  const cube=Cube.fromString(facelets).move(prefix),result=cube.solve().trim();
  consider([prefix,...(result?result.split(/\s+/):[])]);
  options.onProgress?.(`Comparing complete solutions · best ${best!.length} moves`);
 }
 const moves=best!;
 if(!Cube.fromString(facelets).move(moves.join(' ')).isSolved()||centerDelta(moves).some((v,i)=>v!==target[i]))throw new Error('The complete picture solution failed verification.');
 return {moves,optimal:searchedDepth>=moves.length-1,searchedDepth,candidates};
}
