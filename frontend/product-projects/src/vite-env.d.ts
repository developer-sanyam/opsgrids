/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "false" to render the app against the live gateway instead of local fixtures. */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
