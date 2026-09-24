/**
 * The operator console is not allowed to reach the tenant half of the shared client, and this is the
 * lint rule that says so — run here as a test because a rule nobody has seen fail is a rule nobody
 * knows still works. `frontend/opsgrids-internal/eslint.config.js` restricts the tenant sign-in, the
 * tenant session provider and the grants hooks; the audience-neutral primitives stay open.
 *
 * The runtime half of the same boundary — a tenant token never sent to an operator route, an
 * operator token never sent to a tenant one — is `auth/operatorRequest.test.ts`. The workspace half —
 * no two apps sharing a session slot, no tenant app naming `/auth/operator` — is
 * `tools/frontend_manifest.py`.
 */

import { ESLint } from "eslint";
import { describe, expect, it } from "vitest";

/** ESLint reads this package's own `eslint.config.js`, exactly as `pnpm lint` does. */
const eslint = new ESLint({ cwd: process.cwd() });

/** The names an import statement was refused for, one import per line so a line says which. */
async function refusedIn(names: string[]): Promise<{ line: number; message: string }[]> {
  const source = names.map((name) => `import { ${name} } from "@opsgrids/client";`).join("\n");
  const [result] = await eslint.lintText(`${source}\nexport {};\n`, { filePath: "src/auth/Thing.tsx" });
  return (result?.messages ?? [])
    .filter((message) => message.ruleId === "no-restricted-imports")
    .map((message) => ({ line: message.line, message: message.message }));
}

const TENANT_ONLY = [
  "authApi",
  "AuthProvider",
  "AuthContext",
  "useAuth",
  "RequireAuth",
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

const AUDIENCE_NEUTRAL = [
  "apiRequest",
  "ApiError",
  "problemMessage",
  "useAsync",
  "useMutation",
  "getAccessToken",
  "setAccessToken",
  "configureSession",
  "notifyUnauthorized",
  "setUnauthorizedHandler",
  "decodeAccessToken",
  "settle",
];

describe("what the operator console may not import from @opsgrids/client", () => {
  it("refuses the tenant sign-in, session, grants and their hooks", async () => {
    const refused = await refusedIn(TENANT_ONLY);
    // Every name is refused, and each on its own line — none is let through by its neighbours.
    expect(refused.map((r) => r.line)).toEqual(TENANT_ONLY.map((_, index) => index + 1));
    for (const r of refused) expect(r.message).toContain("ADR-0003");
  });

  it("leaves the audience-neutral primitives open — they are what the operator sign-in is built on", async () => {
    expect(await refusedIn(AUDIENCE_NEUTRAL)).toEqual([]);
  });

  it("refuses one banned name among allowed ones, and only that one", async () => {
    const refused = await refusedIn(["ApiError", "useAuth", "useAsync"]);
    expect(refused.map((r) => r.line)).toEqual([2]);
  });
});
