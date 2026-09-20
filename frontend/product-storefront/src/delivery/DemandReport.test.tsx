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
 * **Queries here are scoped to the `<table>` on purpose.** The report renders two layouts — the
 * table from `sm` up, a stacked list below it — and jsdom applies no media queries, so both are in
 * the DOM at once and a bare `getByText` finds each row twice. In a browser exactly one is
 * `display: none`, and a `display: none` subtree is not in the accessibility tree either, so no
 * screen reader hears it twice. The last test below pins that the two carry the same facts.
 */

const PUBLISHED: StoreOut = {
  id: "store-test",
  slug: "greenleaf",
  status: "published",
  currency: "INR",
  timezone: "Asia/Kolkata",
  custom_domain: null,
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

  it("says the same thing in the narrow layout as in the table", async () => {
    vi.spyOn(api, "getDemand").mockResolvedValue({
      truncated: false,
      total: 3,
      rows: [
        { postal_code: "421201", outcome: "NO_MAPPING", checks: 34, last_checked_on: "2026-09-18" },
      ],
    });
    render(<DemandReport store={PUBLISHED} />);

    await waitFor(() => screen.getByRole("table"));
    // One in the table, one in the list. Both name the code, the outcome and the count; the
    // browser shows exactly one.
    expect(screen.getAllByText("421201")).toHaveLength(2);
    expect(screen.getAllByText("Not mapped")).toHaveLength(2);
    expect(screen.getAllByText("34")).toHaveLength(2);
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
