/**
 * A route that throws while rendering must cost only that route: the router puts `RouteError` in its
 * place, inside the same layout, with a way out. (Following the way out is a navigation, which a
 * data router builds a `Request` for; under Node 24 jsdom's `AbortSignal` is refused there, so the
 * click itself is not exercised.)
 */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";

import { RouteError } from "./RouteError";

function Boom(): never {
  throw new Error("a response this screen cannot draw");
}

describe("RouteError", () => {
  it("replaces only the failing route, leaves the layout around it, and offers a way out", async () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: (
            <div>
              <nav aria-label="Shell">the shell</nav>
              <Outlet />
            </div>
          ),
          children: [{ path: "warehouses/map", element: <Boom />, errorElement: <RouteError /> }],
        },
      ],
      { initialEntries: ["/warehouses/map"] },
    );
    render(<RouterProvider router={router} />);

    expect(await screen.findByText("This screen couldn't be shown")).toBeDefined();
    expect(screen.getByRole("navigation", { name: "Shell" })).toBeDefined();
    expect(screen.getByRole("button", { name: "Go to warehouses" })).toBeDefined();
  });
});
