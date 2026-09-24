// The one ESLint flat config every app and package uses (ADR-0001: "one eslint config").
// Each workspace's eslint.config.js is a one-line re-export of this, so a rule added here is
// held by fourteen packages at once and a component moved into @opsgrids/ui is held to exactly
// the rules it was held to in the app it came from.
//
// The plugins are this package's own devDependencies, not each app's: a preset that relies on
// the importing app to have installed its plugins breaks the first time one app forgets one.
// devDependencies rather than dependencies so `pnpm audit --prod` and `pnpm licenses --prod`
// keep meaning "what ships to a browser" — nothing here does.
import js from "@eslint/js";
import vitest from "@vitest/eslint-plugin";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  // `dist-buyer` is the storefront's second Vite output (ADR-0012 §2) and is built output like
  // `dist`; ignoring a directory a package does not have costs nothing. `storybook-static` is the
  // design system's workshop build; `test-results` and `playwright-report` are what the visual
  // harness (opsgrids-visual) leaves behind.
  {
    ignores: [
      "dist",
      "dist-buyer",
      "node_modules",
      "storybook-static",
      "test-results",
      "playwright-report",
    ],
  },
  {
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      // Semantic HTML first, labels tied to controls, no div-as-button (frontend/CLAUDE.md).
      // The recommended set, not strict: every rule in it is one a screen-reader user notices.
      jsxA11y.flatConfigs.recommended,
    ],
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
      // `role` on a component is a prop like any other — `<RoleForm role={…}>` passes a domain
      // Role, not an ARIA one. On a DOM element it is still checked.
      "jsx-a11y/aria-role": ["error", { ignoreNonDOM: true }],
      // A radio card's label text sits three elements deep (label > span > span > text). The
      // default depth of 2 cannot see it and reports a labelled control as unlabelled.
      "jsx-a11y/label-has-associated-control": ["error", { depth: 3 }],
    },
  },
  {
    // A focused test narrows the suite to itself and a skipped or todo test is a test that does
    // not run — none of which fails vitest, so all three fail lint instead. The frontend half
    // of what tools/hygiene.py does for pytest.
    files: ["**/*.{test,spec}.{ts,tsx}", "**/test-setup.{ts,tsx}"],
    plugins: { vitest },
    rules: {
      "vitest/no-focused-tests": "error",
      "vitest/no-disabled-tests": "error",
      "vitest/warn-todo": "error",
    },
  },
  {
    // A story file's exports are story objects by design (Component Story Format); Fast Refresh
    // has nothing to preserve in them, so the only-components rule does not apply.
    files: ["**/*.stories.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
  {
    files: ["*.config.{ts,js}", "eslint.config.js", ".storybook/main.ts"],
    languageOptions: { globals: globals.node },
  },
);
