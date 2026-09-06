import type { Plugin } from "vite";

// cubejs 1.3.2 uses a legacy top-level `this.Cube` check. In strict ESM
// bundles `this` is undefined, so both the browser import and the worker
// throw before the solver starts. Keep the declared dependency unchanged
// and resolve its existing CommonJS model explicitly during bundling.
export function cubejsCompat(): Plugin {
  return {
    name: "cubejs-strict-esm-compat",
    enforce: "pre",
    transform(code, id) {
      if (!id.split("?")[0].replaceAll("\\", "/").endsWith("/cubejs/lib/solve.js")) return;
      return {
        code: code.replace("Cube = this.Cube || require('./cube');", "Cube = require('./cube');"),
        map: null,
      };
    },
  };
}
