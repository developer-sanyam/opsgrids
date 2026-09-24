/**
 * The browser storage slots this app owns, named once.
 *
 * Every OpsGrids app names its own (`opsgrids.finance.access_token`, `opsgrids.crm.theme`), and the
 * operator's carry `operator` so no tenant app's slot can ever be this one's. They are not a
 * security boundary — the console is served from its own origin, and `sessionStorage` and
 * `localStorage` are per-origin already — but a key that could collide is a mistake waiting for a
 * deployment that puts two apps on one origin, and `tools/frontend_manifest.py` refuses it.
 */

/** The `sessionStorage` slot the access token is mirrored to, for a reload within the tab. */
export const OPERATOR_SESSION_KEY = "opsgrids.operator.access_token";

/**
 * The `localStorage` slot holding the light/dark preference. It is a display choice and nothing
 * sensitive, and `index.html` reads it by this same name to set the theme before first paint.
 */
export const OPERATOR_THEME_KEY = "opsgrids.operator.theme";
