import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

// Self-contained flat config, matching the other apps until opsgrids-config ships a shared preset
// (ADR-0001).
//
// Two deliberate differences from the Vite apps. `react-refresh` is not here: that plugin guards
// Vite's fast-refresh boundary, and this app is Next, whose own compiler owns that concern — the
// rule would report on every `page.tsx` that correctly exports metadata beside its component.
// And `.next` joins the ignores for the same reason `dist` is in theirs: it is build output, and
// linting generated files reports faults nobody wrote.
export default tseslint.config(
  { ignores: [".next", "dist", "node_modules", "next-env.d.ts"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
    },
  },
  {
    files: ["eslint.config.js", "next.config.*"],
    languageOptions: { globals: globals.node },
  },
);
