/**
 * The frontend's dependency boundaries (ADR-0001), checked by `make frontend-boundaries`.
 *
 * The backend's equivalent is `.importlinter`. Same idea: the topology in the ADR is only true
 * while something refuses the import that would break it, and a reviewer reading a 40-file diff
 * is not that something.
 *
 * Paths are relative to `frontend/`. Workspace packages resolve through pnpm's symlinks to their
 * real directories (`opsgrids-ui/src/index.ts`), which is what lets these rules see a product
 * importing the design system at all.
 *
 * Run once per package (see the Makefile) because every app maps `@/` to its own `src/`, and
 * one cruise can resolve it only one way.
 */

/** Every deployable surface. An app is a leaf: nothing imports it. */
const APP = "(product-[^/]+|opsgrids-dashboard|opsgrids-internal)";

/** The only files of the shared packages an app may land on: their `exports` entries. */
const UI_EXPORTS = "^opsgrids-ui/src/(index\\.ts|icons\\.tsx|styles/console\\.css)$";
const CLIENT_EXPORTS = "^opsgrids-client/src/index\\.ts$";

/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
  forbidden: [
    {
      name: "apps-are-independent",
      comment:
        "A product app never imports another app. A customer who bought only CRM downloads only " +
        "CRM (ADR-0001); shared code goes into @opsgrids/ui or @opsgrids/client, not sideways.",
      severity: "error",
      from: { path: `^${APP}/` },
      to: { path: `^${APP}/`, pathNot: "^$1/" },
    },
    {
      name: "ui-only-via-exports",
      comment:
        "An app reaches @opsgrids/ui through its package exports and nothing else. A deep import " +
        "couples the app to a file the design system is free to move.",
      severity: "error",
      from: { path: `^${APP}/` },
      to: { path: "^opsgrids-ui/", pathNot: UI_EXPORTS },
    },
    {
      name: "client-only-via-exports",
      comment: "An app reaches @opsgrids/client through its package exports and nothing else.",
      severity: "error",
      from: { path: `^${APP}/` },
      to: { path: "^opsgrids-client/", pathNot: CLIENT_EXPORTS },
    },
    {
      name: "ui-imports-no-app",
      comment: "The design system is below every app. An import upward is a cycle waiting to happen.",
      severity: "error",
      from: { path: "^opsgrids-ui/" },
      to: { path: `^${APP}/` },
    },
    {
      name: "ui-imports-no-client",
      comment:
        "Components are presentational: they take data and callbacks as props. Auth and HTTP live " +
        "in @opsgrids/client, and the app wires the two together.",
      severity: "error",
      from: { path: "^opsgrids-ui/" },
      to: { path: "^opsgrids-client/" },
    },
    {
      name: "client-imports-no-ui",
      comment: "The SDK renders nothing of the design system's; it must be usable without it.",
      severity: "error",
      from: { path: "^opsgrids-client/" },
      to: { path: "^opsgrids-ui/" },
    },
    {
      name: "client-imports-no-app",
      comment: "The SDK is below every app.",
      severity: "error",
      from: { path: "^opsgrids-client/" },
      to: { path: `^${APP}/` },
    },
    {
      name: "no-circular",
      comment: "No import cycles, anywhere — they make module initialisation order a runtime bug.",
      severity: "error",
      from: {},
      to: { circular: true },
    },
    {
      name: "not-to-unresolvable",
      comment:
        "An import that does not resolve is a typo, a missing dependency, or a deep import into a " +
        "workspace package that its `exports` does not offer.",
      severity: "error",
      from: {},
      to: { couldNotResolve: true },
    },
  ],
  options: {
    doNotFollow: { path: "node_modules" },
    exclude: { path: "(^|/)(dist|dist-buyer|node_modules|storybook-static)/" },
    // Type-only imports count: a cycle through `import type` is still a cycle in the design.
    tsPreCompilationDeps: true,
    // `@/` → this package's `src/`. See that file for why it is not read from the tsconfigs.
    webpackConfig: { fileName: ".dependency-cruiser.resolve.cjs" },
    // Honour package.json `exports`, so `@opsgrids/ui/src/Button` is unresolvable (and fails
    // `not-to-unresolvable`) exactly as it would be for the bundler.
    enhancedResolveOptions: {
      exportsFields: ["exports"],
      conditionNames: ["import", "require", "node", "default", "types"],
      mainFields: ["module", "main", "types", "typings"],
    },
    reporterOptions: { text: { highlightFocused: true } },
  },
};
