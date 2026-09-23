/**
 * Fixture mode, on its own so anything may read it without importing the whole API client.
 *
 * It used to live in `./client`, and `@/settings/api` imported it from there while `./client`
 * imported `getStoreIdentity` from `@/settings/api` — an import cycle, which is initialisation
 * order decided by whichever module a bundle happened to load first. This file imports nothing.
 * `./client` re-exports both names, so existing `@/api/client` imports are unchanged.
 */

/** Default source. Fixtures unless `VITE_USE_FIXTURES=false` pins the app to the live gateway.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;
