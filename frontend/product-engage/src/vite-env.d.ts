/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "true" to render the app from `src/api/fixtures.ts` instead of the gateway. */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
