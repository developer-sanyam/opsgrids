/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "true" to render the app from local fixtures with no login (offline design work). */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
