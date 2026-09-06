import assert from "node:assert/strict";
import {readdir} from "node:fs/promises";
import test from "node:test";
import Cube from "cubejs";

test("compiled browser cubejs chunk loads without a top-level-this crash",async()=>{
  const files=await readdir(new URL("../dist/client/assets/",import.meta.url));
  const file=files.find(f=>f.startsWith("cubejs-")&&f.endsWith(".js"));
  assert.ok(file);
  await import(new URL("../dist/client/assets/"+file,import.meta.url));
});
test("compiled production worker solves a real scramble",async()=>{
  const files=await readdir(new URL("../dist/client/assets/",import.meta.url));
  let result;
  globalThis.self={postMessage:value=>{result=value;}};
  try {
    const file=files.find(f=>f.startsWith("solver.worker-")&&f.endsWith(".js"));
    assert.ok(file);
    await import(new URL("../dist/client/assets/"+file,import.meta.url));
    const facelets=new Cube().move("R U F2 L' B D2 R'").asString();
    self.onmessage({data:facelets});
    assert.ok(!result.error,result.error);
    assert.equal(Cube.fromString(facelets).move(result.moves.join(" ")).isSolved(),true);
    self.onmessage({data:new Cube().asString()});
    assert.deepEqual(result.moves,[]);
    self.onmessage({data:"F".repeat(54)});
    assert.ok(result.error);
    const scramble="R U F2 L'";
    const jointCube=new Cube().move(scramble);
    self.onmessage({data:{facelets:jointCube.asString(),target:[3,3,2,0,1,0],seed:['L','F2',"U'","R'"]}});
    assert.ok(!result.error,result.error);
    assert.equal(jointCube.move(result.moves.join(' ')).isSolved(),true);
    assert.equal(result.optimal,true);
  } finally {delete globalThis.self;}
});
