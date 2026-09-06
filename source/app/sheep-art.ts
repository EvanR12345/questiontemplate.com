import { cropFace, scanCanvas, type PictureScan } from './photo-scanner';
import { FACE_KEYS, type FaceKey, type PictureState } from './supercube';
import type { FaceImages } from './cube-canvas';

// Actual retailer photographs, projected at runtime. These are display artwork,
// not a claimed factory face arrangement or automatically recognized user state.
const ART:Record<FaceKey,{file:string;size:[number,number];corners:[number,number][]}>={
 U:{file:'sheep-other.png',size:[800,800],corners:[[110,225],[374,422],[376,740],[125,540]]},
 R:{file:'sheep-other.png',size:[800,800],corners:[[440,65],[700,245],[378,410],[114,220]]},
 F:{file:'sheep-display.jpg',size:[1811,1375],corners:[[450,780],[808,815],[796,1089],[438,1042]]},
 D:{file:'sheep-display.jpg',size:[1811,1375],corners:[[823,814],[448,780],[598,652],[939,687]]},
 L:{file:'sheep-display.jpg',size:[1811,1375],corners:[[1293,718],[1181,844],[803,801],[965,667]]},
 B:{file:'sheep-product.jpg',size:[1536,1628],corners:[[1438,1330],[694,1528],[650,610],[1450,360]]},
};
let cached:Promise<FaceImages>|undefined;
export function loadSheepArt():Promise<FaceImages>{
 return cached??=Promise.all(FACE_KEYS.map(async face=>{
  const spec=ART[face],img=new Image();img.src='/sheep/'+spec.file;await img.decode();
  const canvas=cropFace(img,spec.corners.map(([x,y])=>[x/spec.size[0],y/spec.size[1]]));
  return [face,canvas.toDataURL('image/jpeg',.92)];
 })).then(entries=>Object.fromEntries(entries));
}
export async function renderSheepScans(pictures:PictureState,images:FaceImages):Promise<Record<FaceKey,PictureScan>>{
 const decoded=Object.fromEntries(await Promise.all(FACE_KEYS.map(async f=>{const img=new Image();img.src=images[f]!;await img.decode();return [f,img];})));
 return Object.fromEntries(FACE_KEYS.map(f=>{
  const canvas=document.createElement('canvas');canvas.width=540;canvas.height=540;const ctx=canvas.getContext('2d')!;
  pictures[f].forEach((tile,i)=>{
   ctx.save();ctx.translate((i%3)*180+90,Math.floor(i/3)*180+90);ctx.rotate(tile.turns*Math.PI/2);
   ctx.drawImage(decoded[tile.sourceFace],(tile.sourceIndex%3)*180,Math.floor(tile.sourceIndex/3)*180,180,180,-90,-90,180,180);ctx.restore();
  });return [f,scanCanvas(canvas)];
 })) as Record<FaceKey,PictureScan>;
}
