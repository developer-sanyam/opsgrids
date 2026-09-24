import { render, screen } from "@testing-library/react";
import { createMemoryRouter, Outlet, RouterProvider } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { RouteErrorCard } from "./RouteError";

/**
 * This is the production bug's actual failure mode, reduced to its shape: a route's own element
 * throws while rendering. Before `main.tsx` grew an `errorElement` on every route, that bubbled to
 * React Router's default boundary and blanked the whole app — Safari's "Unexpected Application
 * Error! undefined is not an object" from the incident report. With `errorElement` wired per route,
 * the same throw is contained to that route's slot: the shell around it (the sidebar, in the real
 * app) keeps rendering, and the content area shows a card instead of a blank page.
 */
function Boom(): never {
  throw new Error("undefined is not an object (evaluating 'l')");
}

describe("RouteErrorCard", () => {
  it("contains a thrown render error in its own route slot, leaving the parent layout intact", () => {
    // React logs the caught error to the console as part of normal error-boundary behaviour; this
    // suite asserts on the rendered result, not the log, so it is silenced rather than asserted on.
    vi.spyOn(console, "error").mockImplementation(() => {});

    const router = createMemoryRouter(
      [
        {
          path: "/",
          element: (
            <div>
              <nav>Shell navigation</nav>
              <Outlet />
            </div>
          ),
          children: [{ index: true, element: <Boom />, errorElement: <RouteErrorCard /> }],
        },
      ],
      { initialEntries: ["/"] },
    );

    render(<RouterProvider router={router} />);

    // The parent layout survives the child's throw...
    expect(screen.getByText("Shell navigation")).toBeTruthy();
    // ...and the content slot shows a contained error card, not a blank page.
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText("This page couldn't load")).toBeTruthy();
    expect(screen.getByText("undefined is not an object (evaluating 'l')")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
  });
});
