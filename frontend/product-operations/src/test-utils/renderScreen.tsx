import type { ReactElement } from "react";
import { vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

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

interface RouteOptions {
  /** The route pattern the screen is mounted on (`/inventory/rolls/:id`), for screens that read params. */
  path: string;
  /** The address to start at (`/inventory/rolls/roll-1`). */
  at: string;
}

/** Renders a screen as a signed-in user holding exactly `grants`, inside a router. Pass `route`
 *  for a screen that reads URL params or the query string. */
export function renderScreen(page: ReactElement, grants: string[], route?: RouteOptions) {
  return render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={grants} entitlements={[]}>
        {route ? (
          <MemoryRouter initialEntries={[route.at]}>
            <Routes>
              <Route path={route.path} element={page} />
            </Routes>
          </MemoryRouter>
        ) : (
          <MemoryRouter>{page}</MemoryRouter>
        )}
      </PermissionProvider>
    </AuthContext.Provider>,
  );
}
