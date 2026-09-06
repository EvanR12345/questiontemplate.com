import Cube from "cubejs";
import { validateCubies } from "./supercube";
let ready = false;
self.onmessage = (event: MessageEvent<string>) => {
  try {
    const cube = Cube.fromString(event.data);
    if (cube.asString() !== event.data) throw new Error("Some tiles do not form real cube pieces. Recheck the matches.");
    const error = validateCubies(cube.toJSON());
    if (error) throw new Error(error);
    if (cube.isSolved()) { self.postMessage({moves: []}); return; }
    if (!ready) { Cube.initSolver(); ready = true; }
    const result = cube.solve().trim();
    const moves = result ? result.split(/\s+/) : [];
    cube.move(moves.join(" "));
    if(cube.asString()!=="UUUUUUUUURRRRRRRRRFFFFFFFFFDDDDDDDDDLLLLLLLLLBBBBBBBBB") throw new Error("The solution could not be verified. Please check the entered tiles.");
    self.postMessage({moves});
  } catch(error) { self.postMessage({error:error instanceof Error ? error.message : "Could not solve this layout."}); }
};
