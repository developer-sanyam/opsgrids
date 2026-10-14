import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { NotFoundPage } from "./NotFound";

describe("NotFoundPage", () => {
  it("offers the way back with a 44px touch target", () => {
    render(
      <MemoryRouter>
        <NotFoundPage />
      </MemoryRouter>,
    );
    const link = screen.getByRole("link", { name: "Back to the overview" });
    expect(link.getAttribute("href")).toBe("/");
    // `hit-44` extends the hit area to 44px without changing the 36px look; it needs `relative`.
    expect(link.classList.contains("hit-44")).toBe(true);
    expect(link.classList.contains("relative")).toBe(true);
  });
});
