/**
 * Leaving a full-page form inside the app is a route change, which the browser's own prompt never
 * sees. The guard holds it and asks — and lets it through untouched when there is nothing to lose.
 */

import { useState } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, RouterProvider, createMemoryRouter } from "react-router-dom";

import { UnsavedChangesGuard } from "./UnsavedChangesGuard";

function EditPage() {
  const [name, setName] = useState("");
  return (
    <main>
      <h1>Edit purchase order</h1>
      <label>
        Supplier
        <input value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      <Link to="/orders">All orders</Link>
      <UnsavedChangesGuard when={name !== ""} subject="the purchase order" />
    </main>
  );
}

function renderApp() {
  const router = createMemoryRouter(
    [
      { path: "/orders/new", element: <EditPage /> },
      { path: "/orders", element: <h1>Purchase orders</h1> },
    ],
    { initialEntries: ["/orders/new"] },
  );
  render(<RouterProvider router={router} />);
  return router;
}

describe("UnsavedChangesGuard", () => {
  it("lets navigation through when nothing has changed", async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole("link", { name: "All orders" }));
    expect(screen.getByRole("heading", { name: "Purchase orders" })).toBeTruthy();
  });

  it("holds a route change while there are changes, and Stay keeps the page and the typing", async () => {
    const user = userEvent.setup();
    const router = renderApp();
    await user.type(screen.getByLabelText("Supplier"), "Kaveri Textiles");
    await user.click(screen.getByRole("link", { name: "All orders" }));

    expect(screen.getByRole("dialog", { name: "Leave without saving?" }).textContent).toContain("the purchase order");
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Stay on page" }));
    await user.click(screen.getByRole("button", { name: "Stay on page" }));

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(router.state.location.pathname).toBe("/orders/new");
    expect(screen.getByLabelText("Supplier")).toHaveProperty("value", "Kaveri Textiles");
  });

  it("goes where the user was going on Leave and discard", async () => {
    const user = userEvent.setup();
    const router = renderApp();
    await user.type(screen.getByLabelText("Supplier"), "K");
    await user.click(screen.getByRole("link", { name: "All orders" }));
    await user.click(screen.getByRole("button", { name: "Leave and discard" }));
    expect(router.state.location.pathname).toBe("/orders");
    expect(screen.getByRole("heading", { name: "Purchase orders" })).toBeTruthy();
  });
});
