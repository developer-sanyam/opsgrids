import type { ReactElement } from "react";
import { vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";
import { LocationProbe } from "./LocationProbe";

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

export interface TestRoute {
  path: string;
  element: ReactElement;
}

/**
 * Renders several routes as a signed-in user holding exactly `grants`, and hands back `where()` — the
 * current pathname and query string — so a test can read where a click took it. For flows that cross
 * screens, which `renderScreen`'s single route cannot follow.
 *
 * A `MemoryRouter`, deliberately, not a data router (`createMemoryRouter`): a data router builds a
 * `Request` for every navigation, and under Node 24 undici refuses the `AbortSignal` jsdom hands it,
 * so any test that clicks a link would fail there for a reason that has nothing to do with the screen.
 */
export function renderRoutes(routes: TestRoute[], at: string, grants: string[]) {
  const view = render(
    <AuthContext.Provider value={AUTH}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <MemoryRouter initialEntries={[at]}>
          <LocationProbe />
          <Routes>
            {routes.map((route) => (
              <Route key={route.path} path={route.path} element={route.element} />
            ))}
          </Routes>
        </MemoryRouter>
      </PermissionProvider>
    </AuthContext.Provider>,
  );
  function where(): { pathname: string; search: string } {
    const probe = view.container.querySelector("[data-testid='location']");
    return { pathname: probe?.getAttribute("data-pathname") ?? "", search: probe?.getAttribute("data-search") ?? "" };
  }
  return { where, ...view };
}
