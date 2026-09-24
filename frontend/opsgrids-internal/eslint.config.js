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

const NO_RAW_HTML =
  "The operator console never writes markup from a string: it renders what an operator can read and act on, and a string that reached the DOM as HTML is a script that ran with an operator's session. Render elements.";

/** `no-restricted-syntax` takes one list per config block, and the token rules already use it. */
const NO_DANGEROUS_HTML = {
  selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
  message: NO_RAW_HTML,
};

/**
 * The token rules with the markup rule added to their list. A later block that set
 * `no-restricted-syntax` on its own would replace the token selectors instead of joining them, and
 * the token lint would go quiet without a word; so the union is built from the token blocks
 * themselves, which also keeps their file scope (source, not tests) in one place.
 */
const tokensAndMarkup = tokens.map((block) => ({
  ...block,
  rules: {
    ...block.rules,
    "no-restricted-syntax": [
      "error",
      ...block.rules["no-restricted-syntax"].slice(1),
      NO_DANGEROUS_HTML,
    ],
  },
}));

export default [
  ...preset,
  {
    // What no line of the operator console does, whichever way it is reached. It is a staff surface
    // holding an operator token that can suspend an organization, so the calls that turn a string
    // into script or markup, and the browser's own modal prompts (which cannot be styled, focused
    // or tested, and which a confirmation must never depend on), are errors here rather than habits.
    files: ["src/**/*.{ts,tsx}"],
    rules: {
      "no-console": "error",
      "no-eval": "error",
      "no-implied-eval": "error",
      "no-new-func": "error",
      "no-alert": "error",
      "no-restricted-syntax": ["error", NO_DANGEROUS_HTML],
      "no-restricted-properties": [
        "error",
        { property: "innerHTML", message: NO_RAW_HTML },
        { property: "outerHTML", message: NO_RAW_HTML },
        { property: "insertAdjacentHTML", message: NO_RAW_HTML },
        { object: "document", property: "write", message: NO_RAW_HTML },
        { object: "document", property: "writeln", message: NO_RAW_HTML },
      ],
    },
  },
  // After the block above, so the source files (not the tests) get the token rules *and* the markup
  // rule rather than the markup rule alone.
  ...tokensAndMarkup,
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
