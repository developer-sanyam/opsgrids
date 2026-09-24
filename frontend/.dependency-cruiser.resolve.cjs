/**
 * The one resolution rule dependency-cruiser cannot read from the tsconfigs: every app maps `@/`
 * to its own `src/` through `paths` with no `baseUrl`, and the tsconfig-paths plugin it uses
 * only honours `paths` alongside a `baseUrl`. Adding `baseUrl` to fourteen tsconfigs would change
 * how TypeScript resolves every bare import in them to satisfy a linter; this does not.
 *
 * `make frontend-boundaries` runs one cruise per package and names it in DEPCRUISE_PACKAGE, so
 * `@/` resolves exactly as Vite and tsc resolve it for that package.
 */
const path = require("node:path");

const pkg = process.env.DEPCRUISE_PACKAGE;
if (!pkg) {
  throw new Error("DEPCRUISE_PACKAGE is not set — run this through `make frontend-boundaries`.");
}

module.exports = {
  resolve: {
    alias: { "@": path.resolve(__dirname, pkg, "src") },
  },
};
