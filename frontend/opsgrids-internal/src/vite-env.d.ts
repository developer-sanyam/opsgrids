/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Set to "false" to talk to the live admin-gateway instead of local fixtures. Defaults to
   * fixtures (`true`) while the console backend is not yet wired — this is a fixtures-first app.
   */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
