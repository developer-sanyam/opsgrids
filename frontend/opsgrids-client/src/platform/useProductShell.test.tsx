import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { configureAuthOrigin } from "../auth/origin";
import { useProductShell } from "./useProductShell";

const logout = vi.fn();
const reloadGrants = vi.fn();

vi.mock("../auth/context", () => ({ useAuth: () => ({ logout }) }));
vi.mock("../permissions/context", () => ({ usePermissions: () => ({ status: "error", reloadGrants }) }));
vi.mock("../notifications/NotificationsBell", () => ({ NotificationsBell: () => null }));
vi.mock("./useCurrentUser", () => ({
  useCurrentUser: () => ({ name: "Sofia", email: "s@acme.example", initials: "S", roleDisplay: "Admin" }),
}));
vi.mock("./usePlatformModules", () => ({
  usePlatformModules: () => [{ key: "finance", label: "Finance", href: "https://finance.example", iconKey: "wallet" }],
}));
vi.mock("./useAdminLinks", () => ({
  useAdminLinks: () => [{ key: "users", label: "Users", href: "/admin/members" }],
}));

beforeEach(() => {
  logout.mockReset();
  reloadGrants.mockReset();
});

describe("useProductShell", () => {
  it("shapes the signed-in user, modules and grants for the shell", () => {
    const { result } = renderHook(() => useProductShell());
    expect(result.current.user).toEqual({ name: "Sofia", email: "s@acme.example", roleLabel: "Admin" });
    expect(result.current.modules).toEqual([
      { key: "finance", label: "Finance", href: "https://finance.example", iconKey: "wallet" },
    ]);
    expect(result.current.grants.status).toBe("error");
    result.current.grants.reload();
    expect(reloadGrants).toHaveBeenCalledTimes(1);
  });

  it("signs out through the session", () => {
    const { result } = renderHook(() => useProductShell());
    result.current.onSignOut();
    expect(logout).toHaveBeenCalledTimes(1);
  });

  it("points admin links at the dashboard, the production one when none is configured", () => {
    const { result } = renderHook(() => useProductShell());
    expect(result.current.adminLinks).toEqual([
      { key: "users", label: "Users", href: "https://app.opsgrids.com/admin/members" },
    ]);
  });

  it("points admin links at the configured auth origin", () => {
    configureAuthOrigin("https://auth.example.com");
    const { result } = renderHook(() => useProductShell());
    expect(result.current.adminLinks[0]?.href).toBe("https://auth.example.com/admin/members");
    configureAuthOrigin("");
  });

  it("includes the bell unless the app leaves it out", () => {
    expect(renderHook(() => useProductShell()).result.current.notifications).toBeTruthy();
    expect(renderHook(() => useProductShell({ notifications: false })).result.current.notifications).toBeUndefined();
  });
});
