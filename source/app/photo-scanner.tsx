"use client";
import { useEffect, useRef, useState } from "react";
import { Camera, ImagePlus, RotateCcw, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { FaceKey } from "./supercube";
export type RGB=[number,number,number];
export type PictureScan={preview:string;faceTone:RGB;tileSamples:RGB[][]};
type Point=[number,number];
const GUIDE:Record<FaceKey,string>={
 F:"Start with your chosen front facing you and top facing up.",
 R:"Return to the starting hold. Turn the whole cube left 90°, keeping the same top up.",
 B:"Return to the starting hold. Turn the whole cube 180°, keeping the same top up.",
 L:"Return to the starting hold. Turn the whole cube right 90°, keeping the same top up.",
 U:"Return to the starting hold. Tip the top toward the camera. The original FRONT edge belongs at the BOTTOM of this photo.",
 D:"Return to the starting hold. Lift the front away to see underneath. The original FRONT edge belongs at the TOP of this photo."
};
export function rgbToHex(v:RGB){return "#"+v.map(n=>Math.max(0,Math.min(255,Math.round(n))).toString(16).padStart(2,"0")).join("");}
function dominantTone(samples:RGB[]):RGB{
 const bins=new Map<string,{count:number;sum:RGB}>();
 for(const rgb of samples){if(Math.max(...rgb)<50)continue;const key=rgb.map(n=>Math.round(n/24)).join(":");const b=bins.get(key)??{count:0,sum:[0,0,0]};b.count++;b.sum=b.sum.map((n,i)=>n+rgb[i]) as RGB;bins.set(key,b);}
 const best=[...bins.values()].sort((a,b)=>b.count-a.count)[0];return best?best.sum.map(n=>Math.round(n/best.count)) as RGB:[128,128,128];
}
export function scanCanvas(canvas:HTMLCanvasElement):PictureScan{
 const ctx=canvas.getContext("2d",{willReadFrequently:true});if(!ctx)throw new Error("Cannot read photo");
 const size=canvas.width/3,tileSamples:RGB[][]=[];
 for(let index=0;index<9;index++){
  const inset=size*.1,width=Math.floor(size-inset*2);
  const data=ctx.getImageData(Math.floor((index%3)*size+inset),Math.floor(Math.floor(index/3)*size+inset),width,width).data;
  const samples:RGB[]=[];
  for(let y=0;y<width;y+=6)for(let x=0;x<width;x+=6){const i=(y*width+x)*4;const rgb:RGB=[data[i],data[i+1],data[i+2]];if(Math.max(...rgb)>42)samples.push(rgb);}
  tileSamples.push(samples);
 }
 return {preview:canvas.toDataURL("image/jpeg",.92),faceTone:dominantTone(tileSamples[4]),tileSamples};
}
// Map a square to the selected quadrilateral using a projective homography.
// This removes perspective without moving, inventing, or independently rotating tiles.
function cropFace(image:HTMLImageElement,points:Point[]){
 const source=document.createElement("canvas");const scale=Math.min(1,1600/Math.max(image.width,image.height));
 source.width=Math.round(image.width*scale);source.height=Math.round(image.height*scale);
 const ctx=source.getContext("2d",{willReadFrequently:true})!;ctx.drawImage(image,0,0,source.width,source.height);
 const q=points.map(([x,y])=>[x*source.width,y*source.height]);
 // Reject crossed or collapsed corner selections before dividing.
 const crosses=q.map((p,i)=>{const b=q[(i+1)%4],c=q[(i+2)%4];return (b[0]-p[0])*(c[1]-b[1])-(b[1]-p[1])*(c[0]-b[0]);});
 if(crosses.some(n=>n<20))throw new Error("Keep the four corners in order around one face, without crossing them.");
 const [[x0,y0],[x1,y1],[x2,y2],[x3,y3]]=q;
 const dx1=x1-x2,dx2=x3-x2,dx3=x0-x1+x2-x3,dy1=y1-y2,dy2=y3-y2,dy3=y0-y1+y2-y3;
 const det=dx1*dy2-dx2*dy1;
 if(Math.abs(det)<.01)throw new Error("Spread the corners around the full cube face.");
 const g=(dx3*dy2-dx2*dy3)/det,h=(dx1*dy3-dx3*dy1)/det;
 const a=x1-x0+g*x1,b=x3-x0+h*x3,d=y1-y0+g*y1,e=y3-y0+h*y3;
 const input=ctx.getImageData(0,0,source.width,source.height).data;
 const canvas=document.createElement("canvas");canvas.width=540;canvas.height=540;const out=canvas.getContext("2d")!,pixels=out.createImageData(540,540);
 for(let y=0;y<540;y++)for(let x=0;x<540;x++){
  const u=x/539,v=y/539,den=g*u+h*v+1;
  const sx=Math.max(0,Math.min(source.width-1,Math.round((a*u+b*v+x0)/den)));
  const sy=Math.max(0,Math.min(source.height-1,Math.round((d*u+e*v+y0)/den)));
  const from=(sy*source.width+sx)*4,to=(y*540+x)*4;
  for(let channel=0;channel<3;channel++)pixels.data[to+channel]=input[from+channel];pixels.data[to+3]=255;
 }
 out.putImageData(pixels,0,0);return canvas;
}
export function PhotoScanner({face,faceName,scan,onScan,onFaceToneChange}:{
 face:FaceKey;faceName:string;scan?:PictureScan;onScan:(scan:PictureScan)=>void;onFaceToneChange:(tone:RGB)=>void;
}){
 const [camera,setCamera]=useState(false),[error,setError]=useState(""),[processing,setProcessing]=useState(false);
 const [crop,setCrop]=useState<HTMLImageElement|null>(null);
 const [points,setPoints]=useState<Point[]>([[.08,.08],[.92,.08],[.92,.92],[.08,.92]]);
 const [picking,setPicking]=useState(false);
 const file=useRef<HTMLInputElement>(null),video=useRef<HTMLVideoElement>(null),stream=useRef<MediaStream|null>(null);
 const dragging=useRef<number|null>(null),cropBox=useRef<HTMLDivElement>(null),mounted=useRef(true);
 useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
 useEffect(()=>{
  if(!camera)return;let cancelled=false;
  if(!navigator.mediaDevices?.getUserMedia){setCamera(false);setError("Use Choose photo to take or select a picture on this device.");return;}
  void navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:"environment"}},audio:false}).then(s=>{
   if(cancelled){s.getTracks().forEach(t=>t.stop());return;}stream.current=s;if(video.current){video.current.srcObject=s;void video.current.play().catch(()=>{});}
  }).catch(()=>{setCamera(false);setError("Camera access was blocked. Choose a photo instead.");});
  return()=>{cancelled=true;stream.current?.getTracks().forEach(t=>t.stop());stream.current=null;};
 },[camera]);
 async function openCrop(source:string){
  setError("");setProcessing(true);
  try{const img=new Image();img.src=source;await img.decode();if(!mounted.current)return;
   const canvas=document.createElement("canvas"),scale=Math.min(1,1600/Math.max(img.width,img.height));canvas.width=Math.round(img.width*scale);canvas.height=Math.round(img.height*scale);canvas.getContext("2d")!.drawImage(img,0,0,canvas.width,canvas.height);
   const stable=new Image();stable.src=canvas.toDataURL("image/jpeg",.95);await stable.decode();if(!mounted.current)return;
   setCrop(stable);setPoints([[.08,.08],[.92,.08],[.92,.92],[.08,.92]]);setCamera(false);}
  catch{if(mounted.current)setError("That image could not be read. Try a JPEG or take a new photo.");}
  finally{if(mounted.current)setProcessing(false);}
 }
 function capture(){
  const v=video.current;if(!v||v.readyState<2){setError("The camera is still starting.");return;}
  const c=document.createElement("canvas");c.width=v.videoWidth;c.height=v.videoHeight;c.getContext("2d")!.drawImage(v,0,0);
  void openCrop(c.toDataURL("image/jpeg",.95));
 }
 async function rotatePreview(){
  if(!scan)return;const img=new Image();img.src=scan.preview;await img.decode();
  const c=document.createElement("canvas");c.width=540;c.height=540;const ctx=c.getContext("2d")!;ctx.translate(270,270);ctx.rotate(-Math.PI/2);ctx.drawImage(img,-270,-270,540,540);
  onScan({...scanCanvas(c),faceTone:scan.faceTone});
 }
 async function pickTone(x:number,y:number){
  if(!scan||!picking)return;
  const img=new Image();img.src=scan.preview;await img.decode();const c=document.createElement("canvas");c.width=540;c.height=540;const ctx=c.getContext("2d")!;ctx.drawImage(img,0,0,540,540);
  const px=ctx.getImageData(Math.max(0,Math.min(539,Math.floor(x*540))),Math.max(0,Math.min(539,Math.floor(y*540))),1,1).data;
  onFaceToneChange([px[0],px[1],px[2]]);setPicking(false);
 }
 return <div className="photo-scanner">
  <div className="face-instruction"><Camera/><p><strong>{faceName} face:</strong> {GUIDE[face]} Rotate the whole cube for photos, not a layer. Return to your starting hold before each photo.</p></div>
  {crop?<div className="crop-editor">
   <p>Drag the four handles to the outside corners of this face. Keep all nine tiles inside.</p>
   <div className="crop-image" ref={cropBox} style={{aspectRatio:crop.width+"/"+crop.height}}
    onPointerMove={event=>{if(dragging.current===null||!cropBox.current)return;const r=cropBox.current.getBoundingClientRect();const p:Point=[Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];setPoints(old=>old.map((v,i)=>i===dragging.current?p:v));}}
    onPointerUp={()=>dragging.current=null} onPointerCancel={()=>dragging.current=null}>
    <img src={crop.src} alt="Adjust the crop corners"/>
    <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true"><polygon points={points.map(p=>p.map(n=>n*100).join(",")).join(" ")}/></svg>
    {points.map(([x,y],i)=><button key={i} className="crop-handle" style={{left:x*100+"%",top:y*100+"%"}} aria-label={["Top left crop corner","Top right crop corner","Bottom right crop corner","Bottom left crop corner"][i]}
     onPointerDown={e=>{dragging.current=i;e.currentTarget.setPointerCapture(e.pointerId);}}
     onKeyDown={e=>{const delta:Record<string,Point>={ArrowLeft:[-.01,0],ArrowRight:[.01,0],ArrowUp:[0,-.01],ArrowDown:[0,.01]};const d=delta[e.key];if(d){e.preventDefault();setPoints(old=>old.map((p,j)=>j===i?[Math.max(0,Math.min(1,p[0]+d[0])),Math.max(0,Math.min(1,p[1]+d[1]))]:p));}}}>{i+1}</button>)}
   </div>
   <div className="capture-actions"><Button variant="outline" onClick={()=>setCrop(null)}>Cancel</Button><Button onClick={()=>{try{onScan(scanCanvas(cropFace(crop,points)));setCrop(null);}catch(e){setError(e instanceof Error?e.message:"Please adjust the corners.");}}}>Use this face</Button></div>
  </div>:<>
   <div className={"capture-frame "+(scan||camera?"has-media ":"")+(picking?"picking-tone":"")}>
    {camera?<video ref={video} playsInline muted aria-label="Live camera"/>:scan?<button className="photo-tone-target" type="button" disabled={!picking} aria-label="Choose the picture background" onClick={e=>{const r=e.currentTarget.getBoundingClientRect();void pickTone((e.clientX-r.left)/r.width,(e.clientY-r.top)/r.height);}}><img src={scan.preview} alt={faceName+" captured picture"}/></button>:<div className="capture-empty"><Camera/><strong>{faceName} picture</strong><span>Photograph the cube as it is now</span></div>}
    {!scan&&<div className="scan-grid" aria-hidden="true">{Array.from({length:9},(_,i)=><i key={i}/>)}</div>}
   </div>
   <input className="visually-hidden" ref={file} type="file" accept="image/*" onChange={e=>{const f=e.target.files?.[0];if(f){const url=URL.createObjectURL(f);void openCrop(url).finally(()=>{URL.revokeObjectURL(url);if(file.current)file.current.value="";});}}}/>
   <div className="capture-actions">{camera?<><Button variant="outline" onClick={()=>setCamera(false)}>Cancel</Button><Button onClick={capture}><Camera/> Take photo</Button></>:<><Button disabled={processing} variant="outline" onClick={()=>file.current?.click()}><ImagePlus/> Choose photo</Button><Button disabled={processing} onClick={()=>{setError("");setCamera(true);}}><Camera/> Open camera</Button></>}</div>
   {scan&&!camera&&<div className="scan-calibration">
    <Button variant="outline" onClick={()=>void rotatePreview()}><RotateCcw/> Rotate whole photo</Button>
    <button type="button" className="tone-picker-button" onClick={()=>setPicking(!picking)}><i style={{background:rgbToHex(scan.faceTone)}}/>{picking?"Tap the main background on the center tile":"Fix background sample"}</button>
    <p>Photo rotation must match the holding guide, even if the sheep looks sideways. You will fix the middle tile separately after the picture pieces are assembled.</p>
   </div>}
  </>}
  {error&&<p className="camera-error" role="alert">{error}</p>}
  <p className="privacy-note"><ShieldCheck/> Photos stay on this device. Background matching needs your review.</p>
 </div>;
}
