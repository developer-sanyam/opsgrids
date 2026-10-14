/**
 * The phone fixes in this app, one behaviour each: Reports is not offered until it exists, an
 * open-ended campaign says "No end date", the campaign actions are one overflow menu, and
 * selecting campaigns is a mode on a phone rather than a tick box on every card.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Audience, Campaign, Page } from "@/api/types";
import { CampaignDetailPage } from "@/routes/CampaignDetail";
import { CampaignsPage } from "@/routes/Campaigns";
import { useNavGroups } from "@/ui/useNavGroups";

const CAMPAIGN: Campaign = {
  id: "cmp-1",
  name: "Diwali Enterprise Push",
  channel: "search",
  status: "live",
  currency: "INR",
  budget_minor: 3200000,
  spent_minor: 2840000,
  revenue_minor: 14200000,
  pipeline_minor: 32000000,
  leads: 640,
  conversions: 34,
  goal: "Enterprise pipeline",
  audience_id: "aud-1",
  start_date: "2026-06-15",
  end_date: null,
  owner: "Meghna Rao",
};

const AUDIENCE: Audience = {
  id: "aud-1",
  name: "Enterprise India",
  size: 4210,
  criteria: "1000+ employees",
  created_at: "2026-01-01",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: 1 };
}

function stubPhone(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function NavProbe() {
  const { groups } = useNavGroups();
  return (
    <ul>
      {groups
        .flatMap((group) => group.items)
        .map((entry) => (
          <li key={entry.to}>{entry.label}</li>
        ))}
    </ul>
  );
}

describe("navigation", () => {
  it("does not offer Reports, which is a placeholder, and still offers what exists", async () => {
    vi.spyOn(client, "getCampaigns").mockResolvedValue(page([CAMPAIGN]));
    render(<NavProbe />);
    await waitFor(() => expect(screen.getByText("Campaigns")).toBeTruthy());
    expect(screen.queryByText("Reports")).toBeNull();
    expect(screen.getByText("Audiences")).toBeTruthy();
  });
});

describe("CampaignDetail", () => {
  function open() {
    vi.spyOn(client, "getCampaign").mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, "getAudiences").mockResolvedValue(page([AUDIENCE]));
    vi.spyOn(client, "getCampaignWeekly").mockResolvedValue([]);
    return render(
      <MemoryRouter initialEntries={["/campaigns/cmp-1"]}>
        <PermissionProvider grants={["marketing.analytics.campaign.write:ORG"]}>
          <Routes>
            <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
          </Routes>
        </PermissionProvider>
      </MemoryRouter>,
    );
  }

  it("says there is no end date rather than printing a dash after a dash", async () => {
    open();
    await screen.findByRole("heading", { name: "Diwali Enterprise Push" });
    expect(screen.getByText(/No end date/)).toBeTruthy();
    expect(screen.queryByText(/– —/)).toBeNull();
  });

  it("keeps Delete in an overflow menu, away from the everyday actions", async () => {
    const user = userEvent.setup();
    open();
    await screen.findByRole("heading", { name: "Diwali Enterprise Push" });
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();

    await user.click(screen.getByRole("button", { name: "More actions for Diwali Enterprise Push" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete" }));
    expect(await screen.findByRole("dialog", { name: "Delete this campaign?" })).toBeTruthy();
  });
});

describe("Campaigns on a phone", () => {
  function open() {
    vi.spyOn(client, "getCampaigns").mockResolvedValue(page([CAMPAIGN]));
    return render(
      <MemoryRouter initialEntries={["/campaigns"]}>
        <PermissionProvider grants={["marketing.analytics.campaign.write:ORG"]}>
          <Routes>
            <Route path="/campaigns" element={<CampaignsPage />} />
          </Routes>
        </PermissionProvider>
      </MemoryRouter>,
    );
  }

  it("has one actions control per card instead of View, Edit and Delete text buttons", async () => {
    stubPhone();
    open();
    await screen.findByText("Diwali Enterprise Push");
    expect(screen.getByRole("button", { name: "More actions for Diwali Enterprise Push" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Delete Diwali Enterprise Push" })).toBeNull();
  });

  it("shows no tick boxes until Select campaigns is turned on, and clears the choice when it is turned off", async () => {
    stubPhone();
    const user = userEvent.setup();
    open();
    await screen.findByText("Diwali Enterprise Push");
    expect(screen.queryByRole("checkbox")).toBeNull();

    const toggle = screen.getByRole("button", { name: "Select campaigns" });
    await user.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    const box = screen.getByRole("checkbox", { name: "Select Diwali Enterprise Push" });
    await user.click(box);
    expect((box as HTMLInputElement).checked).toBe(true);

    await user.click(within(document.body).getByRole("button", { name: "Done selecting" }));
    expect(screen.queryByRole("checkbox")).toBeNull();
  });

  it("keeps the tick boxes on a wide screen, where they are not clutter", async () => {
    open();
    await screen.findByText("Diwali Enterprise Push");
    expect(screen.getAllByRole("checkbox").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Select campaigns" })).toBeNull();
  });
});
