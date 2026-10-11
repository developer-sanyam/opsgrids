import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { NotFoundPage } from "./NotFound";

/**
 * The route table's catch-all. Nothing loads, so there is no loading, empty, error or forbidden
 * state to pin: what matters is that a person who followed a dead link is told so and offered the
 * way back, in the words the Overview link uses.
 */

describe("NotFoundPage", () => {
  it("says the page does not exist and offers the way back to the overview", () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    expect(screen.getByText("Not found")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "That page doesn't exist" })).toBeTruthy();
    expect(screen.getByText("Check the address, or head back to the overview.")).toBeTruthy();
    const back = screen.getByRole("link", { name: "Back to overview" });
    expect(back.getAttribute("href")).toBe("/");
  });
});
