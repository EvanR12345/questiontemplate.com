"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Camera, Check, ChevronLeft, ChevronRight, Pause, Play, RotateCcw, RotateCw, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { CubeCanvas, type CubeFaces, type FaceImages } from "./cube-canvas";
import { PictureFace, FACE_HEX } from "./picture-face";
import { PhotoScanner, rgbToHex, type PictureScan, type RGB } from "./photo-scanner";
import { FACE_KEYS, applyPictureMoves, centerDelta, findCenterCorrection, initialPictures, inverseMoves, mod4, movePictures, validateCubies, type FaceKey, type PictureState } from "./supercube";

const NAMES:Record<FaceKey,string>={U:"Top",R:"Right",F:"Front",D:"Bottom",L:"Left",B:"Back"};
const IDS:Record<FaceKey,string>={U:"white",R:"red",F:"green",D:"yellow",L:"orange",B:"blue"};
const SCAN_ORDER:FaceKey[]=["F","R","B","L","U","D"];
const SOLVED=FACE_KEYS.map(f=>f.repeat(9)).join("");
const ZERO=()=>Object.fromEntries(FACE_KEYS.map(f=>[f,0])) as Record<FaceKey,number>;
const FLAGS=()=>Object.fromEntries(FACE_KEYS.map(f=>[f,false])) as Record<FaceKey,boolean>;
function blankFaces(solved=false):CubeFaces{return Object.fromEntries(FACE_KEYS.map(f=>[f,Array.from({length:9},(_,i)=>solved||i===4?IDS[f]:null)])) as CubeFaces;}
function colorsAt(pictures:PictureState,original:CubeFaces):CubeFaces{return Object.fromEntries(FACE_KEYS.map(f=>[f,pictures[f].map(t=>original[t.sourceFace][t.sourceIndex])])) as CubeFaces;}
function distance(samples:RGB[],tone:RGB){
 if(!samples.length)return Infinity;
 const values=samples.map(v=>Math.hypot(...v.map((n,i)=>n-tone[i]))).sort((a,b)=>a-b);
 const count=Math.min(values.length,Math.max(3,Math.ceil(values.length*.2)));
 return values.slice(0,count).reduce((s,n)=>s+n,0)/count;
}
function classify(scans:Partial<Record<FaceKey,PictureScan>>):CubeFaces{
 const result=blankFaces();const refs=FACE_KEYS.filter(f=>scans[f]);
 for(const face of refs) result[face]=scans[face]!.tileSamples.map((samples,i)=>{
  if(i===4)return IDS[face];
  if(refs.length!==6)return null;
  const ranked=refs.map(f=>({f,d:distance(samples,scans[f]!.faceTone)})).sort((a,b)=>a.d-b.d);
  // Leave uncertain or empty samples blank instead of silently inventing a match.
  return Number.isFinite(ranked[0].d)&&ranked[1].d-ranked[0].d>=6 ? IDS[ranked[0].f] : null;
 });
 return result;
}
type Plan={kind:"pieces"|"centers";moves:string[];snapshots:PictureState[];original:CubeFaces;images:FaceImages};
function makePlan(kind:Plan["kind"],moves:string[],base:PictureState,original:CubeFaces,images:FaceImages):Plan{
 const snapshots=[base];for(const m of moves)snapshots.push(movePictures(snapshots.at(-1)!,m));
 return {kind,moves,snapshots,original,images};
}
export function CubeSolver(){
 const [mode,setMode]=useState<"centers"|"scrambled">("centers");
 const [stage,setStage]=useState<"entry"|"play"|"align"|"done">("entry");
 const [entryMode,setEntryMode]=useState("photo"),[active,setActive]=useState<FaceKey>("F");
 const [faces,setFaces]=useState<CubeFaces>(()=>blankFaces(true));
 const [scans,setScans]=useState<Partial<Record<FaceKey,PictureScan>>>({});
 const [paint,setPaint]=useState<FaceKey>("F"),[reviewed,setReviewed]=useState(FLAGS);
 const [message,setMessage]=useState(""),[solving,setSolving]=useState(false);
 const [plan,setPlan]=useState<Plan|null>(null),[index,setIndex]=useState(0),[playing,setPlaying]=useState(false);
 const [base,setBase]=useState<PictureState>(initialPictures);
 const [baseFaces,setBaseFaces]=useState<CubeFaces>(()=>blankFaces(true));
 const [baseImages,setBaseImages]=useState<FaceImages>({});
 const [corrections,setCorrections]=useState(ZERO),[checked,setChecked]=useState(FLAGS);
 const [transition,setTransition]=useState<{id:number;move:string}|null>(null);
 const transitionId=useRef(0),worker=useRef<Worker|null>(null),timeout=useRef<ReturnType<typeof setTimeout>|null>(null);
 const solverRun=useRef(0);
 const initial=useMemo(initialPictures,[]);
 const images=useMemo(()=>Object.fromEntries(FACE_KEYS.filter(f=>scans[f]).map(f=>[f,scans[f]!.preview])),[scans]);
 const filled=FACE_KEYS.reduce((s,f)=>s+faces[f].filter(Boolean).length,0);
 const photoCount=Object.keys(images).length;
 const counts=FACE_KEYS.map(f=>FACE_KEYS.reduce((s,p)=>s+faces[p].filter(n=>n===IDS[f]).length,0));
 const pictureState=stage==="entry"?initial:stage==="play"&&plan?plan.snapshots[index]:base;
 const original=stage==="entry"?faces:stage==="play"&&plan?plan.original:baseFaces;
 const displayFaces=useMemo(()=>colorsAt(pictureState,original),[pictureState,original]);
 const displayImages=stage==="entry"?images:stage==="play"&&plan?plan.images:baseImages;
 const reviewCount=FACE_KEYS.filter(f=>reviewed[f]).length;
 const checkedCount=FACE_KEYS.filter(f=>checked[f]).length;
 function goTo(next:number){
  if(!plan)return;
  setTransition(Math.abs(next-index)===1?{id:++transitionId.current,move:next>index?plan.moves[index]:inverseMoves([plan.moves[next]])[0]}:null);
  setIndex(next);
 }
 useEffect(()=>{
  if(!playing||!plan||index>=plan.moves.length){if(playing)setPlaying(false);return;}
  const timer=setTimeout(()=>goTo(index+1),1500);return()=>clearTimeout(timer);
 },[playing,index,plan]);
 useEffect(()=>()=>{worker.current?.terminate();if(timeout.current)clearTimeout(timeout.current);},[]);
 function cancelSolve(){solverRun.current++;worker.current?.terminate();worker.current=null;if(timeout.current)clearTimeout(timeout.current);setSolving(false);setMessage("Cancelled. Your photos and corrections are still here.");}
 function changeMode(next:"centers"|"scrambled"){
  setMode(next);setReviewed(FLAGS());setMessage("");setFaces(next==="centers"?blankFaces(true):classify(scans));
 }
 function acceptScan(face:FaceKey,scan:PictureScan){
  const next={...scans,[face]:scan};setScans(next);setReviewed(FLAGS());setMessage("");
  setFaces(mode==="centers"?blankFaces(true):classify(next));
 }
 function setTone(face:FaceKey,tone:RGB){
  const scan=scans[face];if(scan)acceptScan(face,{...scan,faceTone:tone});
 }
 function beginAlignment(pictures:PictureState,originalFaces:CubeFaces,sourceImages:FaceImages){
  setBase(pictures);setBaseFaces(originalFaces);setBaseImages(sourceImages);setCorrections(ZERO());setChecked(FLAGS());setActive("F");setPlaying(false);setTransition(null);setStage("align");setMessage("");
 }
 async function solvePieces(){
  setMessage("");
  if(filled!==54){setEntryMode("manual");setMessage("Some tiles are blank or uncertain. Choose their picture in Review tiles.");return;}
  if(counts.some(n=>n!==9)){setEntryMode("manual");setMessage("Each picture must have exactly 9 tiles. Check the counts and correct the matches.");return;}
  if(reviewCount!==6){setEntryMode("manual");setMessage("Confirm all six faces in Review tiles before solving. A matching background is only a suggestion.");return;}
  const toFace=Object.fromEntries(FACE_KEYS.map(f=>[IDS[f],f]));
  const facelets=FACE_KEYS.flatMap(f=>faces[f]).map(v=>toFace[v!]).join("");
  if(facelets===SOLVED){beginAlignment(initial,faces,images);return;}
  setSolving(true);setMessage("Finding a solution. First use can take a little longer; the page stays usable.");
  const run=++solverRun.current;
  try{
   const Cube=(await import("cubejs")).default;
   if(run!==solverRun.current)return;
   const cube=Cube.fromString(facelets);
   if(cube.asString()!==facelets)throw new Error("Some matched tiles do not form real cube pieces. Recheck the photos and tile matches.");
   const error=validateCubies(cube.toJSON());if(error)throw new Error(error);
   const w=new Worker(new URL("./solver.worker.ts",import.meta.url),{type:"module"});worker.current=w;
   timeout.current=setTimeout(()=>{w.terminate();worker.current=null;setSolving(false);setMessage("The solver took too long. Your photos are safe. Retry, or use Only centers if the outside pieces already form the pictures.");},120000);
   const finish=()=>{if(timeout.current)clearTimeout(timeout.current);w.terminate();worker.current=null;setSolving(false);};
   w.onerror=()=>{finish();setMessage("The solver could not start. Refresh this page and retry. Your photos remain here until you refresh.");};
   w.onmessage=(event:MessageEvent<{moves?:string[];error?:string}>)=>{
    finish();if(event.data.error){setMessage(event.data.error);setEntryMode("manual");return;}
    const moves=event.data.moves!;
    // Independently replay every proposed move before presenting it.
    const proposed=makePlan("pieces",moves,initial,faces,images);
    const final=colorsAt(proposed.snapshots.at(-1)!,faces);
    if(!FACE_KEYS.every(f=>final[f].every(c=>c===IDS[f]))){setMessage("The move check failed. No solution was shown.");return;}
    setPlan(proposed);setIndex(0);setTransition(null);setStage("play");setMessage("");
   };w.postMessage(facelets);
  }catch(error){setSolving(false);setMessage(error instanceof Error?error.message:"Could not read this cube.");}
 }
 async function solveCenters(){
  setMessage("");
  if(checkedCount!==6){setMessage("Check all six middle tiles, including the ones that already match.");return;}
  const target=FACE_KEYS.map(f=>corrections[f]),moves=findCenterCorrection(target);
  if(moves===null){setMessage("These rotations cannot all be corrected with legal turns. Recheck the six previews. For example, one lone 90° center turn is impossible, but one 180° turn is possible. If a center cap was physically twisted, that may explain it.");return;}
  const Cube=(await import("cubejs")).default;
  if(!new Cube().move(moves.join(" ")).isSolved()||centerDelta(moves).some((v,i)=>v!==target[i])){setMessage("The center solution did not pass verification.");return;}
  const result=makePlan("centers",moves,base,baseFaces,baseImages);
  if(!moves.length){setStage("done");return;}
  setPlan(result);setIndex(0);setTransition(null);setStage("play");
 }
 async function demo(){
  const Cube=(await import("cubejs")).default;
  const moves="R U R' U' F2 D L2 B U2 R2".split(" ");
  const scramble=applyPictureMoves(initialPictures(),moves);
  const solved=blankFaces(true);
  setMode("scrambled");setFaces(colorsAt(scramble,solved));setScans({});setReviewed(Object.fromEntries(FACE_KEYS.map(f=>[f,true])) as Record<FaceKey,boolean>);setEntryMode("manual");setMessage("Demo has no sheep photos. It demonstrates piece solving; use your photos for picture-center alignment.");
 }
 function completePhase(){
  if(!plan)return;
  if(plan.kind==="pieces")beginAlignment(plan.snapshots.at(-1)!,plan.original,plan.images);
  else {setBase(plan.snapshots.at(-1)!);setStage("done");setPlaying(false);setTransition(null);}
 }
 function turnCenter(turn:number){setCorrections(c=>({...c,[active]:mod4(c[active]+turn)}));setChecked(c=>({...c,[active]:false}));setMessage("");}
 const currentMove=plan?.moves[index];
 const matchedColors=colorsAt(base,baseFaces);
 function faceTabs(flags:Record<FaceKey,boolean>){
  return <div className="face-tabs" aria-label="Choose a face">{SCAN_ORDER.map(f=><button type="button" key={f} className={active===f?"active":""} onClick={()=>setActive(f)} aria-pressed={active===f}><span className="face-letter">{f}</span><span>{NAMES[f]}</span>{flags[f]&&<Check className="face-check"/>}</button>)}</div>;
 }
 return <main className="app-shell">
  <header className="site-header"><a className="brand" href="/">Picture Cube Solver</a><span className="orientation-pill">Supercube · centers included</span></header>
  <section className="workspace">
   <aside className="visual-panel">
    <CubeCanvas faces={displayFaces} pictures={pictureState} faceImages={displayImages} transition={transition} focus={stage==="align"?active:null}/>
    <div className="orientation-card"><div><p className="eyebrow">Keep one starting hold</p><h2>Same top. Same front.</h2></div><p>Drag the 3D view to look around. This does not change your cube’s starting hold. Every face turn is clockwise as seen looking directly at that face.</p></div>
    <div className="cube-reference"><a href="https://us.carrollsirishgifts.com/products/wacky-woolies-puzzle-cube" target="_blank" rel="noreferrer">Your sheep cube: Wacky Woollies ↗</a><p>Use photos of your cube as it is now. You do not need photos from before it was scrambled.</p></div>
   </aside>
   <section className="control-panel">
    {stage==="entry"&&<div className="entry-view"><fieldset className="entry-fields" disabled={solving}>
     <div className="panel-heading"><div><p className="eyebrow">Picture cube setup</p><h1>{mode==="centers"?"Fix rotated middles":"Assemble your pictures"}</h1></div></div>
     <div className="mode-cards"><button aria-pressed={mode==="centers"} className={mode==="centers"?"selected":""} onClick={()=>changeMode("centers")}><strong>Only my centers are wrong</strong><span>The eight outside tiles on every face are already correct.</span></button><button aria-pressed={mode==="scrambled"} className={mode==="scrambled"?"selected":""} onClick={()=>changeMode("scrambled")}><strong>My cube is scrambled</strong><span>Picture pieces are on the wrong faces or in the wrong places.</span></button></div>
     <div className="face-entry">
      <div className="entry-mode-row"><div className="section-title"><span>1</span><div><h2>{entryMode==="photo"?"Photograph all six faces":"Review every tile match"}</h2><p>{photoCount}/6 photos{mode==="scrambled"?" · "+reviewCount+"/6 faces reviewed":""}</p></div></div>
       <Tabs value={entryMode} onValueChange={setEntryMode} className="entry-mode-tabs"><TabsList><TabsTrigger value="photo"><Camera/> Photos</TabsTrigger><TabsTrigger value="manual">Review tiles</TabsTrigger></TabsList></Tabs>
      </div>
      <p className="setup-hold">Choose any picture as the front and any adjacent picture as the top. Use the holding guide for each photo. Do not try to make every sheep look upright by rotating the whole photo.</p>
      <Progress value={photoCount/6*100} className="entry-progress"/>
      {faceTabs(entryMode==="photo"?Object.fromEntries(FACE_KEYS.map(f=>[f,!!scans[f]])) as Record<FaceKey,boolean>:reviewed)}
      {entryMode==="photo"?<PhotoScanner key={active} face={active} faceName={NAMES[active]} scan={scans[active]} onScan={scan=>acceptScan(active,scan)} onFaceToneChange={tone=>setTone(active,tone)}/>:<div className="tile-review">
       <p>Pick the picture this tile belongs to, then tap the tile. The small letters show the proposed destination face, not the sheep’s color.</p>
       <PictureFace label={NAMES[active]} tiles={initial[active]} images={images} colors={faces[active]} showLabels onTile={i=>{if(i===4)return;setFaces(old=>({...old,[active]:old[active].map((v,j)=>i===j?IDS[paint]:v)}));setReviewed(old=>({...old,[active]:false}));}}/>
       <div className="picture-palette">{SCAN_ORDER.map(f=><button key={f} type="button" className={paint===f?"selected":""} aria-pressed={paint===f} onClick={()=>setPaint(f)}>{images[f]?<i className="center-sample" style={{backgroundImage:"url("+images[f]+")"}}/>:<i style={{background:scans[f]?rgbToHex(scans[f]!.faceTone):FACE_HEX[IDS[f]]}}/>}<span>{f} · {NAMES[f]}</span></button>)}</div>
       <Button variant={reviewed[active]?"outline":"default"} onClick={()=>{if(faces[active].some(v=>!v)){setMessage("Fill the blank tiles on this face first.");return;}setReviewed(r=>({...r,[active]:true}));setMessage("");setActive(SCAN_ORDER[(SCAN_ORDER.indexOf(active)+1)%6]);}}><Check/>{reviewed[active]?"Face checked":"These 9 matches are correct"}</Button>
      </div>}
      {mode==="scrambled"&&<div className="tile-counts">{FACE_KEYS.map((f,i)=><span key={f} className={counts[i]===9?"complete":"incomplete"}>{f} {counts[i]}/9</span>)}</div>}
      <div className="face-navigation"><Button variant="outline" disabled={SCAN_ORDER.indexOf(active)===0} onClick={()=>setActive(SCAN_ORDER[SCAN_ORDER.indexOf(active)-1])}><ArrowLeft/> Back</Button><Button variant="outline" disabled={SCAN_ORDER.indexOf(active)===5} onClick={()=>setActive(SCAN_ORDER[SCAN_ORDER.indexOf(active)+1])}>Next face <ArrowRight/></Button></div>
     </div>
     {mode==="centers"?<><Button className="solve-button full" onClick={()=>beginAlignment(initial,blankFaces(true),images)}>Align middle picture tiles <ArrowRight/></Button>{photoCount<6&&<p className="minor-note">Missing photos use arrows. Upload all six for full picture previews, or enter the required rotations manually.</p>}</>:<Button disabled={solving} className="solve-button full" onClick={()=>void solvePieces()}><Sparkles/>{solving?"Finding moves…":"Solve picture pieces"}</Button>}
     <button className="text-button" onClick={()=>void demo()}>Try a piece-solving demo</button>
     </fieldset>{solving&&<Button variant="outline" onClick={cancelSolve}>Cancel solving</Button>}
    </div>}
    {stage==="align"&&<div className="entry-view">
     <div className="panel-heading"><div><p className="eyebrow">Supercube · middle rotations</p><h1>Make the picture join up</h1></div></div>
     <p className="setup-hold">Rotate only the middle square in each preview until it joins the eight outside tiles. Sideways is fine if the surrounding picture is sideways. Do not turn your real cube yet.</p>
     <p className="minor-note">The left 3D cube is the current state. This preview is your target.</p>
     {faceTabs(checked)}
     <h2 className="center-face-title">{NAMES[active]} picture</h2>
     <PictureFace label={NAMES[active]+" target preview"} tiles={base[active]} images={baseImages} colors={matchedColors[active]} centerTurn={corrections[active]}/>
     {!baseImages[base[active][4].sourceFace]&&<p className="manual-center-note">No photo for this face. Looking directly at your real {NAMES[active].toLowerCase()} face, choose how far its middle tile needs to turn to match the rest of its picture.</p>}
     <div className="center-actions"><Button variant="outline" onClick={()=>turnCenter(-1)}><RotateCcw/> Left 90°</Button><Button variant="outline" onClick={()=>turnCenter(1)}><RotateCw/> Right 90°</Button></div>
     <p className="center-choice">{["No rotation needed","Clockwise 90° needed","180° needed","Counterclockwise 90° needed"][corrections[active]]}</p>
     <Button className="solve-button full" onClick={()=>{setChecked(c=>({...c,[active]:true}));setMessage("");const other=SCAN_ORDER.find(f=>f!==active&&!checked[f]);if(other)setActive(other);}}><Check/>{checked[active]?"Picture checked":"This middle matches the picture"}</Button>
     <p className="minor-note">{checkedCount} of 6 middle tiles checked</p>
     <Button variant="outline" className="solve-button full" onClick={()=>void solveCenters()}>Get center-fixing moves <ArrowRight/></Button>
     <button className="text-button" onClick={()=>{setStage("entry");setMessage("");}}>Back to photos</button>
    </div>}
    {stage==="play"&&plan&&<div className="solution-view">
     <div className="panel-heading"><div><p className="eyebrow">{plan.kind==="pieces"?"Part 1 · picture pieces":"Part 2 · middle rotations"}</p><h1>{plan.moves.length} moves</h1></div></div>
     <div className="progress-copy"><span>{index} of {plan.moves.length} completed</span><span>{Math.round(index/plan.moves.length*100)}%</span></div><Progress value={index/plan.moves.length*100}/>
     {currentMove?<div className="move-card"><span className="move-token">{currentMove}</span><div><p className="move-label">Next turn</p><h2>Turn the {NAMES[currentMove[0] as FaceKey].toLowerCase()} face {currentMove.endsWith("2")?"180°":currentMove.endsWith("'")?"counterclockwise":"clockwise"}.</h2><p>Look directly at that face. Turn the whole face, not just its center cap.</p></div></div>:<div className="complete-card"><Check/><h2>{plan.kind==="pieces"?"Outside pieces assembled. Now check the middles.":"All center-fixing moves completed."}</h2></div>}
     {plan.kind==="centers"&&<p className="setup-hold">These moves temporarily mix up the picture pieces. Finish the full sequence to restore them with the middles aligned. Never twist a center cap by hand.</p>}
     <div className="playback-controls"><Button variant="outline" aria-label="Previous move" disabled={index===0} onClick={()=>{setPlaying(false);goTo(index-1);}}><ChevronLeft/></Button><Button variant="outline" disabled={index>=plan.moves.length} onClick={()=>setPlaying(v=>!v)}>{playing?<Pause/>:<Play/>}{playing?"Pause":"Auto play"}</Button><Button aria-label="Next move" disabled={index>=plan.moves.length} onClick={()=>{setPlaying(false);goTo(index+1);}}>I did it <ChevronRight/></Button></div>
     <p className="minor-note">If you go back in the guide, undo that turn on your real cube too.</p>
     {index===plan.moves.length&&<Button className="solve-button full" onClick={completePhase}>{plan.kind==="pieces"?"Check middle rotations":"Finish"} <ArrowRight/></Button>}
     <details className="algorithm-details"><summary>See the full move sequence</summary><p>{plan.moves.join(" ")}</p></details>
     <button className="text-button" onClick={()=>{setPlaying(false);setStage("entry");setPlan(null);setMessage("If you already turned your real cube, take new photos of its current state before solving again.");}}>Start over / rescan current cube</button>
    </div>}
    {stage==="done"&&<div className="complete-card finished"><Check/><h1>Pictures aligned</h1><p>Check all six faces on your real cube. Every middle square should join its surrounding picture.</p><Button onClick={()=>{setScans({});setFaces(blankFaces(true));setMode("centers");setReviewed(FLAGS());setStage("entry");setPlan(null);setMessage("");}}>Solve another cube</Button></div>}
    {message&&<div className="status-message" role="status">{message}</div>}
   </section>
  </section>
 </main>;
}
