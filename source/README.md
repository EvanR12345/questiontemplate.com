# Picture Supercube Solver

Live website: https://questiontemplate.com

The GitHub repository root contains the static website. `source/` contains the editable project, including the exact solver and regression tests used to build it. No user-uploaded photos are included in this repository.

## Use

1. Choose **Only my centers are wrong** if every outside picture tile is already correct. Otherwise choose **My cube is scrambled**.
2. Capture all six current faces using the holding guides. Drag the crop handles around the face. Return to the same starting hold before every capture. Do not rotate each photo just to make the sheep upright.
3. For a scrambled cube, review every proposed tile match. Background matching is a heuristic, not automatic picture recognition. Correct uncertain matches and confirm all six faces.
4. Follow the verified piece-solving moves. The photos travel with the pieces in 3D.
5. Rotate each middle square in the complete-picture preview until it joins the surrounding image. The desired rotation is relative to the picture, not to the screen's top edge. Confirm all six faces, including unchanged centers.
6. Follow the center-fixing moves. They temporarily scramble the outside pieces but restore them at the end. Do not twist removable center caps by hand.

Six solved reference photos are not required. In center-only mode, missing photos can be replaced by explicitly entering how far each real center needs to turn. A lone 90-degree center correction is not reachable through legal face turns; a lone 180-degree correction is.

The Wacky Woollies sheep cube was identified using the Carrolls Irish Gifts product listing. Retail images are linked as reference only and are not bundled. The solver does not assume a specific factory face arrangement or substitute invented sheep artwork.

## Implementation

- `app/supercube.ts`: exact sticker positions and picture orientations; center correction generators spanning 2,048 reachable states.
- `app/solver.worker.ts`: background two-phase cubie solver with validation and solution replay.
- `app/cube-canvas.tsx`: Three.js view with 54 independent photo textures and animated layer turns.
- `app/photo-scanner.tsx`: local camera/upload capture, four-corner projective crop, and background sampling.
- `build/cubejs-compat.ts`: fixes cubejs 1.3.2's legacy top-level `this.Cube` access in strict browser/worker bundles, without modifying dependencies.

Photos stay in memory on the user's device and are not uploaded. Refreshing clears them. The app does not claim fully automatic recognition; the user confirms picture matches and final center rotations.

## Build and verify

From `source/` in the GitHub checkout, install the locked dependencies, then run:

```sh
npm ci
npm run build
npx tsc --project tsconfig.solver.json
node --test tests/supercube.test.mjs tests/production-solver.test.mjs
```

The static export is in `dist/client/`. Publish its contents at the repository root while keeping the existing `CNAME` and `.nojekyll`. Keep prior content-addressed assets available for clients with cached HTML.

Tests compare every face move with cubejs, verify the center generators preserve all outside picture tiles and rotations, count all 2,048 legal center vectors, exercise center-only corrections, solve 30 deterministic full scrambles including picture orientations, and load/run the compiled browser and worker bundles.

No browser-driven camera or touch testing was performed in this revision; production code execution and mathematical/regression checks are automated.
