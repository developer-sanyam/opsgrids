/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Set to "true" to render the app from local fixtures with no login (offline design work). */
  readonly VITE_USE_FIXTURES?: string;
  /**
   * The browser-side Google Maps key for the sites map, the warehouse address lookup and the
   * put-away reveal pin. Absent or blank means a map-less build: every map surface falls back to a
   * table or plain text. Baked into the bundle, so the key must be referrer- and API-restricted.
   */
  readonly VITE_GOOGLE_MAPS_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
