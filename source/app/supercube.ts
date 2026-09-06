export const FACE_KEYS = ["U", "R", "F", "D", "L", "B"] as const;
export type FaceKey = typeof FACE_KEYS[number];
export type Vec = [number, number, number];
export type PictureTile = { sourceFace: FaceKey; sourceIndex: number; turns: number };
export type PictureState = Record<FaceKey, PictureTile[]>;
export const BASIS: Record<FaceKey, { normal: Vec; up: Vec; right: Vec }> = {
  U: { normal: [0,1,0], up: [0,0,-1], right: [1,0,0] },
  R: { normal: [1,0,0], up: [0,1,0], right: [0,0,-1] },
  F: { normal: [0,0,1], up: [0,1,0], right: [1,0,0] },
  D: { normal: [0,-1,0], up: [0,0,1], right: [1,0,0] },
  L: { normal: [-1,0,0], up: [0,1,0], right: [0,0,1] },
  B: { normal: [0,0,-1], up: [0,1,0], right: [-1,0,0] },
};
export const mod4 = (n: number) => ((n % 4) + 4) % 4;
export const dot = (a: Vec, b: Vec) => a.reduce((s, n, i) => s + n*b[i], 0);
const neg = (v: Vec) => v.map(n => -n) as Vec;
export function stickerPosition(face: FaceKey, index: number): Vec {
  const {normal, up, right} = BASIS[face];
  return normal.map((n,i) => n + right[i]*(index%3-1) + up[i]*(1-Math.floor(index/3))) as Vec;
}
function clockwise(v: Vec, axis: Vec): Vec {
  const cross = [v[1]*axis[2]-v[2]*axis[1],v[2]*axis[0]-v[0]*axis[2],v[0]*axis[1]-v[1]*axis[0]];
  return cross.map((n,i) => n + axis[i]*dot(v,axis)) as Vec;
}
export function initialPictures(): PictureState {
  return Object.fromEntries(FACE_KEYS.map(face => [face, Array.from({length:9},(_,index) => ({sourceFace:face,sourceIndex:index,turns:0}))])) as PictureState;
}
export function movePictures(state: PictureState, move: string): PictureState {
  if (!/^[URFDLB](2|')?$/.test(move)) throw new Error(`Invalid move: ${move}`);
  const axis = BASIS[move[0] as FaceKey].normal;
  const count = move.endsWith("2") ? 2 : move.endsWith("'") ? 3 : 1;
  const result = initialPictures();
  for (const face of FACE_KEYS) for (let index=0;index<9;index++) {
    const tile = state[face][index];
    let pos = stickerPosition(face,index);
    let normal = BASIS[face].normal;
    let up = [BASIS[face].up,BASIS[face].right,neg(BASIS[face].up),neg(BASIS[face].right)][tile.turns];
    if (dot(pos,axis) === 1) for(let turn=0;turn<count;turn++) {
      pos = clockwise(pos,axis); normal = clockwise(normal,axis); up = clockwise(up,axis);
    }
    const destination = FACE_KEYS.find(f => dot(BASIS[f].normal,normal) === 1)!;
    const basis = BASIS[destination];
    const newIndex = (1-dot(pos,basis.up))*3 + dot(pos,basis.right)+1;
    const turns = [basis.up,basis.right,neg(basis.up),neg(basis.right)].findIndex(v => dot(v,up) === 1);
    result[destination][newIndex] = {...tile, turns};
  }
  return result;
}
export function applyPictureMoves(state: PictureState, moves: string[]): PictureState {
  return moves.reduce(movePictures,state);
}
export function inverseMoves(moves: string[]) {
  return [...moves].reverse().map(m => m.endsWith("2") ? m : m.endsWith("'") ? m[0] : `${m}'`);
}
export function centerDelta(moves: string[]) {
  const result = [0,0,0,0,0,0];
  for(const m of moves) {
    const i=FACE_KEYS.indexOf(m[0] as FaceKey);
    result[i]=mod4(result[i]+(m.endsWith("2")?2:m.endsWith("'")?3:1));
  }
  return result;
}
// Each generator leaves every edge and corner in its original position/orientation.
// Together their center vectors span all 2,048 physically reachable center states.
export const CENTER_GENERATORS = [
  "R2 F' B' U D B2 R2 F' B' U D B2 R2 F' B' U D B2",
  "L D2 R2 U R2 D2 L D2 R2 U R2 D2 L D2 R2 U R2 D2",
  "U' R' B F L' D' U' R' B F L' D' U' R' B F L' D'",
  "F D2 L2 B' L2 D2 F D2 L2 B' L2 D2 F D2 L2 B' L2 D2",
  "B D R2 B D R2 B D R2 B D R2 B D R2 B D R2 B D R2 B D R2 B D R2",
  "L R F2 R' L' F' L R F2 R' L' F'",
];
export function legacyCenterCorrection(target: number[]): string[] | null {
  if(target.length!==6 || target.some(n=> !Number.isInteger(n)||n<0||n>3)) return null;
  const generators=CENTER_GENERATORS.flatMap(a=>[a.split(" "),inverseMoves(a.split(" "))]);
  const vectors=generators.map(centerDelta);
  const encode=(v:number[])=>v.reduce((s,n,i)=>s+n*(4**i),0);
  const end=encode(target);
  const previous=new Int16Array(4096).fill(-1);
  const via=new Int8Array(4096).fill(-1);
  const queue=[0]; previous[0]=0;
  for(let cursor=0;cursor<queue.length && previous[end]===-1;cursor++) {
    const key=queue[cursor];
    const state=FACE_KEYS.map((_,i)=>Math.floor(key/(4**i))%4);
    for(let g=0;g<generators.length;g++) {
      const next=encode(state.map((n,i)=>mod4(n+vectors[g][i])));
      if(previous[next]!==-1) continue;
      previous[next]=key; via[next]=g; queue.push(next);
    }
  }
  if(previous[end]===-1) return null;
  const path:number[]=[];
  for(let key=end;key!==0;key=previous[key]) path.push(via[key]);
  // Combine adjacent same-face moves without changing their center effect.
  const result:string[]=[];
  for(const move of path.reverse().flatMap(g=>generators[g])) {
    const last=result.at(-1);
    if(last?.[0]!==move[0]) { result.push(move); continue; }
    const turns=(m:string)=>m.endsWith("2")?2:m.endsWith("'")?3:1;
    const n=mod4(turns(result.pop()!)+turns(move));
    if(n) result.push(move[0]+(n===2?"2":n===3?"'":""));
  }
  return result;
}
// Proper cube rotations relabel algorithms without changing turn handedness.
export function centerAlgorithms(): string[][] {
  const best = new Map<string,string[]>();
  for (const front of FACE_KEYS) for (const top of FACE_KEYS) {
    const z=BASIS[front].normal,y=BASIS[top].normal;
    if(dot(z,y)) continue;
    const x:Vec=[y[1]*z[2]-y[2]*z[1],y[2]*z[0]-y[0]*z[2],y[0]*z[1]-y[1]*z[0]];
    const mapping=Object.fromEntries(FACE_KEYS.map(f=>{
      const v=BASIS[f].normal;
      const rotated=x.map((n,i)=>n*v[0]+y[i]*v[1]+z[i]*v[2]) as Vec;
      return [f,FACE_KEYS.find(k=>dot(BASIS[k].normal,rotated)===1)!];
    }));
    for(const algorithm of CENTER_GENERATORS) {
      const moves=algorithm.split(" ").map(m=>mapping[m[0]]+m.slice(1));
      for(const candidate of [moves,inverseMoves(moves)]) {
        const key=centerDelta(candidate).join("");
        if(!best.has(key)||best.get(key)!.length>candidate.length) best.set(key,candidate);
      }
    }
  }
  return [...best.values()];
}
export function simplifyMoves(moves:string[]):string[] {
  const result:string[]=[];
  const opposite:Record<string,string>={U:"D",D:"U",R:"L",L:"R",F:"B",B:"F"};
  const turns=(m:string)=>m.endsWith("2")?2:m.endsWith("'")?3:1;
  for(const move of moves) {
    let i=result.length-1;
    while(i>=0&&result[i][0]===opposite[move[0]]) i--;
    if(i>=0&&result[i][0]===move[0]) {
      const n=mod4(turns(result[i])+turns(move));
      if(n) result[i]=move[0]+(n===2?"2":n===3?"'":"");
      else result.splice(i,1);
    } else result.push(move);
  }
  return result;
}
let centerTable:{algorithms:string[][];previous:Int16Array;via:Int16Array}|undefined;
export function findCenterCorrection(target:number[]):string[]|null {
  if(target.length!==6||target.some(n=>!Number.isInteger(n)||n<0||n>3)) return null;
  const encode=(v:number[])=>v.reduce((s,n,i)=>s+n*4**i,0);
  if(!centerTable) {
    const algorithms=centerAlgorithms(),vectors=algorithms.map(centerDelta);
    const previous=new Int16Array(4096).fill(-1),via=new Int16Array(4096).fill(-1);
    const distance=new Float64Array(4096).fill(Infinity),visited=new Uint8Array(4096);
    distance[0]=0;previous[0]=0;
    // Weighted shortest paths: a half turn counts as one move (HTM).
    for(let step=0;step<2048;step++) {
      let key=-1,best=Infinity;
      for(let i=0;i<4096;i++)if(!visited[i]&&distance[i]<best){key=i;best=distance[i];}
      if(key<0) break;
      visited[key]=1;
      const state=FACE_KEYS.map((_,i)=>(key>>(2*i))&3);
      for(let g=0;g<algorithms.length;g++) {
        const next=encode(state.map((n,i)=>mod4(n+vectors[g][i]))),cost=best+algorithms[g].length;
        if(cost<distance[next]){distance[next]=cost;previous[next]=key;via[next]=g;}
      }
    }
    centerTable={algorithms,previous,via};
  }
  const {algorithms,previous,via}=centerTable,end=encode(target);
  if(previous[end]===-1)return null;
  const path:number[]=[];
  for(let key=end;key!==0;key=previous[key])path.push(via[key]);
  const candidate=simplifyMoves(path.reverse().flatMap(g=>algorithms[g]));
  // Retain the old candidate when its boundary cancellations happen to win.
  const fallback=simplifyMoves(legacyCenterCorrection(target)!);
  return candidate.length<=fallback.length?candidate:fallback;
}
export function permutationParity(values: number[]) {
  let inversions=0;
  for(let i=0;i<values.length;i++) for(let j=i+1;j<values.length;j++) if(values[i]>values[j]) inversions++;
  return inversions%2;
}
export function validateCubies(state: {cp:number[];co:number[];ep:number[];eo:number[]}) {
  const {cp,co,ep,eo}=state;
  if(new Set(cp).size!==8 || cp.some(n=>!Number.isInteger(n)||n<0||n>7) || new Set(ep).size!==12 || ep.some(n=>!Number.isInteger(n)||n<0||n>11))
    return "Some picture tiles are matched to the wrong face. Check the corner and edge tiles in Review tiles.";
  if(co.some(n=>!Number.isInteger(n)||n<0||n>2)||co.reduce((s,n)=>s+n,0)%3) return "A corner appears twisted. Check the photos’ holding direction and corner matches.";
  if(eo.some(n=>!Number.isInteger(n)||n<0||n>1)||eo.reduce((s,n)=>s+n,0)%2) return "An edge appears flipped. Check your photo rotation and edge matches.";
  if(permutationParity(cp)!==permutationParity(ep)) return "Two pieces appear swapped. Check your photo order and tile matches.";
  return null;
}
