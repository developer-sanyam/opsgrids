/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "false" to point the app at the live gateway instead of local fixtures. */
  readonly VITE_USE_FIXTURES?: string;
  /**
   * Where uploaded images are served from — `http://localhost:9100/opsgrids-public` against the
   * dev MinIO, the CDN origin in production (ADR-0009 §13, `PUBLIC_ASSET_BASE_URL`). Unset means
   * "same origin as this app"; see `theme/render/assetSrc.ts`.
   */
  readonly VITE_ASSET_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
