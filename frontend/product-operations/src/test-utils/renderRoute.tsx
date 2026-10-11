import { vi } from "vitest";
import { render } from "@testing-library/react";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import type { RouteObject } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: {
    user_id: "user-1",
    email: "ops@example.com",
    full_name: "Asha Rao",
    organization_id: "org-1",
    membership_id: "mem-1",
    roles: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [],
  },
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

/**
 * Renders `routes` at `initialEntry` as a signed-in user holding exactly `grants`
 * (`"operations.procurement.receipt.write:ORG"`), and hands back the router so a test can read
 * where a flow navigated to.
 */
export function renderRoute(routes: RouteObject[], initialEntry: string, grants: string[]) {
  const router = createMemoryRouter(routes, { initialEntries: [initialEntry] });
  render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <RouterProvider router={router} />
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  return router;
}
