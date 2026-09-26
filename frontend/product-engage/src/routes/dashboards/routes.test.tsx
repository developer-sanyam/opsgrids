import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import * as analytics from "@/api/analytics";
import * as client from "@/api/client";
import { CAMPAIGN_SENT } from "@/api/fixtures";
import { routes } from "./index";
import { CAMPAIGN, EMAIL, HUB, WHATSAPP } from "./testData";

/** The list the orchestrator wires: the contract's paths, each one rendering its own screen. */

describe("the dashboard routes", () => {
  it("are exactly the contract's paths, each named once", () => {
    expect(routes.map((route) => route.path)).toEqual([
      "/",
      "/whatsapp",
      "/whatsapp/analytics",
      "/email",
      "/email/campaigns/:id/analytics",
    ]);
    expect(new Set(routes.map((route) => route.title)).size).toBe(routes.length);
  });

  it("each render their screen at their own path", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, "getCampaign").mockResolvedValue(CAMPAIGN_SENT);

    const headings: Record<string, string> = {
      "/": "Communication",
      "/whatsapp": "WhatsApp",
      "/whatsapp/analytics": "WhatsApp analytics",
      "/email": "Email",
      "/email/campaigns/c-1/analytics": CAMPAIGN_SENT.name,
    };

    for (const [url, heading] of Object.entries(headings)) {
      const view = render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            {routes.map((route) => (
              <Route key={route.path} path={route.path} element={route.element} />
            ))}
          </Routes>
        </MemoryRouter>,
      );
      expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeTruthy();
      view.unmount();
    }
  });
});
