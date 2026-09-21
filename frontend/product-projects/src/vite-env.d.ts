/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Live by default. Set to "true" in a dev build to render the app from local fixtures instead
   * of the real gateway — handy for offline design work. Compiled out of production entirely:
   * `import.meta.env.DEV` is `false` there, so this can never flip a shipped build to fixtures.
   */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
