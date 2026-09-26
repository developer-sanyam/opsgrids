import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as api from "./api";
import { DemandReport } from "./DemandReport";
import type { StoreOut } from "./types";

/**
 * All four async states of the demand report, including the empty one that has to explain itself.
 *
 * The report is a `DataTable` with `mobileCard="auto"`: below 768px it renders one card per row
 * instead of the table, chosen by a real media query rather than a CSS-hidden second copy, so
 * nothing is ever in the DOM (or the accessibility tree) twice. jsdom has no layout engine and
 * reports the wide table either way (see `useMediaQuery`'s own doc comment) — the narrow card
 * layout is `DataTable`'s own responsibility and covered by its tests, not repeated here.
 */

const PUBLISHED: StoreOut = {
  id: "store-test",
  slug: "greenleaf",
  name: "Greenleaf",
  tagline: null,
  logo_text: null,
  status: "published",
  currency: "INR",
  timezone: "Asia/Kolkata",
  custom_domain: null,
  custom_domain_status: null,
  created_at: "2026-09-01T00:00:00Z",
  updated_at: "2026-09-18T00:00:00Z",
};

const DRAFT: StoreOut = { ...PUBLISHED, status: "draft" };

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DemandReport", () => {
  it("lists the most-asked postal codes first, with what they were told", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({
      truncated: false,
      total: 3,
      rows: [
        { postal_code: "421201", outcome: "NO_MAPPING", checks: 34, last_checked_on: "2026-09-18" },
        { postal_code: "400703", outcome: "NO_MAPPING", checks: 21, last_checked_on: "2026-09-19" },
        { postal_code: "400050", outcome: "SERVED", checks: 288, last_checked_on: "2026-09-19" },
      ],
    });

    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByRole("table"));
    const table = screen.getByRole("table");
    const codes = within(table)
      .getAllByRole("cell")
      .map((cell) => cell.textContent);
    // 421201 (34) before 400703 (21), and the served one filtered out of the default view.
    expect(codes.indexOf("421201")).toBeLessThan(codes.indexOf("400703"));
    expect(codes).not.toContain("400050");
  });

  it("shows served checks too once the filter is switched", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({
      truncated: false,
      total: 3,
      rows: [
        { postal_code: "400050", outcome: "SERVED", checks: 288, last_checked_on: "2026-09-19" },
      ],
    });
    const user = userEvent.setup();
    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByText("Nobody has been turned away in this range"));
    await user.click(screen.getByRole("button", { name: "Every check" }));

    const table = await screen.findByRole("table");
    expect(within(table).getByText("400050")).toBeTruthy();
  });

  it("names the code, the outcome and the count in one row", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({
      truncated: false,
      total: 3,
      rows: [
        { postal_code: "421201", outcome: "NO_MAPPING", checks: 34, last_checked_on: "2026-09-18" },
      ],
    });
    render(<DemandReport store={PUBLISHED} />);

    const table = await screen.findByRole("table");
    expect(within(table).getByText("421201")).toBeTruthy();
    expect(within(table).getByText("Not mapped")).toBeTruthy();
    expect(within(table).getByText("34")).toBeTruthy();
  });

  it("explains *why* it is empty rather than saying 'no data'", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({ rows: [], truncated: false, total: 0 });
    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByText("Nobody has been turned away in this range"));
    expect(screen.getByText(/Switch to “Every check”/)).toBeTruthy();
  });

  it("names the draft store as the reason when that is the reason", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({ rows: [], truncated: false, total: 0 });
    render(<DemandReport store={DRAFT} />);

    await waitFor(() => screen.getByText(/answers 404 to every buyer/));
  });

  it("renders a 403 as a permission fact with no retry button", async () => {
    vi.spyOn(api, "getDemand").mockRejectedValue(new ApiError(403, "forbidden", null));
    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByText("You can't see delivery zones"));
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("offers a retry on a 5xx, and retrying the same range re-runs the request", async () => {
    const spy = vi
      .spyOn(api, "getDemand")
      .mockRejectedValueOnce(new ApiError(503, "upstream unavailable", null))
      .mockResolvedValue({ rows: [], truncated: false, total: 0 });
    const user = userEvent.setup();
    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByRole("button", { name: "Try again" }));
    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
  });
});
