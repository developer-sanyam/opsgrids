// The shared preset (ADR-0001). A rule belongs there, not here, so every app keeps it.
import preset from "@opsgrids/config/eslint";
// Feature code names a token, never a value. The operator console adopts the rule first; it is the
// app that was migrated onto the design system in one piece, so it starts clean.
import tokens from "@opsgrids/config/eslint-tokens";

/**
 * The tenant half of `@opsgrids/client`: the sign-in, the session provider and the grants. It is
 * all written against tenant routes and tenant tokens (`/auth/login`, `/auth/me`,
 * `/members/me/grants`), and the operator console must never reach any of it — an operator token
 * on a tenant route, or a tenant sign-in on the operator surface, is exactly the crossing ADR-0003
 * §2 draws the audience boundary to prevent. The operator's own sign-in is `src/auth/`, built on the
 * client's *primitives* (`apiRequest`, `ApiError`, the session store, `useAsync`, `useMutation`),
 * which are audience-neutral and stay allowed.
 */
const TENANT_ONLY = [
  "AuthProvider",
  "AuthContext",
  "useAuth",
  "RequireAuth",
  "authApi",
  "PermissionProvider",
  "PermissionContext",
  "fetchMyGrants",
  "Can",
  "RequirePermission",
  "useCan",
  "useEntitlement",
  "usePermission",
  "usePermissions",
  "usePermissionStatus",
];

export default [
  ...preset,
  ...tokens,
  {
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-imports": [
        "error",
        {
          paths: [
            {
              name: "@opsgrids/client",
              importNames: TENANT_ONLY,
              message:
                "The tenant sign-in, session and grants are for the product apps. The operator console signs in through src/auth/ and reaches only /admin and /auth/operator (ADR-0003 §2).",
            },
          ],
        },
      ],
    },
  },
];
