/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Fixtures are the default source for this product (its backend is still a skeleton). Set
   * `VITE_USE_FIXTURES=false` to point the app at the live gateway once the service ships.
   */
  readonly VITE_USE_FIXTURES?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
