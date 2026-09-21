/**
 * The hooks reading a grant set they were handed directly — fixture mode, and any test that wants
 * a caller with a known set. The provider fetching its own from `GET /members/me/grants` is
 * covered next door in `grants.test.tsx`.
 *
 * What the hooks do when they are *not* told anything still matters most: "unknown" must hide a
 * control rather than show one that 403s at the end of a form.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { setAccessToken } from "../session";
import { PermissionProvider } from "./PermissionProvider";
import { RequirePermission } from "./RequirePermission";
import { usePermission, usePermissionStatus, useEntitlement } from "./context";

function Probe({ perm, scope }: { perm: string; scope?: "OWN" | "TEAM" | "LOCATION" | "ORG" }) {
  const allowed = usePermission(perm, scope ?? "OWN");
  const status = usePermissionStatus(perm, scope ?? "OWN");
  return <span>{`${allowed ? "yes" : "no"}/${status}`}</span>;
}

/** A JWT whose payload is readable and whose signature is meaningless — which is the point. */
function tokenWith(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "RS256" })}.${encode(claims)}.not-a-signature`;
}

describe("usePermission", () => {
  it("grants what the caller holds at or above the scope asked for", () => {
    render(
      <PermissionProvider grants={["hrms.people.employee.read:ORG"]} entitlements={[]}>
        <Probe perm="hrms.people.employee.read" scope="TEAM" />
      </PermissionProvider>,
    );
    expect(screen.getByText("yes/granted")).toBeDefined();
  });

  it("refuses a name held only below the endpoint's floor", () => {
    render(
      <PermissionProvider grants={["core.approval.request.decide:OWN"]} entitlements={[]}>
        <Probe perm="core.approval.request.decide" scope="ORG" />
      </PermissionProvider>,
    );
    expect(screen.getByText("no/denied")).toBeDefined();
  });

  it("reads as unknown — and hides — when the app has no grants to give", () => {
    render(
      <PermissionProvider entitlements={[]}>
        <Probe perm="core.approval.policy.write" scope="ORG" />
      </PermissionProvider>,
    );
    expect(screen.getByText("no/unknown")).toBeDefined();
  });

  it("hides rather than throwing when no provider is mounted at all", () => {
    render(<Probe perm="core.approval.policy.write" />);
    expect(screen.getByText("no/unknown")).toBeDefined();
  });
});

describe("RequirePermission", () => {
  it("renders the control for a holder", () => {
    render(
      <PermissionProvider grants={["core.approval.policy.write:ORG"]} entitlements={[]}>
        <RequirePermission perm="core.approval.policy.write" scope="ORG">
          <button type="button">Approval settings</button>
        </RequirePermission>
      </PermissionProvider>,
    );
    expect(screen.getByRole("button", { name: "Approval settings" })).toBeDefined();
  });

  it("renders nothing — not a disabled control — for everyone else", () => {
    render(
      <PermissionProvider grants={["core.approval.request.read:OWN"]} entitlements={[]}>
        <RequirePermission perm="core.approval.policy.write" scope="ORG">
          <button type="button">Approval settings</button>
        </RequirePermission>
      </PermissionProvider>,
    );
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("renders the fallback a screen supplies, so it can say who to ask", () => {
    render(
      <PermissionProvider grants={[]} entitlements={[]}>
        <RequirePermission
          perm="core.approval.policy.write"
          scope="ORG"
          fallback={<p>Only an approvals administrator can change these.</p>}
        >
          <button type="button">Approval settings</button>
        </RequirePermission>
      </PermissionProvider>,
    );
    expect(screen.getByText("Only an approvals administrator can change these.")).toBeDefined();
  });
});

describe("useEntitlement", () => {
  function EntitlementProbe({ feature }: { feature: string }) {
    return <span>{useEntitlement(feature) ? "on-plan" : "off-plan"}</span>;
  }

  it("reads the activated products from the token's ent claim", () => {
    setAccessToken(tokenWith({ ent: ["hrms", "crm"] }));
    render(
      <PermissionProvider>
        <EntitlementProbe feature="hrms" />
      </PermissionProvider>,
    );
    expect(screen.getByText("on-plan")).toBeDefined();
    setAccessToken(null);
  });

  it("is false for a product the organization has not activated", () => {
    setAccessToken(tokenWith({ ent: ["crm"] }));
    render(
      <PermissionProvider>
        <EntitlementProbe feature="hrms" />
      </PermissionProvider>,
    );
    expect(screen.getByText("off-plan")).toBeDefined();
    setAccessToken(null);
  });

  it("is false when there is no token to read", () => {
    setAccessToken(null);
    render(
      <PermissionProvider>
        <EntitlementProbe feature="hrms" />
      </PermissionProvider>,
    );
    expect(screen.getByText("off-plan")).toBeDefined();
  });
});
