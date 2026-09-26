/**
 * `AssetDetailPage`: the book value is shown in the currency's own decimals. A yen asset imported
 * or entered as 150000 is ¥1,50,000, and a dinar asset of 1250 minor units is 1.250, not 12.50.
 */
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Asset } from "@/api/types";
import { AssetDetailPage } from "./AssetDetail";

function asset(over: Partial<Asset>): Asset {
  return {
    tag: "LT-2041",
    name: "Dell Latitude 7440",
    type: "laptop",
    status: "in_use",
    owner: "Anita Desai",
    location: "Bengaluru HQ",
    serial: "8H2K3Z3",
    purchased_at: "2025-08-14",
    warranty_expires_at: "2099-08-13",
    cost_minor: 8_450_000,
    cost_currency: "INR",
    open_incident_ids: [],
    ...over,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

async function bookValueOf(shown: Asset): Promise<string> {
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(shown), { status: 200, headers: { "content-type": "application/json" } })),
  );
  render(
    <MemoryRouter initialEntries={[`/assets/${shown.tag}`]}>
      <Routes>
        <Route path="/assets/:tag" element={<AssetDetailPage />} />
      </Routes>
    </MemoryRouter>,
  );
  await screen.findByText(shown.name);
  const label = screen.getByText("Book value");
  return label.nextElementSibling?.textContent ?? "";
}

describe("AssetDetailPage: book value", () => {
  it.each([
    ["INR", 8_450_000, "₹84,500"],
    ["INR", 2950, "₹29.50"],
    ["USD", 240_000_000, "$24,00,000"],
    ["JPY", 150_000, "JP¥1,50,000"],
    ["KWD", 1250, "KWD 1.250"],
  ])("shows %s %i minor units as %s", async (currency, minor, shown) => {
    expect(await bookValueOf(asset({ cost_minor: minor, cost_currency: currency }))).toBe(shown);
  });
});
