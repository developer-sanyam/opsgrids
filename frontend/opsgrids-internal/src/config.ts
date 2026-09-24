/**
 * Where the console's data comes from.
 *
 * Fixtures unless `VITE_USE_FIXTURES=false` pins the console to the live admin-gateway. Dev builds
 * only: `import.meta.env.DEV` compiles to `false` in a production build, so a stray CI or
 * production env var can never ship the console rendering fabricated data with the environment
 * badge suppressed.
 */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES !== "false";

/** True when the console is talking to the real admin-gateway. */
export const LIVE = !USE_FIXTURES;
