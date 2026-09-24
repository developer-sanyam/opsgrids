/**
 * Rendering the console for a test — imported by suites and nothing else.
 *
 * Screens are rendered the way the app mounts them: inside the theme and toast providers, under a
 * router, with the operator session supplied as a fixture identity so no gateway is involved and
 * a test can choose which roles it holds. Data comes from the same fixtures the console renders in
 * development, so a screen's test reads the way a person would use it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { Outlet, RouterProvider, createMemoryRouter } from "react-router-dom";
import type { RouteObject } from "react-router-dom";

import { ThemeProvider, Toaster } from "@opsgrids/ui";

import { OperatorAuthProvider } from "@/auth/OperatorAuthProvider";
import type { OperatorMe } from "@/auth/types";
import { routes } from "@/router";

export function operatorWith(roles: string[]): OperatorMe {
  return {
    staff_id: "8f21a0c4-5b1e-4d3a-9c77-2e6a4b0f1d10",
    email: "priya.venkatesan@opsgrids.com",
    display_name: "Priya Venkatesan",
    operator_roles: roles,
    amr: ["pwd", "otp"],
  };
}

export const ALL_ROLES = ["owner", "billing_ops", "support"];

/**
 * The one alert on screen that says something. The toast layer keeps two permanent, empty live
 * regions (`role="alert"` for errors, `role="status"` for the rest) so that a message announces
 * reliably when it arrives; `getByRole("alert")` would find those too and refuse to pick one.
 */
export async function findAlert(): Promise<HTMLElement> {
  return waitFor(() => {
    const saying = screen.getAllByRole("alert").filter((el) => (el.textContent ?? "").trim() !== "");
    if (saying.length !== 1) throw new Error(`Expected one alert with text, found ${saying.length}`);
    const [only] = saying;
    if (only === undefined) throw new Error("unreachable");
    return only;
  });
}

/** The app's own route tree, with the operator session replaced by a fixture holding `roles`. */
function routesFor(operator: OperatorMe): RouteObject[] {
  const root = routes[0];
  return [
    {
      element: (
        <OperatorAuthProvider fixtureOperator={operator}>
          <Outlet />
        </OperatorAuthProvider>
      ),
      children: root?.children ?? [],
    },
  ];
}

/** Mount the whole console at `path`, as an operator holding `roles`. */
export function renderConsole(path = "/", roles: string[] = ALL_ROLES) {
  const router = createMemoryRouter(routesFor(operatorWith(roles)), { initialEntries: [path] });
  const utils = render(
    <ThemeProvider storageKey="opsgrids.operator.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>,
  );
  return { ...utils, router };
}
