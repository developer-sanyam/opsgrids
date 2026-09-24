import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { PlansPage } from "./Plans";

/** There is no live catalog-browsing endpoint (`services/catalog/app/api/admin.py` only exposes
 *  writes) — this page makes no API call at all and says so plainly instead of 404ing. */
describe("PlansPage", () => {
  it("says catalog browsing isn't available, and points to the real per-account pricing flow", () => {
    render(
      <MemoryRouter>
        <PlansPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Catalog browsing isn't available from the console yet")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Go to accounts" })).toBeTruthy();
  });
});
