/**
 * The stand-in operator identity for fixtures mode, kept in its own module — never a static
 * import from `AuthProvider.tsx` — so `import("./fixtureOperator")` is the only path to it. Gated
 * behind `if (USE_FIXTURES)`, that dynamic import is provably unreachable in a production build
 * (`USE_FIXTURES` is always `false` there) and Rollup drops it and this fabricated identity
 * entirely rather than shipping it, unused, to `admin.opsgrids.com`.
 */

import type { OperatorMe } from "./types";

export const FIXTURE_OPERATOR: OperatorMe = {
  staff_id: "op-8f21a0c4",
  email: "priya.venkatesan@opsgrids.com",
  display_name: "Priya Venkatesan",
  operator_roles: ["owner"],
  amr: ["pwd"],
};

/** A synthetic token used only in fixtures mode so the session shape is exercised end to end. */
export const FIXTURE_TOKEN = "fixtures.operator.token";
