import { describe, expect, it, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { MeOut, OrganizationChoice } from "../auth/types";
import { PermissionProvider } from "../permissions/PermissionProvider";
import { configurePlatformHosts, resetPlatformHosts } from "./catalogue";
import { usePlatformModules } from "./usePlatformModules";
import { useCurrentUser } from "./useCurrentUser";
import { useAdminLinks } from "./useAdminLinks";

function authValue(user: MeOut | null): AuthContextValue {
  return {
    status: user ? "authenticated" : "unauthenticated",
    user,
    login: () => Promise.reject(new Error("not used")),
    verifyMfa: () => Promise.reject(new Error("not used")),
    selectOrg: () => Promise.reject(new Error("not used")),
    logout: () => Promise.resolve(),
  };
}

function meWith(overrides: Partial<MeOut> & { organizations?: OrganizationChoice[] } = {}): MeOut {
  return {
    user_id: "u-1",
    email: "casey@example.com",
    full_name: "Casey Rivera",
    organization_id: "org-a",
    membership_id: "m-1",
    roles: [],
    role_names: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [],
    ...overrides,
  };
}

function Wrapper({
  user,
  grants,
  entitlements,
  children,
}: {
  user: MeOut | null;
  grants: readonly string[];
  entitlements: readonly string[];
  children: React.ReactNode;
}) {
  return (
    <AuthContext value={authValue(user)}>
      <PermissionProvider grants={grants} entitlements={entitlements}>
        {children}
      </PermissionProvider>
    </AuthContext>
  );
}

function ModulesProbe() {
  const modules = usePlatformModules();
  return (
    <ul>
      {modules.map((m) => (
        <li key={m.key}>
          {m.label} — {m.href}
        </li>
      ))}
    </ul>
  );
}

describe("usePlatformModules", () => {
  afterEach(() => {
    resetPlatformHosts();
  });

  it("hides a product that is entitled but carries no grant", () => {
    render(
      <Wrapper user={meWith()} grants={[]} entitlements={["hrms"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.queryByText(/HRMS/)).toBeNull();
  });

  it("hides a product the caller holds a grant in but the organization has not activated", () => {
    render(
      <Wrapper user={meWith()} grants={["hrms.people.employee.read:ORG"]} entitlements={[]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.queryByText(/HRMS/)).toBeNull();
  });

  it("shows a product entitled and granted, linking to its host", () => {
    render(
      <Wrapper user={meWith()} grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.getByText("HRMS — https://people.opsgrids.com")).toBeTruthy();
  });

  it("folds operations and inventory entitlements into the one Operations & Inventory entry", () => {
    render(
      <Wrapper
        user={meWith()}
        grants={["inventory.stock.item.read:ORG"]}
        entitlements={["inventory"]}
      >
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.getByText("Operations & Inventory — https://ops.opsgrids.com")).toBeTruthy();
  });

  it("a sales entitlement no longer resolves to CRM", () => {
    render(
      <Wrapper user={meWith()} grants={["sales.lead.record.read:ORG"]} entitlements={["sales"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.queryByText(/^CRM/)).toBeNull();
  });

  it("resolves hosts from the per-environment override", () => {
    configurePlatformHosts({ hrms: "https://people.staging.opsgrids.dev/" });
    render(
      <Wrapper user={meWith()} grants={["hrms.people.employee.read:ORG"]} entitlements={["hrms"]}>
        <ModulesProbe />
      </Wrapper>,
    );
    expect(screen.getByText("HRMS — https://people.staging.opsgrids.dev")).toBeTruthy();
  });
});

function UserProbe() {
  const user = useCurrentUser();
  if (!user) return <p>signed out</p>;
  return (
    <p>
      {user.name} · {user.email} · {user.initials} · {user.roleDisplay ?? "no role"}
    </p>
  );
}

describe("useCurrentUser", () => {
  it("reads name, email, initials and the active org's role, by display name", () => {
    render(
      <Wrapper
        user={meWith({
          organizations: [
            {
              organization_id: "org-a",
              slug: "org-a",
              name: "Org A",
              roles: ["ops_manager"],
              role_names: ["Operations Manager"],
            },
          ],
        })}
        grants={[]}
        entitlements={[]}
      >
        <UserProbe />
      </Wrapper>,
    );
    expect(
      screen.getByText("Casey Rivera · casey@example.com · CR · Operations Manager"),
    ).toBeTruthy();
  });

  it("falls back to the role code when the server has no name for it", () => {
    render(
      <Wrapper
        user={meWith({
          organizations: [
            {
              organization_id: "org-a",
              slug: "org-a",
              name: "Org A",
              roles: ["ops_manager", "auditor"],
              role_names: ["Operations Manager"],
            },
          ],
        })}
        grants={[]}
        entitlements={[]}
      >
        <UserProbe />
      </Wrapper>,
    );
    expect(
      screen.getByText("Casey Rivera · casey@example.com · CR · Operations Manager, auditor"),
    ).toBeTruthy();
  });

  it("reports no role when the active org carries none", () => {
    render(
      <Wrapper user={meWith()} grants={[]} entitlements={[]}>
        <UserProbe />
      </Wrapper>,
    );
    expect(screen.getByText(/no role/)).toBeTruthy();
  });

  it("is null before sign-in", () => {
    render(
      <Wrapper user={null} grants={[]} entitlements={[]}>
        <UserProbe />
      </Wrapper>,
    );
    expect(screen.getByText("signed out")).toBeTruthy();
  });
});

function AdminProbe() {
  const links = useAdminLinks();
  return (
    <ul>
      {links.map((l) => (
        <li key={l.key}>{l.label}</li>
      ))}
    </ul>
  );
}

describe("useAdminLinks", () => {
  it("shows only the links the caller's grants cover", () => {
    render(
      <Wrapper user={meWith()} grants={["core.identity.membership.read:TEAM"]} entitlements={[]}>
        <AdminProbe />
      </Wrapper>,
    );
    expect(screen.getByText("Users")).toBeTruthy();
    expect(screen.queryByText("Settings")).toBeNull();
    expect(screen.queryByText("Audit Logs")).toBeNull();
  });

  it("shows all three when every underlying permission is held", () => {
    render(
      <Wrapper
        user={meWith()}
        grants={[
          "core.identity.membership.read:TEAM",
          "core.org.organization.read:ORG",
          "core.identity.audit.read:ORG",
        ]}
        entitlements={[]}
      >
        <AdminProbe />
      </Wrapper>,
    );
    expect(screen.getByText("Users")).toBeTruthy();
    expect(screen.getByText("Settings")).toBeTruthy();
    expect(screen.getByText("Audit Logs")).toBeTruthy();
  });

  it("hides Audit Logs below ORG scope, because every audit source refuses it", () => {
    render(
      <Wrapper user={meWith()} grants={["core.identity.audit.read:OWN"]} entitlements={[]}>
        <AdminProbe />
      </Wrapper>,
    );
    expect(screen.queryByText("Audit Logs")).toBeNull();
  });

  it("shows nothing when the caller holds none of the three", () => {
    render(
      <Wrapper user={meWith()} grants={[]} entitlements={[]}>
        <AdminProbe />
      </Wrapper>,
    );
    expect(screen.queryByText("Users")).toBeNull();
    expect(screen.queryByText("Settings")).toBeNull();
    expect(screen.queryByText("Audit Logs")).toBeNull();
  });
});
