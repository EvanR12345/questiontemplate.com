"use client";
import type { PictureTile } from "./supercube";
import type { FaceImages, StickerColor } from "./cube-canvas";
export const FACE_HEX: Record<string,string> = {white:"#f8fafc",yellow:"#ffd500",green:"#16a34a",blue:"#2563eb",red:"#ef2b2d",orange:"#ff7a00"};
export function PictureFace({tiles, images, colors, centerTurn=0, onTile, label, showLabels=false}: {
  tiles: PictureTile[]; images: FaceImages; colors: StickerColor[]; centerTurn?: number;
  onTile?: (index:number)=>void; label:string; showLabels?:boolean;
}) {
  return <div className="picture-face-grid" aria-label={label}>{tiles.map((tile,index)=>{
    const src=images[tile.sourceFace];
    const style={backgroundColor:FACE_HEX[colors[index]??""]??"#444c5c", backgroundImage:src?`url(${src})`:undefined,
      backgroundSize:"300% 300%",backgroundPosition:`${tile.sourceIndex%3*50}% ${Math.floor(tile.sourceIndex/3)*50}%`,
      transform:`rotate(${(tile.turns+(index===4?centerTurn:0))*90}deg)`};
    const matchLabel:Record<string,string>={white:"U",red:"R",green:"F",yellow:"D",orange:"L",blue:"B"};
    const content=<><span className="picture-fragment" style={style}/>{showLabels&&<small>{matchLabel[colors[index]??""]??"?"}</small>}{!src&&index===4&&<b className="picture-arrow" style={{transform:`rotate(${(tile.turns+centerTurn)*90}deg)`}}>↑</b>}</>;
    return onTile?<button type="button" key={index} onClick={()=>onTile(index)} aria-label={`${label}, tile ${index+1}${index===4?", center":""}`}>{content}</button>:<div key={index}>{content}</div>;
  })}</div>;
}
