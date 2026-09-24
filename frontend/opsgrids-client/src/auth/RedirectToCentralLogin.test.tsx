import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { RedirectToCentralLogin } from "./RedirectToCentralLogin";

describe("RedirectToCentralLogin", () => {
  const realLocation = window.location;
  let assign: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...realLocation, href: "https://people.opsgrids.com/employees/42" },
    });
    // `assign` is not configurable on the real Location either — same shape as the dashboard's
    // own Login.test.tsx workaround.
    (window.location as unknown as { assign: typeof assign }).assign = assign;
  });

  afterEach(() => {
    Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  });

  it("sends the browser to the dashboard's login with this app's own URL as next", () => {
    render(<RedirectToCentralLogin authOrigin="https://app.opsgrids.com" logo={<span />} />);

    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" +
        encodeURIComponent("https://people.opsgrids.com/employees/42"),
    );
  });

  it("shows the product's mark and a status message while it navigates away", () => {
    render(
      <RedirectToCentralLogin
        authOrigin="https://app.opsgrids.com"
        logo={<span data-testid="mark" />}
        message="Hold on…"
      />,
    );

    expect(screen.getByTestId("mark")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toBe("Hold on…");
  });
});
