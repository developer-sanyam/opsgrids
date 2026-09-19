import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as api from "./api";
import { ServiceabilityTester } from "./ServiceabilityTester";
import type { MerchantServiceabilityOut } from "./types";

/**
 * **The most important behaviour in this module, and the one a naive implementation gets wrong by
 * default.**
 *
 * ADR-0010 §13: *a check that fails renders as "we couldn't check", never as "we don't deliver
 * here". Unknown is not no.* A `catch → not serviceable` is one line and passes every happy-path
 * test; these are the tests that refuse it. A merchant who reads a 503 as "my own store won't
 * deliver to my own warehouse" files a bug that costs a day.
 */

const SERVED: MerchantServiceabilityOut = {
  postal_code: "400050",
  serviceable: true,
  mode: "PROMISED",
  reason: null,
  promise: { kind: "DURATION", minutes: 30, date: null },
  fees: {
    currency: "INR",
    delivery_fee_minor: 2900,
    free_delivery_above_minor: 49900,
    min_order_minor: 19900,
    display_only: true,
  },
  buyer_note: null,
  checked_at: "2026-09-19T05:00:00Z",
  zone_id: "zone-central",
  zone_code: "MUM-CENTRAL",
  zone_name: "Mumbai — central",
};

afterEach(() => {
  vi.restoreAllMocks();
});

async function check(code: string) {
  const user = userEvent.setup();
  render(<ServiceabilityTester />);
  await user.type(screen.getByLabelText("Postal code"), code);
  await user.click(screen.getByRole("button", { name: "Check" }));
}

describe("ServiceabilityTester", () => {
  it("starts empty, with an explanation rather than a blank card", () => {
    render(<ServiceabilityTester />);
    expect(screen.getByText("No check run yet")).toBeTruthy();
  });

  it("renders the server's answer and never computes one of its own", async () => {
    const spy = vi.spyOn(api, "checkServiceability").mockResolvedValue(SERVED);
    await check("400050");

    await waitFor(() => screen.getByText("Yes — you deliver here"));
    expect(spy).toHaveBeenCalledWith("400050");
    expect(screen.getByText(/in about 30 minutes/)).toBeTruthy();
    // The money caveat travels with the money (ADR-0010 §8).
    expect(screen.getByText("Shown to buyers — not charged")).toBeTruthy();
  });

  it("renders a 5xx as 'couldn't check', not as 'not serviceable'", async () => {
    vi.spyOn(api, "checkServiceability").mockRejectedValue(
      new ApiError(503, "upstream unavailable", { request_id: "7f3c1a2e" }),
    );
    await check("400050");

    await waitFor(() => screen.getByText("Couldn't check 400050"));
    expect(screen.queryByText("No — you don't deliver here")).toBeNull();
    expect(screen.queryByText("Yes — you deliver here")).toBeNull();
    expect(screen.getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByText(/Reference: 7f3c1a2e/)).toBeTruthy();
  });

  it("renders a dead network the same way", async () => {
    vi.spyOn(api, "checkServiceability").mockRejectedValue(new TypeError("Failed to fetch"));
    await check("400050");

    await waitFor(() => screen.getByText("Couldn't check 400050"));
    expect(screen.queryByText("No — you don't deliver here")).toBeNull();
  });

  it("renders a rate limit as 'couldn't check' too", async () => {
    vi.spyOn(api, "checkServiceability").mockRejectedValue(new ApiError(429, "slow down", null));
    await check("400050");

    await waitFor(() => screen.getByText("Couldn't check 400050"));
    expect(screen.getByText(/Wait a few seconds/)).toBeTruthy();
  });

  it("renders a genuine UNSERVED as a real no, with the reason", async () => {
    vi.spyOn(api, "checkServiceability").mockResolvedValue({
      ...SERVED,
      postal_code: "422001",
      serviceable: false,
      mode: "UNSERVED",
      reason: "no_mapping",
      promise: null,
      fees: null,
      zone_id: null,
      zone_code: null,
      zone_name: null,
    });
    await check("422001");

    await waitFor(() => screen.getByText("No — you don't deliver here"));
    expect(screen.getByText("Not mapped to any zone yet")).toBeTruthy();
  });

  it("treats a 403 as a permission fact, not as an unknown check", async () => {
    vi.spyOn(api, "checkServiceability").mockRejectedValue(new ApiError(403, "forbidden", null));
    await check("400050");

    await waitFor(() => screen.getByText("That check wasn't allowed"));
    expect(screen.queryByText("Couldn't check 400050")).toBeNull();
  });

  it("validates the box before spending a round trip, and says the rule", async () => {
    const spy = vi.spyOn(api, "checkServiceability");
    await check("4000");

    expect(await screen.findByText("Indian PIN codes are six digits, like 400050.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
