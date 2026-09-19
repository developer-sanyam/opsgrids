import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// Self-contained flat config. opsgrids-config is meant to ship a shared eslint preset
// (ADR-0001); until that lands, this app carries its own and graduates later.
export default tseslint.config(
  // `dist-buyer` is the second Vite output (ADR-0012 §2) and is built output like `dist`.
  { ignores: ["dist", "dist-buyer", "node_modules"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
    },
  },
  {
    files: ["vite.config.ts", "vitest.config.ts", "eslint.config.js"],
    languageOptions: { globals: globals.node },
  },
);
