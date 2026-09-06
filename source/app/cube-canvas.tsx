"use client";
import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { BASIS, FACE_KEYS, initialPictures, stickerPosition, type FaceKey, type PictureState } from "./supercube";
export type StickerColor = string | null;
export type CubeFaces = Record<FaceKey, StickerColor[]>;
export type FaceImages = Partial<Record<FaceKey,string>>;
const HEX:Record<string,string>={white:"#f8fafc",yellow:"#ffd500",green:"#16a34a",blue:"#2563eb",red:"#ef2b2d",orange:"#ff7a00"};
type Props={faces:CubeFaces;faceImages?:FaceImages;pictures?:PictureState;transition?:{id:number;move:string}|null;focus?:FaceKey|null};

export function CubeCanvas({faces,faceImages={},pictures=initialPictures(),transition,focus}:Props) {
  const hostRef=useRef<HTMLDivElement>(null);
  const input=useRef({faces,faceImages,pictures,transition,focus});
  input.current={faces,faceImages,pictures,transition,focus};
  const reset=useRef<()=>void>(()=>{});
  const [unavailable,setUnavailable]=useState(false);
  useEffect(()=>{
    const host=hostRef.current;if(!host)return;
    let renderer:THREE.WebGLRenderer;
    try{renderer=new THREE.WebGLRenderer({antialias:true,alpha:true});}catch{setUnavailable(true);return;}
    renderer.setPixelRatio(Math.min(window.devicePixelRatio,2));renderer.outputColorSpace=THREE.SRGBColorSpace;
    host.appendChild(renderer.domElement);
    const scene=new THREE.Scene();
    const camera=new THREE.PerspectiveCamera(34,1,.1,100);camera.position.set(6.4,5.2,7.2);
    const controls=new OrbitControls(camera,renderer.domElement);controls.enableDamping=true;controls.enablePan=false;controls.minDistance=7;controls.maxDistance=16;
    reset.current=()=>{camera.up.set(0,1,0);camera.position.set(6.4,5.2,7.2);controls.target.set(0,0,0);controls.update();};
    scene.add(new THREE.HemisphereLight(0xffffff,0x111827,2));
    const light=new THREE.DirectionalLight(0xffffff,2);light.position.set(5,8,7);scene.add(light);
    const group=new THREE.Group();scene.add(group);
    const pivot=new THREE.Group();group.add(pivot);
    const cubeGeometry=new THREE.BoxGeometry(.96,.96,.96);
    const black=new THREE.MeshStandardMaterial({color:"#111318",roughness:.55});
    const bodies:THREE.Mesh[]=[];
    for(let x=-1;x<=1;x++)for(let y=-1;y<=1;y++)for(let z=-1;z<=1;z++){
      const mesh=new THREE.Mesh(cubeGeometry,black);mesh.position.set(x,y,z);group.add(mesh);bodies.push(mesh);
    }
    const planeGeometry=new THREE.PlaneGeometry(.9,.9);
    const stickers:THREE.Mesh<THREE.PlaneGeometry,THREE.MeshBasicMaterial>[]=[];
    for(const face of FACE_KEYS)for(let i=0;i<9;i++){
      const mesh=new THREE.Mesh(planeGeometry,new THREE.MeshBasicMaterial({color:"#555"}));
      mesh.userData={face,index:i};group.add(mesh);stickers.push(mesh);
    }
    const textures=new Map<string,THREE.CanvasTexture>();
    let imageRefs:FaceImages={};let textureVersion=0;let disposed=false;let dirty=true;
    let lastPictures:PictureState|null=null;let lastFaces:CubeFaces|null=null;
    let transitionId:number|undefined;let lastFocus:FaceKey|null|undefined;
    let animation:{start:number;axis:THREE.Vector3;angle:number}|null=null;
    function draw(){
      while(pivot.children.length)group.attach(pivot.children[0]);pivot.quaternion.identity();
      let k=0;for(let x=-1;x<=1;x++)for(let y=-1;y<=1;y++)for(let z=-1;z<=1;z++){
        bodies[k].position.set(x,y,z);bodies[k].quaternion.identity();k++;
      }
      for(const mesh of stickers){
        const face=mesh.userData.face as FaceKey,index=mesh.userData.index as number;
        const basis=BASIS[face],tile=input.current.pictures[face][index];
        const pos=stickerPosition(face,index);mesh.position.set(...pos).addScaledVector(new THREE.Vector3(...basis.normal),.49);
        mesh.quaternion.setFromRotationMatrix(new THREE.Matrix4().makeBasis(new THREE.Vector3(...basis.right),new THREE.Vector3(...basis.up),new THREE.Vector3(...basis.normal)));
        mesh.rotateZ(-tile.turns*Math.PI/2);
        mesh.material.map=textures.get(tile.sourceFace+":"+tile.sourceIndex)??null;
        mesh.material.color.set(mesh.material.map?"#fff":HEX[input.current.faces[face][index]??""]??"#626a7a");
        mesh.material.needsUpdate=true;
      }
      lastPictures=input.current.pictures;lastFaces=input.current.faces;dirty=false;
    }
    function loadImages(){
      if(FACE_KEYS.every(f=>imageRefs[f]===input.current.faceImages[f]))return;
      imageRefs={...input.current.faceImages};const version=++textureVersion;
      for(const texture of textures.values())texture.dispose();textures.clear();dirty=true;
      for(const face of FACE_KEYS){
        const source=imageRefs[face];if(!source)continue;
        const image=new Image();image.onload=()=>{
          if(disposed||version!==textureVersion)return;
          for(let index=0;index<9;index++){
            const canvas=document.createElement("canvas");canvas.width=192;canvas.height=192;
            const ctx=canvas.getContext("2d");if(!ctx)continue;
            ctx.drawImage(image,(index%3)*image.width/3,Math.floor(index/3)*image.height/3,image.width/3,image.height/3,0,0,192,192);
            const texture=new THREE.CanvasTexture(canvas);texture.colorSpace=THREE.SRGBColorSpace;texture.anisotropy=4;
            textures.set(face+":"+index,texture);
          }dirty=true;
        };image.src=source;
      }
    }
    const resize=()=>{const w=host.clientWidth,h=host.clientHeight;renderer.setSize(w,h,false);camera.aspect=w/Math.max(h,1);camera.updateProjectionMatrix();};
    const observer=new ResizeObserver(resize);observer.observe(host);resize();
    let frame=0;
    const render=(time:number)=>{
      loadImages();
      const next=input.current;
      if(next.focus!==lastFocus){lastFocus=next.focus;if(next.focus){const b=BASIS[next.focus];camera.position.set(...b.normal).multiplyScalar(11);camera.up.set(...b.up);controls.update();}else reset.current();}
      if(next.transition?.id!==transitionId){
        transitionId=next.transition?.id;
        if(animation){animation=null;draw();}
        else if(next.transition&&lastPictures&&lastPictures!==next.pictures&&!window.matchMedia("(prefers-reduced-motion: reduce)").matches){
          const move=next.transition.move,axis=new THREE.Vector3(...BASIS[move[0] as FaceKey].normal);
          for(const obj of [...bodies,...stickers])if(obj.position.dot(axis)>.9)pivot.attach(obj);
          animation={start:time,axis,angle:(move.endsWith("2")?-2:move.endsWith("'")?1:-1)*Math.PI/2};
        }
      }
      if(animation){const progress=Math.min(1,(time-animation.start)/450);pivot.quaternion.setFromAxisAngle(animation.axis,animation.angle*(progress*progress*(3-2*progress)));if(progress===1){animation=null;draw();}}
      else if(dirty||lastPictures!==next.pictures||lastFaces!==next.faces)draw();
      controls.update();renderer.render(scene,camera);frame=requestAnimationFrame(render);
    };frame=requestAnimationFrame(render);
    return()=>{disposed=true;cancelAnimationFrame(frame);observer.disconnect();controls.dispose();cubeGeometry.dispose();planeGeometry.dispose();black.dispose();for(const m of stickers)m.material.dispose();for(const t of textures.values())t.dispose();renderer.dispose();renderer.domElement.remove();};
  },[]);
  return <div className="cube-stage" aria-label="Interactive 3D picture cube"><div ref={hostRef} className="cube-canvas"/>{unavailable?<p className="webgl-error">3D is unavailable on this device. The photo previews and move instructions still work.</p>:<><button type="button" className="reset-view" onClick={()=>reset.current()}>Reset view</button><p className="drag-hint">Drag to look around · Pinch to zoom</p></>}</div>;
}
